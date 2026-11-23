"""Hybrid GraphRAG evidence assembly (LLD-9, FR-KG-05).

graph_evidence(question, entities, mode):
  kg / hybrid: choose templates from the intent and linked entities (dependent templates run in a second stage,
               e.g. stock of the part T-COVER found), else guarded Text2Cypher; verbalize paths as facts with
               path ids; follow (ErrorCode)-[:DOCUMENTED_IN]->(Document) to the exact manual chunk.
  rag / hybrid: hybrid retrieval with family/model filters from the entities.
Merge: KG facts first (by likelihood), then DOCUMENTED_IN chunks, then retrieved chunks; chunks deduped by
doc#anchor. Failures in one leg are recorded and the other legs still answer.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.kg.client import read_query
from app.kg.linker import Entities, Linker
from app.kg.templates import run_template
from app.kg.text2cypher import Text2CypherFailed, text2cypher
from app.kg.verbalize import verbalize, verbalize_query_rows
from app.providers.base import LLM, Embedder
from app.retrieval.evidence import Evidence
from app.retrieval.hybrid import Filters, Hit, retrieve
from app.retrieval.rerank import Reranker

log = logging.getLogger("mcs.graphrag")
Mode = Literal["rag", "kg", "hybrid"]
MAX_KG = 8
MAX_CHUNKS = 6
MAX_STOCK_FOLLOWUPS = 4

_PARTS = re.compile(r"\b(spare|parts?|replace(?:ment)?|need to order|which part)\b", re.IGNORECASE)
_STOCK = re.compile(r"\b(stock|available|availability|in hand|warehouse|dispatch)\b", re.IGNORECASE)
_COVER = re.compile(r"\b(cover(?:ed|age)?|amc|contract|plan|warranty)\b", re.IGNORECASE)
_SHARED = re.compile(r"\b(other (?:error )?codes?|same cause|share[sd]?|related codes?)\b", re.IGNORECASE)
_SC = re.compile(r"\b(service cent(?:re|er)s?|engineer|technician|nearest|visit)\b", re.IGNORECASE)


@dataclass
class GraphRAGDeps:
    conn: AsyncConnection | None = None       # Postgres (chunks)
    embedder: Embedder | None = None
    reranker: Reranker | None = None
    kg_driver: Any = None
    kg_database: str | None = None
    linker: Linker | None = None               # node props (codes, cities, names)
    llm: LLM | None = None                     # Text2Cypher fallback
    llm_model: str = ""
    rerank_timeout_ms: int = 250


@dataclass
class GraphEvidence:
    evidence: list[Evidence]
    plan: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def kg(self) -> list[Evidence]:
        return [e for e in self.evidence if e.kind == "kg_path"]

    @property
    def chunks(self) -> list[Evidence]:
        return [e for e in self.evidence if e.kind == "chunk"]


def _props(deps: GraphRAGDeps, node_id: str) -> dict[str, Any]:
    if deps.linker and node_id in deps.linker.nodes:
        return deps.linker.nodes[node_id].get("props", {})
    return {}


def _code(deps: GraphRAGDeps, error_id: str) -> str:
    return _props(deps, error_id).get("code") or error_id.split("-", 1)[-1]


def _city(deps: GraphRAGDeps, ents: Entities) -> str | None:
    if wh := ents.ids("Warehouse"):
        return _props(deps, wh[0]).get("city")
    if city := ents.ids("City"):
        return _props(deps, city[0]).get("name") or city[0].split("-", 1)[-1].title()
    return None


def choose_templates(question: str, ents: Entities, deps: GraphRAGDeps,
                     intent: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Stage-1 templates from the intent (when the router supplied one) and question cues."""
    plan: list[tuple[str, dict[str, Any]]] = []
    model, family, errors = ents.model, ents.family, ents.error_codes
    city = _city(deps, ents)
    parts = ents.ids("SparePart")
    wants_parts = bool(_PARTS.search(question)) or intent in ("parts.availability", "parts.compatibility")
    wants_stock = bool(_STOCK.search(question)) or intent == "parts.availability"
    wants_cover = bool(_COVER.search(question)) or (intent or "").startswith(("amc.", "warranty."))
    if model and errors:
        code = _code(deps, errors[0])
        plan.append(("T-DIAG", {"model": model, "code": code}))
        if wants_parts or wants_cover or (wants_stock and not parts):
            plan.append(("T-COVER", {"model": model, "code": code}))
    if errors and family and (_SHARED.search(question)):
        plan.append(("T-SHARED", {"error_id": errors[0], "family": family}))
    for cause in ents.ids("Cause"):
        if family:
            plan.append(("T-PARTS", {"cause": cause, "family": family}))
    if parts and city and (wants_stock or wants_parts):
        plan.append(("T-STOCK", {"part": parts[0], "city": city}))
    if city and _SC.search(question):
        plan.append(("T-SC", {"city": city}))
    return plan


async def _run(tid: str, params: dict[str, Any], deps: GraphRAGDeps) -> tuple[str, dict[str, Any], list[dict]]:
    res = await run_template(tid, params, driver=deps.kg_driver, database=deps.kg_database)
    return tid, params, res.rows


def _names(deps: GraphRAGDeps) -> dict[str, str]:
    if not deps.linker:
        return {}
    return {nid: n.get("props", {}).get("name", nid) for nid, n in deps.linker.nodes.items()
            if n["label"] in ("Warehouse", "AMCPlan")}


async def kg_evidence(question: str, ents: Entities, deps: GraphRAGDeps, intent: str | None,
                      result: GraphEvidence) -> list[Evidence]:
    plan = choose_templates(question, ents, deps, intent)
    result.plan.extend(plan)
    names = _names(deps)
    out: list[Evidence] = []
    if plan:
        stage1 = await asyncio.gather(*(_run(t, p, deps) for t, p in plan), return_exceptions=True)
        followups: list[tuple[str, dict[str, Any]]] = []
        city = _city(deps, ents)
        for item in stage1:
            if isinstance(item, BaseException):
                result.errors.append(f"template: {type(item).__name__}: {item}")
                continue
            tid, params, rows = item
            out += verbalize(tid, params, rows, names)
            # dependent stage: stock (or service centre) for the part the coverage template found
            if tid == "T-COVER" and rows and city and not ents.ids("SparePart") and _STOCK.search(question):
                for part in dict.fromkeys(r["part_no"] for r in rows[:MAX_STOCK_FOLLOWUPS]):
                    followups.append(("T-STOCK", {"part": part, "city": city}))
        if followups:
            result.plan.extend(followups)
            for item in await asyncio.gather(*(_run(t, p, deps) for t, p in followups), return_exceptions=True):
                if isinstance(item, BaseException):
                    result.errors.append(f"template: {type(item).__name__}: {item}")
                else:
                    out += verbalize(item[0], item[1], item[2], names)
    elif deps.llm is not None:
        result.plan.append(("text2cypher", {}))
        try:
            t2c = await text2cypher(question, ents, deps.llm, deps.kg_driver, model=deps.llm_model,
                                    database=deps.kg_database)
            out += verbalize_query_rows(t2c.cypher, t2c.rows)
        except Text2CypherFailed as exc:
            result.errors.append(f"text2cypher: {exc}")
    return out


async def documented_chunks(error_ids: list[str], deps: GraphRAGDeps) -> list[Evidence]:
    """Follow (ErrorCode)-[:DOCUMENTED_IN]->(Document) to the manual chunk for that code (active versions only)."""
    if not error_ids or deps.conn is None:
        return []
    rows, _ = await read_query(deps.kg_driver, """
        MATCH (e:ErrorCode)-[:DOCUMENTED_IN]->(d:Document) WHERE e.id IN $ids
        RETURN e.id AS error_id, d.id AS doc_id""", {"ids": error_ids}, database=deps.kg_database)
    out: list[Evidence] = []
    for r in rows:
        chunk = (await deps.conn.execute(text("""
            SELECT c.doc_id, c.anchor, c.heading_path, c.content, d.title FROM kb_chunks c
            JOIN kb_documents d USING (doc_id, version)
            WHERE c.doc_id = :doc AND d.status = 'active' AND c.metadata->'error_codes' ? :eid
            ORDER BY c.ord LIMIT 1"""), {"doc": r["doc_id"], "eid": r["error_id"]})).first()
        if chunk:
            out.append(Evidence(id=f"doc:{chunk.doc_id}#{chunk.anchor}", kind="chunk", source="documented_in",
                                text=chunk.content, score=1.0, title=chunk.title, doc_id=chunk.doc_id,
                                anchor=chunk.anchor, metadata={"heading_path": list(chunk.heading_path or []),
                                                               "error_id": r["error_id"]}))
    return out


def _chunk_evidence(h: Hit) -> Evidence:
    return Evidence(id=f"doc:{h.doc_id}#{h.anchor}", kind="chunk", source="hybrid", text=h.content,
                    score=h.rerank_score if h.rerank_score is not None else h.rrf, title=h.title, doc_id=h.doc_id,
                    anchor=h.anchor, metadata={"heading_path": h.heading_path, "doc_type": h.doc_type,
                                               "status": h.status, "chunk_id": h.chunk_id})


def merge(kg: list[Evidence], documented: list[Evidence], rag: list[Evidence],
          max_kg: int = MAX_KG, max_chunks: int = MAX_CHUNKS) -> list[Evidence]:
    facts: dict[str, Evidence] = {}
    for e in sorted(kg, key=lambda e: -e.score):
        facts.setdefault(e.id, e)
    chunks: dict[str, Evidence] = {}
    for e in documented + rag:                       # DOCUMENTED_IN first: it is the exact section
        if e.id in chunks:
            chunks[e.id].metadata.setdefault("also_from", []).append(e.source)
        else:
            chunks[e.id] = e
    return list(facts.values())[:max_kg] + list(chunks.values())[:max_chunks]


async def graph_evidence(question: str, ents: Entities, mode: Mode, deps: GraphRAGDeps,
                         intent: str | None = None) -> GraphEvidence:
    result = GraphEvidence(evidence=[])
    t0 = time.perf_counter()

    async def kg_leg() -> list[Evidence]:
        if mode == "rag" or deps.kg_driver is None:
            return []
        t = time.perf_counter()
        try:
            return await kg_evidence(question, ents, deps, intent, result)
        finally:
            result.timings_ms["kg"] = int((time.perf_counter() - t) * 1000)

    async def rag_leg() -> list[Evidence]:
        if mode == "kg" or deps.conn is None or deps.embedder is None:
            return []
        t = time.perf_counter()
        res = await retrieve(deps.conn, deps.embedder, question, Filters(family=ents.family, model=ents.model),
                             reranker=deps.reranker, rerank_timeout_ms=deps.rerank_timeout_ms)
        result.timings_ms["rag"] = int((time.perf_counter() - t) * 1000)
        return [_chunk_evidence(h) for h in res.hits]

    kg_out, rag_out = await asyncio.gather(kg_leg(), rag_leg(), return_exceptions=True)
    if isinstance(kg_out, BaseException):
        result.errors.append(f"kg: {type(kg_out).__name__}: {kg_out}")
        kg_out = []
    if isinstance(rag_out, BaseException):
        result.errors.append(f"rag: {type(rag_out).__name__}: {rag_out}")
        rag_out = []
    documented: list[Evidence] = []
    if mode != "rag" and deps.kg_driver is not None:
        errs = sorted({eid for e in kg_out for eid in [e.metadata.get("error_id")] if eid} | set(ents.error_codes))
        try:
            documented = await documented_chunks(errs, deps)
        except Exception as exc:  # noqa: BLE001 - a missing chunk must not drop the KG facts
            result.errors.append(f"documented_in: {type(exc).__name__}: {exc}")
    result.evidence = merge(kg_out, documented, rag_out)
    result.timings_ms["total"] = int((time.perf_counter() - t0) * 1000)
    for err in result.errors:
        log.warning("graph_evidence: %s", err)
    return result
