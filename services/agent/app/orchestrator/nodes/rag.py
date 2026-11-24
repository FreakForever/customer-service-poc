"""rag_answer node (BS-15, LLD-8): hybrid retrieval → one fast-model call → cited draft or abstention."""

import asyncio
import time
from typing import Any

from app.agents.rag_answerer import abstention, answer
from app.config.settings import get_settings
from app.db import get_engine
from app.obs.tracing import span_set
from app.orchestrator.state import TurnState
from app.orchestrator.stream import token_sink
from app.providers import get_embedder, get_llm
from app.providers.base import Embedder
from app.retrieval.evidence import Evidence
from app.retrieval.graphrag import chunk_evidence
from app.retrieval.hybrid import Filters, retrieve
from app.retrieval.rerank import get_reranker

Update = dict[str, Any]
BUDGET_SHARE = 0.95      # stop just before the node budget so the evidence found so far is kept


class _Precomputed:
    """Serve the turn's query embedding instead of embedding the same text twice."""

    def __init__(self, inner: Embedder, vec: list[float]) -> None:
        self.inner, self.vec, self.name, self.dim = inner, vec, "precomputed", len(vec)

    async def embed(self, texts: list[str], *, kind: Any) -> list[list[float]]:
        return [self.vec] if kind == "query" and len(texts) == 1 else await self.inner.embed(texts, kind=kind)


async def retrieve_evidence(state: TurnState) -> list[Evidence]:
    engine, ents = get_engine(), state["entities"]
    if engine is None:
        return []
    qvec = state.get("query_embedding")
    embedder = _Precomputed(get_embedder(), qvec) if qvec else get_embedder()
    reranker = await asyncio.to_thread(get_reranker)
    async with engine.connect() as conn:
        res = await retrieve(conn, embedder, state["env"].text, Filters(family=ents.family, model=ents.model),
                             reranker=reranker, rerank_timeout_ms=state["policy"].timeout_ms("rerank"))
    span_set(retrieval_ms=res.timings_ms, rerank_status=res.rerank_status, hits=[h.evidence_id for h in res.hits])
    return [chunk_evidence(h) for h in res.hits]


async def rag_answer(state: TurnState) -> Update:
    policy, s = state["policy"], get_settings()
    remaining = state["deadline"] - time.monotonic()
    budget_s = max(0.0, min(policy.timeout_ms("generate") / 1000, remaining)) * BUDGET_SHARE
    end = time.monotonic() + budget_s
    evidence: list[Evidence] = []
    try:
        async with asyncio.timeout(budget_s):
            evidence = await retrieve_evidence(state)
            left_ms = max(1, int((end - time.monotonic()) * 1000))
            res = await answer(state["env"].text, evidence, get_llm(), model=s.model_fast, timeout_ms=left_ms,
                               sink=token_sink.get())
    except Exception as exc:  # noqa: BLE001 - slow or unavailable model/store: abstain, keep evidence, let T3/T4 act
        span_set(status="timeout" if isinstance(exc, TimeoutError) else "error", error=type(exc).__name__)
        return {"evidence": evidence, "retrieval_score": 0.0, "draft": abstention(), "answered_tier": "T2"}
    if (c := res.completion) is not None:
        span_set(model=c.model, tokens_in=c.input_tokens, tokens_out=c.output_tokens, attempts=c.attempts,
                 fallback_used=c.fallback_used)
    span_set(abstained=res.draft.abstained, citations=[x.evidence_id for x in res.draft.citations])
    # RRF/rerank scores aren't calibrated; the model's abstention is the "low retrieval score" signal (D-033)
    return {"evidence": evidence, "retrieval_score": 1.0 if evidence else 0.0, "draft": res.draft,
            "answered_tier": "T2"}


NODES = {"rag_answer": rag_answer}
