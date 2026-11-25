"""Real cache_lookup node (BS-14, LLD-7): exact cache, then semantic FAQ cache with the turn's query embedding."""

from typing import Any

from app.cache.store import CURATED, CacheHit, exact_lookup, semantic_lookup
from app.db import get_engine
from app.obs.tracing import span_set
from app.orchestrator.state import Citation, Draft, TurnState
from app.providers import get_embedder

Update = dict[str, Any]


def _draft(hit: CacheHit) -> Draft:
    cites = [Citation(marker=f"[{i}]", evidence_id=f"doc:{d}" if d != CURATED else f"faq:{hit.faq_id}", doc_id=d if d != CURATED else None) for i, d in enumerate(hit.source_docs, 1)]
    text = hit.answer + ("".join(f" {c.marker}" for c in cites) if cites else "")
    return Draft(text=text, citations=cites)


async def cache_lookup(state: TurnState) -> Update:
    engine = get_engine()
    ents = state.get("entities")
    has_ids = bool(ents is not None and ents.has_account_ids)
    if engine is None or has_ids or state.get("flags"):
        span_set(cache_hit=False, skipped="no db" if engine is None else "account ids or flags")
        return {"cache_hit": False}
    top = max(state.get("intents") or [], key=lambda i: i.confidence, default=None)
    text = state["env"].text
    try:
        async with engine.begin() as conn:
            hit = await exact_lookup(conn, text)
            if hit is None:
                qvec = state.get("query_embedding") or (await get_embedder().embed([text], kind="query"))[0]
                tau = state["policy"].thresholds.get("cache_semantic", 0.90)
                hit = await semantic_lookup(conn, qvec, tau, has_account_ids=has_ids,
                                            intent=top.name if top else None)
    except Exception as exc:  # noqa: BLE001 - a cache problem is a miss, never a failed turn
        span_set(cache_hit=False, error=type(exc).__name__)
        return {"cache_hit": False}
    if hit is None:
        span_set(cache_hit=False)
        return {"cache_hit": False}
    span_set(cache_hit=True, layer=hit.layer, faq_id=hit.faq_id, similarity=hit.similarity,
             cache_ms=round(hit.server_ms, 2))
    return {"cache_hit": True, "answered_tier": "T1", "draft": _draft(hit), "confidence": 1.0}


NODES = {"cache_lookup": cache_lookup}
