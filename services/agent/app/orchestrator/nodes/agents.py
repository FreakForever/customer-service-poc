"""planner and agents nodes (BS-17, LLD-10; FR-OR-05): specialists, sticky agent, hand-back, multi-intent."""

import asyncio
import logging
from dataclasses import replace
from typing import Any

from app.agents import knowledge, parts, sales, service_request, troubleshoot
from app.agents.base import AgentCtx, AgentResult
from app.agents.rag_answerer import abstention
from app.config.settings import get_settings
from app.db import get_engine
from app.kg.client import get_read_driver
from app.obs.tracing import span_set
from app.orchestrator.budget import allocate_s
from app.orchestrator.nodes.tools import ASK, UNAVAILABLE, lookups, plan
from app.orchestrator.state import Citation, Draft, HandoffDecision, SubQuery, TurnState
from app.providers import get_embedder, get_llm
from app.retrieval.evidence import Evidence
from app.retrieval.graphrag import GraphRAGDeps
from app.retrieval.rerank import get_reranker
from app.rules.context import get_rule_context
from app.tools.account import ToolContext
from app.tools.repo import PgRepo

log = logging.getLogger("mcs.agents")
Update = dict[str, Any]
AGENTS = {"troubleshoot": troubleshoot.run, "service_request": service_request.run, "parts": parts.run,
          "sales": sales.run, "knowledge": knowledge.run}
BUDGET_SHARE = 0.95
HEADINGS = {"troubleshoot": "Troubleshooting", "parts": "Spare parts", "sales": "Quotation", "knowledge": "Information",
            "service_request": "Service request", "amc": "AMC", "warranty": "Warranty", "service": "Ticket", "asset": "Machine"}


def agent_for(intent: str | None) -> str:
    name = intent or ""
    if name.startswith("troubleshoot."):
        return "troubleshoot"
    if name.startswith("parts."):
        return "parts"
    if name == "sales.quote":
        return "sales"
    if name in ("service.create_ticket", "complaint"):
        return "service_request"
    return "knowledge"


async def planner(state: TurnState) -> Update:
    """One sub-query per distinct specialist/look-up, from the router's intents and spans (D-035)."""
    seen: dict[str, SubQuery] = {}
    for i in sorted(state.get("intents") or [], key=lambda x: -x.confidence):
        if i.name in ("human", "safety", "oos", "smalltalk", "other"):
            continue
        if i.name == "complaint" and state["entities"].ticket_ids:
            continue                        # about an existing ticket: status + handoff, not a new ticket
        key = i.name if plan(i.name, state) is not None and not i.name.startswith("parts.") else agent_for(i.name)
        seen.setdefault(key, SubQuery(text=i.span or state["env"].text, intent=i.name))
    span_set(subqueries=[s.intent for s in seen.values()])
    return {"subqueries": list(seen.values())}


async def _with_ctx(state: TurnState, name: str, text: str, dialog: dict[str, Any], timeout_ms: int,
                    sticky: bool) -> AgentResult:
    engine = get_engine()
    if engine is None:
        raise ConnectionError("no database")
    s, rules, env = get_settings(), await get_rule_context(), state["env"]
    reranker = await asyncio.to_thread(get_reranker)
    async with engine.begin() as conn:             # commits tool writes; one connection per agent
        tools = ToolContext(PgRepo(conn), customer_id=env.customer.customer_id if env.customer else None,
                            channel=env.channel, safety="safety" in (state.get("flags") or set()))
        deps = GraphRAGDeps(conn=conn, embedder=get_embedder(), reranker=reranker, kg_driver=get_read_driver(),
                            kg_database=s.neo4j_database, linker=rules.linker, llm=get_llm(), llm_model=s.model_strong,
                            rerank_timeout_ms=state["policy"].timeout_ms("rerank"),
                            kg_timeout_s=state["policy"].timeout_ms("kg") / 1000)
        ctx = AgentCtx(env=env, text=text, ents=state["entities"], dialog=dialog, deps=deps, tools=tools, llm=get_llm(),
                       model_fast=s.model_fast, model_strong=s.model_strong, timeout_ms=timeout_ms, sticky=sticky)
        if sticky and name == "troubleshoot" and parts.CUES.search(text):
            span_set(handoff_to="parts")           # handoff between specialists keeps the dialog
            return await parts.run(replace(ctx, sticky=False))
        return await AGENTS[name](ctx)


def _update(res: AgentResult, dialog: dict[str, Any]) -> Update:
    out: Update = {"answered_tier": res.tier, "active_agent": res.active_agent, "dialog": dialog, "evidence": res.evidence,
                   "draft": Draft(text=res.text, citations=res.citations, actions=res.actions, form=res.form,
                                  abstained=res.abstained, generated=res.generated), "handback": False}
    if res.handoff:
        out["handoff"] = res.handoff
    return out


async def run_agent(state: TurnState, name: str, *, sticky: bool = False) -> Update:
    policy = state["policy"]
    budget = max(0.05, allocate_s(state, "generate")) * BUDGET_SHARE
    dialog = dict(state.get("dialog") or {})
    try:
        async with asyncio.timeout(budget):
            res = await _with_ctx(state, name, state["env"].text, dialog, int(budget * 1000), sticky)
    except Exception as exc:  # noqa: BLE001 - graph/db/model down or slow: say so and hand off, never fail the turn
        span_set(status="timeout" if isinstance(exc, TimeoutError) else "error", error=type(exc).__name__)
        log.warning("agent %s failed: %s", name, type(exc).__name__)
        prior = state.get("draft")
        if prior is not None and prior.abstained:
            return {"draft": prior, "answered_tier": "T2", "handback": False}
        return {"answered_tier": "T4", "draft": Draft(text="I couldn't complete that check, so I'm passing this to our "
                                                           "service team."), "handback": False,
                "handoff": HandoffDecision(action=policy.on_low_confidence, reason=f"{name} failed: {type(exc).__name__}")}
    if res.handback:
        span_set(handback=True)
        return {"active_agent": None, "handback": True, "dialog": dialog}
    return _update(res, dialog)


async def _sub(state: TurnState, q: SubQuery, budget: float, dialog: dict[str, Any]) -> tuple[str, AgentResult]:
    intent = q.intent or ""
    calls = plan(intent, state) if not intent.startswith("parts.") else None
    if calls is not None:
        head = HEADINGS.get(intent.split(".")[0], "Information")
        if not calls:
            return head, AgentResult(ASK.get(intent, "Could you share a few more details?"), tier="T0")
        text, ev = await lookups(state, calls)
        return head, AgentResult(text, tier="T0", evidence=ev)
    name = agent_for(intent)
    text = state["env"].text if name != "knowledge" else q.text
    return HEADINGS[name], await _with_ctx(state, name, text, dialog, int(budget * 1000), False)


async def multi(state: TurnState, subs: list[SubQuery]) -> Update:
    """Independent sub-queries in parallel, one section each; a failed section doesn't lose the others."""
    policy = state["policy"]
    budget = max(0.05, allocate_s(state, "generate")) * BUDGET_SHARE

    dialog = dict(state.get("dialog") or {})

    async def one(q: SubQuery) -> tuple[str, AgentResult]:
        async with asyncio.timeout(budget):
            return await _sub(state, q, budget, dialog)
    done = await asyncio.gather(*(one(q) for q in subs), return_exceptions=True)
    sections: list[str] = []
    failed: list[str] = []
    evidence: list[Evidence] = []
    citations: list[Citation] = []
    actions: list[dict[str, Any]] = []
    for q, r in zip(subs, done, strict=True):
        if isinstance(r, BaseException):
            failed.append(f"{q.intent}: {type(r).__name__}")
            sections.append(f"**{HEADINGS.get(agent_for(q.intent), 'Information')}**\n{UNAVAILABLE}")
            continue
        head, res = r
        shift = len(evidence)                       # renumber [E#] so markers stay unique across sections
        text = res.text
        for n in range(len(res.evidence), 0, -1):
            text = text.replace(f"[E{n}]", f"[E{n + shift}]")
        sections.append(f"**{head}**\n{text}")
        evidence += res.evidence
        citations += [c.model_copy(update={"marker": f"[E{int(c.marker[2:-1]) + shift}]"}) for c in res.citations]
        actions += res.actions
    span_set(subqueries=[q.intent for q in subs], failed=failed)
    out: Update = {"answered_tier": "T3", "evidence": evidence, "handback": False, "dialog": dialog,
                   "draft": Draft(text="\n\n".join(sections), citations=citations, actions=actions,
                                  generated=any(not isinstance(r, BaseException) and r[1].generated for r in done))}
    complaint = any(i.name in ("complaint", "human") for i in state.get("intents") or [])
    if complaint or failed:
        out["handoff"] = HandoffDecision(action="handoff" if complaint and not failed else policy.on_low_confidence,
                                         reason="complaint" if complaint else "; ".join(failed), priority=None)
    return out


async def agents(state: TurnState) -> Update:
    if (name := state.get("active_agent")) in AGENTS and not state.get("subqueries"):
        span_set(sticky_agent=name)
        return await run_agent(state, str(name), sticky=True)
    subs = state.get("subqueries") or []
    if len(subs) > 1 or (subs and not (subs[0].intent or "").startswith("parts.") and plan(subs[0].intent or "", state) is not None):
        return await multi(state, subs)
    d = state.get("draft")
    if d is not None and d.abstained:               # T2 had no answer: try graph + documents
        out = await run_agent(state, "knowledge")
        return out if not out["draft"].abstained else {**out, "draft": abstention()}
    return await run_agent(state, agent_for(subs[0].intent if subs else None))


NODES = {"planner": planner, "agents": agents}
