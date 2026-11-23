"""Hybrid retrieval (LLD-8; FR-RG-01, FR-RG-03, FR-RG-04, FR-RG-06).

vector leg (pgvector cosine, k=30) + lexical leg (Postgres FTS, k=30) → RRF (k=60) → rerank top 20 → k_ctx (6).
Filters: document status (active only unless the query asks about the past), doc type, product family and model.
The lexical leg ORs the query's lexemes (websearch/plainto AND every word, which misses most natural questions).
"""

import asyncio
import datetime as dt
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.providers.base import Embedder
from app.retrieval.rerank import Reranker

RRF_K = 60
LEG_K = 30
RERANK_TOP = 20
K_CTX = 6

_PAST_CUE = re.compile(r"\b(old|older|previous|earlier|former|prior|past|used to|originally|v1|version 1|"
                       r"before (?:19|20)\d\d|until (?:19|20)\d\d|back in)\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(19|20)\d\d\b")


@dataclass
class Filters:
    family: str | None = None
    model: str | None = None
    doc_types: list[str] | None = None
    include_superseded: bool | None = None  # None = decide from the query (FR-RG-06)


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    version: str
    anchor: str
    heading_path: list[str]
    content: str
    title: str
    doc_type: str
    status: str
    family: str | None
    metadata: dict[str, Any]
    vector_rank: int | None = None
    lexical_rank: int | None = None
    rrf: float = 0.0
    rerank_score: float | None = None

    @property
    def evidence_id(self) -> str:
        return f"{self.doc_id}#{self.anchor}"

    @property
    def rerank_text(self) -> str:
        return " > ".join(self.heading_path) + "\n\n" + self.content


@dataclass
class RetrievalResult:
    hits: list[Hit]
    candidates: list[Hit]            # fused list before reranking/cut (for eval and tracing)
    include_superseded: bool
    reranker: str
    rerank_status: str               # ok | skipped | timeout | error
    timings_ms: dict[str, int] = field(default_factory=dict)


def wants_superseded(query: str, today: dt.date | None = None) -> bool:
    """Past-date or 'old/previous policy' cue → superseded documents may be used (FR-RG-06)."""
    today = today or dt.date.today()
    if _PAST_CUE.search(query):
        return True
    return any(int(m.group(0)) < today.year for m in _YEAR.finditer(query))


def rrf_fuse(*rankings: list[int], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion of ranked id lists (rank 1 = best). Ties broken by id for determinism."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


_FILTER_SQL = """
  (d.status = 'active' OR (CAST(:sup AS boolean) AND d.status = 'superseded'))
  AND (CAST(:family AS text) IS NULL OR coalesce(c.metadata->>'family', d.family) IS NULL
       OR coalesce(c.metadata->>'family', d.family) = CAST(:family AS text))
  AND (CAST(:model AS text) IS NULL OR coalesce(jsonb_array_length(c.metadata->'models'), 0) = 0
       OR c.metadata->'models' ? CAST(:model AS text))
  AND (CAST(:doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:doc_types AS text[])))
"""

_VECTOR_SQL = f"""
SELECT c.id FROM kb_chunks c JOIN kb_documents d USING (doc_id, version)
WHERE c.embedding IS NOT NULL AND {_FILTER_SQL}
ORDER BY c.embedding <=> CAST(:qv AS vector), c.id LIMIT :k"""

_LEXICAL_SQL = f"""
WITH q AS (
  SELECT CAST(string_agg(quote_literal(replace(lexeme, E'\\\\', '')), ' | ') AS tsquery) AS tq
  FROM unnest(tsvector_to_array(to_tsvector('english', :text))) AS lexeme)
SELECT c.id FROM kb_chunks c JOIN kb_documents d USING (doc_id, version), q
WHERE q.tq IS NOT NULL AND c.tsv @@ q.tq AND {_FILTER_SQL}
ORDER BY ts_rank_cd(c.tsv, q.tq) DESC, c.id LIMIT :k"""

_DETAIL_SQL = """
SELECT c.id, c.doc_id, c.version, c.anchor, c.heading_path, c.content, c.metadata,
       d.title, d.doc_type, d.status, coalesce(c.metadata->>'family', d.family) AS family
FROM kb_chunks c JOIN kb_documents d USING (doc_id, version) WHERE c.id = ANY(:ids)"""


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def retrieve(conn: AsyncConnection, embedder: Embedder, query: str, filters: Filters | None = None, *,
                   reranker: Reranker | None = None, k_ctx: int = K_CTX, rerank_top: int = RERANK_TOP,
                   rerank_timeout_ms: int = 250, leg_k: int = LEG_K) -> RetrievalResult:
    f = filters or Filters()
    sup = f.include_superseded if f.include_superseded is not None else wants_superseded(query)
    params = {"sup": sup, "family": f.family, "model": f.model, "doc_types": f.doc_types, "k": leg_k}
    timings: dict[str, int] = {}

    t0 = time.perf_counter()
    embed_task = asyncio.create_task(embedder.embed([query], kind="query"))
    lexical = list((await conn.execute(text(_LEXICAL_SQL), {**params, "text": query})).scalars())
    timings["lexical"] = int((time.perf_counter() - t0) * 1000)
    qv = (await embed_task)[0]
    timings["embed"] = int((time.perf_counter() - t0) * 1000)
    t1 = time.perf_counter()
    vector = list((await conn.execute(text(_VECTOR_SQL), {**params, "qv": _vec(qv)})).scalars())
    timings["vector"] = int((time.perf_counter() - t1) * 1000)

    fused = rrf_fuse(vector, lexical)
    ids = [cid for cid, _ in fused]
    rows = {r.id: r for r in (await conn.execute(text(_DETAIL_SQL), {"ids": ids})).all()} if ids else {}
    vrank = {cid: i for i, cid in enumerate(vector, 1)}
    lrank = {cid: i for i, cid in enumerate(lexical, 1)}
    candidates = [Hit(chunk_id=cid, doc_id=r.doc_id, version=r.version, anchor=r.anchor,
                      heading_path=list(r.heading_path or []), content=r.content, title=r.title,
                      doc_type=r.doc_type, status=r.status, family=r.family, metadata=r.metadata or {},
                      vector_rank=vrank.get(cid), lexical_rank=lrank.get(cid), rrf=score)
                  for cid, score in fused if (r := rows.get(cid)) is not None]

    top = candidates[:rerank_top]
    status, name = "skipped", reranker.name if reranker else "none"
    if reranker is not None and top and reranker.name != "none":
        t2 = time.perf_counter()
        try:
            async with asyncio.timeout(rerank_timeout_ms / 1000):
                scores = await asyncio.to_thread(reranker.score, query, [h.rerank_text for h in top])
            for h, s in zip(top, scores, strict=True):
                h.rerank_score = s
            top = sorted(top, key=lambda h: (-(h.rerank_score or 0.0), -h.rrf, h.chunk_id))
            status = "ok"
        except TimeoutError:
            status = "timeout"   # keep RRF order within the latency budget
        except Exception:  # noqa: BLE001 - a broken reranker must not break answering
            status = "error"
        timings["rerank"] = int((time.perf_counter() - t2) * 1000)
    timings["total"] = int((time.perf_counter() - t0) * 1000)
    return RetrievalResult(hits=top[:k_ctx], candidates=candidates, include_superseded=sup, reranker=name,
                           rerank_status=status, timings_ms=timings)
