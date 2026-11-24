"""verify, decide, handoff and respond nodes (BS-18, LLD-11)."""

import time
from typing import Any

from app.agents.rag_answerer import answer, parse_draft
from app.config.settings import get_settings
from app.obs.tracing import span_set
from app.orchestrator.nodes.rules import safety_handoff as _safety
from app.orchestrator.nodes.stubs import handoff_or_review as _handoff
from app.orchestrator.state import Draft, HandoffDecision, TurnState, Verification
from app.providers import get_llm
from app.verify import confidence as conf
from app.verify import consistency, coverage
from app.verify.judge import Judgement, judge
from app.verify.summary import plain, summarize

Update = dict[str, Any]
MIN_COVERAGE = 0.8
REGEN_MIN_S = 1.2            # not worth starting a regeneration with less time than this
CONNECTING = "I'm connecting you with our service team. They'll pick this up with the details you've shared."
UNSURE = "I'm not certain enough to answer that reliably, so I'm connecting you with our service team."


def _left(state: TurnState) -> float:
    return state["deadline"] - time.monotonic()


def _problems(text: str, state: TurnState, draft: Draft) -> tuple[coverage.Coverage, list[str]]:
    return coverage.measure(text), consistency.check(text, state.get("evidence") or [], question=state["env"].text,
                                                     actions=draft.actions)


async def verify(state: TurnState) -> Update:
    draft = state.get("draft")
    if draft is None or draft.abstained or state.get("answered_tier") == "T1" or not state.get("evidence"):
        # nothing to check: abstentions, curated FAQ answers and scripted prompts state no new facts
        return {"verification": Verification(passed=True, coverage=None)}
    mode = state["policy"].verification.rules
    if mode == "off":
        return {"verification": Verification(passed=True, coverage=None)}
    evidence = state["evidence"]
    cov, issues = _problems(draft.text, state, draft)
    if mode == "async":                          # recorded for the audit, doesn't hold the answer back
        span_set(coverage=cov.score, issues=issues)
        return {"verification": Verification(passed=True, coverage=round(cov.score, 3), issues=issues)}
    regenerated = False
    if draft.generated and (issues or cov.score < MIN_COVERAGE) and _left(state) > REGEN_MIN_S:
        s = get_settings()
        notes = [*issues, *(f"no citation: {x}" for x in cov.uncovered)]
        try:
            res = await answer(state["env"].text, evidence, get_llm(), model=s.model_fast,
                               timeout_ms=int((_left(state) - 0.3) * 1000), feedback=notes)
            regenerated = True
            draft = res.draft.model_copy(update={"actions": draft.actions, "form": draft.form})
            cov, issues = _problems(draft.text, state, draft)
        except Exception as exc:  # noqa: BLE001 - keep the first draft; it is still checked below
            span_set(regenerate_error=type(exc).__name__)
    out: Update = {}
    raw = cov.score
    if draft.generated and not draft.abstained and cov.uncovered:
        kept = parse_draft(coverage.drop_uncovered(draft.text), evidence)
        draft = kept.model_copy(update={"actions": draft.actions, "form": draft.form})
        _, issues = _problems(draft.text, state, draft) if not draft.abstained else (cov, [])
    verdict: Judgement | None = None
    policy = state["policy"]
    if draft.generated and not draft.abstained and policy.verification.judge == "sync" and _left(state) > 0.5:
        try:
            verdict = await judge(state["env"].text, draft.text, evidence, get_llm(), model=get_settings().model_strong,
                                  timeout_ms=int(min(policy.timeout_ms("judge") / 1000, _left(state)) * 900))
        except Exception as exc:  # noqa: BLE001 - no judge: the proxy is used, capped in decide
            span_set(judge_error=type(exc).__name__)
    v = Verification(passed=not issues, coverage=round(raw, 3), issues=issues,
                     judge=verdict.faithfulness if verdict else None)
    span_set(coverage=v.coverage, uncovered=len(cov.uncovered), issues=issues, regenerated=regenerated,
             judge=verdict.model_dump() if verdict else None)
    return {**out, "draft": draft, "verification": v}


async def decide(state: TurnState) -> Update:
    draft, tier = state.get("draft"), state.get("answered_tier")
    v = state.get("verification") or Verification(passed=True)
    evidence = state.get("evidence") or []
    if draft is not None and draft.abstained:
        parts = conf.score(router=conf.router_conf(state), retrieval=0.0, coverage=0.0, judge=0.0)
    elif tier in ("T0", "T1") or not evidence:
        parts = {"confidence": 1.0, "deterministic": 1.0}        # tool/template/curated text, no free text
    else:
        j = v.judge
        if j is None and draft is not None and not draft.generated:
            j = 1.0                                               # composed from graph rows, nothing to judge
        parts = conf.score(router=conf.router_conf(state), retrieval=conf.retrieval_norm(evidence),
                           coverage=v.coverage if v.coverage is not None else 1.0, judge=j)
    policy = state["policy"]
    if state["env"].channel == "email" and draft is not None and draft.generated and v.judge is None and not draft.abstained \
            and policy.verification.judge == "sync":
        # the channel requires a judged answer; without one it can't be sent unattended
        parts["confidence"] = min(parts["confidence"], policy.thresholds["confidence_answer"] - 0.01)
        parts["judge_missing"] = 1.0
    c = parts["confidence"]
    out: Update = {"confidence": c, "confidence_parts": parts}
    auto = policy.thresholds.get("auto_send")
    h = state.get("handoff") or conf.overrides(state)
    if h is None and c < policy.thresholds["confidence_answer"]:
        h = HandoffDecision(action=policy.on_low_confidence, reason=f"low confidence {c:.2f}")
    if h is None and auto is not None and c < auto:
        h = HandoffDecision(action="draft_for_review", reason=f"below auto-send threshold {auto}")
    out["auto_send"] = auto is not None and h is None
    span_set(**parts, handoff=h.reason if h else None, auto_send=out["auto_send"])
    if h is not None:
        out["handoff"] = h
        unsafe = not v.passed or (draft is not None and draft.generated and not draft.abstained
                                  and c < policy.thresholds["confidence_answer"])
        if h.action == "handoff" and unsafe and draft is not None:
            out["blocked_draft"] = draft                          # for the human, never for the customer
            out["draft"] = Draft(text=UNSURE)
    return out


async def handoff_or_review(state: TurnState) -> Update:
    out = await _handoff(state)
    drafted = state.get("draft")
    if drafted is None or not drafted.text:      # e.g. "talk to a human": nothing drafted yet
        out["draft"] = Draft(text=CONNECTING)
    merged: TurnState = {**state, **out}  # type: ignore[typeddict-item]
    limit = min(1.5 if state["env"].channel == "chat" else 5.0, max(0.0, _left(state)))
    s = get_settings()
    text = await summarize(merged, get_llm(), model=s.model_fast, timeout_ms=int(limit * 1000)) if limit > 0.3 \
        else plain(merged)
    out["handoff"] = out["handoff"].model_copy(update={"summary": text})
    return out


async def safety_handoff(state: TurnState) -> Update:
    out = await _safety(state)
    merged: TurnState = {**state, **out}  # type: ignore[typeddict-item]
    out["handoff"] = out["handoff"].model_copy(update={"summary": plain(merged)})       # immediate: no model call
    return out


async def respond(state: TurnState) -> Update:
    d, h = state.get("draft"), state.get("handoff")
    return {"response": {"text": d.text if d and d.text else CONNECTING, "citations": [c.model_dump() for c in d.citations] if d else [],
                         "answered_tier": state.get("answered_tier"),
                         # the customer sees that a handoff happened, not the internal reason or note
                         "handoff": {"action": h.action, "priority": h.priority} if h else None}}


NODES = {f.__name__: f for f in (verify, decide, handoff_or_review, safety_handoff, respond)}
