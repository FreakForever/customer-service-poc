"""Real classify (kNN) and llm_router nodes (BS-13, LLD-6; FR-RT-02, FR-RT-03, FR-CH-07)."""

import asyncio
import logging
from typing import Any

from app.config.settings import get_settings
from app.obs.tracing import span_set
from app.orchestrator.state import Intent, TurnState
from app.providers import get_embedder, get_llm
from app.router.knn import get_knn
from app.router.language import detect_language
from app.router.llm_router import llm_route

log = logging.getLogger("mcs.router")
Update = dict[str, Any]
LLM_SHARE_OF_BUDGET = 0.85     # leave room so a slow model falls back to the kNN guess instead of degrading the turn


async def classify(state: TurnState) -> Update:
    text = state["env"].text
    out: Update = {"language": detect_language(text)}
    if any(i.source == "rule" for i in state.get("intents") or []):
        return out                                    # exact intent already decided; no embedding needed
    res, qvec = await get_knn().classify(text, get_embedder())
    tau = state["policy"].thresholds.get("intent_knn", 0.8)
    accepted = res.accepted(tau)
    span_set(knn=res.ranked[:3], best_sim=res.best_sim, accepted=accepted)
    out.update({"query_embedding": qvec, "knn": res.ranked[:3]})
    name, score = res.top
    out["intents"] = [Intent(name=name, confidence=score, source="knn")] if accepted and name else []
    return out


async def llm_router(state: TurnState) -> Update:
    s = get_settings()
    policy = state["policy"]
    budget_ms = policy.timeout_ms("router_llm")
    hint = state.get("knn") or []
    try:
        async with asyncio.timeout(budget_ms * LLM_SHARE_OF_BUDGET / 1000):
            routed, comp = await llm_route(state["env"].text, get_llm(), model=s.model_fast,
                                           timeout_ms=int(budget_ms * LLM_SHARE_OF_BUDGET), hint=hint)
    except Exception as exc:  # noqa: BLE001 - a router failure must not fail the turn: use the kNN guess
        span_set(status="fallback", fallback=type(exc).__name__)
        log.info("llm router fell back to kNN: %s", type(exc).__name__)
        return {"intents": [Intent(name=hint[0][0], confidence=hint[0][1], source="knn")] if hint else []}
    span_set(model=comp.model, tokens_in=comp.input_tokens, tokens_out=comp.output_tokens,
             attempts=comp.attempts, routed=[i.name for i in routed.intents])
    intents = [Intent(name=i.name, confidence=i.confidence, source="llm", span=i.span)
               for i in routed.intents if i.name != "other"]
    out: Update = {"intents": intents}
    if routed.language == "hinglish":
        out["language"] = "hinglish"
    if routed.needs_clarification:
        out["clarification"] = routed.needs_clarification
    return out


NODES = {"classify": classify, "llm_router": llm_router}
