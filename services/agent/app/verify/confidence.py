"""Confidence and handoff policy (LLD-11, FR-VF-04)."""

import math
from typing import Any

from app.orchestrator.state import HandoffDecision, TurnState
from app.retrieval.evidence import Evidence

ACCOUNT_INTENTS = {"service.ticket_status", "amc.status", "warranty.status", "asset.info", "service.create_ticket"}

ACCOUNT_TOOLS = {"get_ticket_status", "get_asset", "get_warranty", "get_amc_contract"}


def retrieval_norm(evidence: list[Evidence]) -> float:
    """0–1. Exact sources (graph paths, tools, the documented section) count as 1; retrieved chunks use the
    cross-encoder score, or leg agreement when the reranker was skipped (RRF isn't a probability)."""
    if not evidence:
        return 0.0
    if any(e.kind in ("kg_path", "tool") or e.source in ("documented_in", "faq_cache") for e in evidence):
        return 1.0
    best = 0.0
    for e in evidence:
        rr = e.metadata.get("rerank_score")
        if rr is not None:
            best = max(best, 1 / (1 + math.exp(-rr)))
        elif e.metadata.get("vector_rank") and e.metadata.get("lexical_rank"):
            best = max(best, 0.7)
        else:
            best = max(best, 0.5)
    return best


def router_conf(state: TurnState) -> float:
    intents = state.get("intents") or []
    if not intents:
        return 0.9 if state.get("active_agent") else 0.5      # sticky turn: the specialist accepted it
    return max(i.confidence for i in intents)


def score(*, router: float, retrieval: float, coverage: float, judge: float | None) -> dict[str, float]:
    proxy = judge if judge is not None else min(1.0, coverage * retrieval + 0.1)
    total = 0.25 * router + 0.25 * retrieval + 0.20 * coverage + 0.30 * proxy
    return {"router": round(router, 3), "retrieval": round(retrieval, 3), "coverage": round(coverage, 3),
            "judge_or_proxy": round(proxy, 3), "judge_run": float(judge is not None), "confidence": round(total, 3)}


def overrides(state: TurnState) -> HandoffDecision | None:
    """Hard overrides, independent of the score."""
    v = state.get("verification")
    names = {i.name for i in state.get("intents") or []}
    flags = state.get("flags") or set()
    policy = state["policy"]
    if v is not None and not v.passed:
        return HandoffDecision(action=policy.on_low_confidence, reason="consistency check failed: " + "; ".join(v.issues[:3]))
    if "human" in names or "human_requested" in flags:
        return HandoffDecision(action="handoff", reason="customer asked for a person")
    env = state["env"]
    if env.channel == "email":
        draft = state.get("draft")
        acts: list[dict[str, Any]] = draft.actions if draft else []
        if "complaint" in names or "complaint" in flags:
            return HandoffDecision(action="draft_for_review", reason="complaint")
        if any(a.get("priority") == "P1" for a in acts):
            return HandoffDecision(action="draft_for_review", reason="priority P1", priority="P1")
        account = bool(names & ACCOUNT_INTENTS) or any(e.source in ACCOUNT_TOOLS for e in state.get("evidence") or [])
        if env.customer is not None and env.customer.trust == "domain" and (account or acts):
            return HandoffDecision(action="draft_for_review", reason="sender matched by domain only")
        if env.customer is None and names & ACCOUNT_INTENTS:
            return HandoffDecision(action="draft_for_review", reason="account question from an unknown sender")
    return None
