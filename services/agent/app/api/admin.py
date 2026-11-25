"""Knowledge admin routes (LLD-14; FR-UI-05, FR-KB-01, FR-KB-04). All need the admin role."""

import asyncio
import datetime as dt
import json
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.chat import EngineDep
from app.api.staff import AdminDep, require
from app.cache.store import CURATED, CacheWriteRejected, _vec, invalidate_faq, write_answer
from app.channels.console_store import like, rows
from app.config.settings import get_settings
from app.ingest import csv_loaders, manage
from app.ingest.parsers import SUPPORTED, UnsupportedDocument, parse_document
from app.ingest.pipeline import doc_id_from_filename, submit_document
from app.kg.changes import InvalidChange, apply_change, get_write_driver
from app.kg.client import get_read_driver, read_query
from app.providers import ProviderError, get_embedder
from app.rules.context import bump_kg_version, refresh_rule_context

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require("admin"))])
MAX_UPLOAD = 10 * 1024 * 1024
MAX_CSV = 2 * 1024 * 1024
FAMILIES = ("MTC", "DHD", "HAD", "CHL", "MDH", "CCS", "RCY")
DocType = Literal["manual", "policy", "guide", "bulletin", "company"]
Q = Annotated[str, Query(max_length=100)]


# ---------- documents ----------

@router.post("/documents", status_code=202)
async def upload(_: AdminDep, engine: EngineDep, file: Annotated[UploadFile, File()], title: Annotated[str, Form(min_length=3, max_length=200)],
                 doc_type: Annotated[DocType, Form()], family: Annotated[str, Form()] = "", supersedes: Annotated[str, Form(max_length=100)] = "") -> dict[str, Any]:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "the file is larger than 10 MB")
    if family and family not in FAMILIES:
        raise HTTPException(422, "unknown product family")
    name = re.sub(r"[^\w.\- ]", "_", (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1])[-120:]
    if doc_id_from_filename(name) in ("DOC-", "DOC-DOC"):
        raise HTTPException(422, "give the file a descriptive name")
    try:
        content = await asyncio.to_thread(parse_document, name, data)
    except UnsupportedDocument as exc:
        raise HTTPException(415, f"unsupported file type; use {', '.join(sorted(SUPPORTED))}") from exc
    except Exception as exc:
        raise HTTPException(422, "the file can't be read") from exc
    if not content.strip():
        raise HTTPException(422, "no text was found in the file")
    async with engine.begin() as conn:
        # versions are numbered per document: one upload of the same document at a time
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:d))"), {"d": doc_id_from_filename(name)})
        if supersedes and not await rows(conn, "SELECT 1 FROM kb_documents WHERE doc_id = :d AND status = 'active'", d=supersedes):
            raise HTTPException(422, "the superseded document isn't active")
        out = await submit_document(conn, filename=name, data=data, title=title.strip(), doc_type=doc_type, family=family or None,
                                    supersedes=supersedes or None, bucket=get_settings().gcs_bucket)
    return {k: out[k] for k in ("doc_id", "version", "job_id")}


@router.get("/documents")
async def documents(_: AdminDep, engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        return {"items": await rows(conn, """
            SELECT d.doc_id, d.version, d.title, d.doc_type, d.family, d.status, d.effective_from, d.effective_to, d.supersedes, d.source, d.created_at,
              (SELECT count(*) FROM kb_chunks c WHERE c.doc_id = d.doc_id AND c.version = d.version) AS chunks
            FROM kb_documents d ORDER BY d.doc_id, d.created_at DESC, d.version DESC""")}


@router.delete("/documents/{doc_id}", status_code=202)
async def delete(doc_id: str, _: AdminDep, engine: EngineDep) -> dict[str, str]:
    async with engine.begin() as conn:
        job = await manage.delete_document(conn, doc_id)
    if job is None:
        raise HTTPException(404, "no such document")
    return {"job_id": job}


@router.post("/documents/{doc_id}/reindex", status_code=202)
async def reindex(doc_id: str, _: AdminDep, engine: EngineDep) -> dict[str, str]:
    async with engine.begin() as conn:
        job = await manage.reindex_document(conn, doc_id)
    if job is None:
        raise HTTPException(404, "no active version of this document")
    return {"job_id": job}


@router.get("/jobs")
async def jobs(_: AdminDep, engine: EngineDep, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    """E-mail payloads stay private: only document fields are shown."""
    async with engine.connect() as conn:
        items = await rows(conn, "SELECT id, kind, status, attempts, error, result, payload->>'doc_id' AS doc_id, payload->>'version' AS version, "
                                 "created_at, started_at, finished_at FROM jobs ORDER BY created_at DESC, id LIMIT :n", n=limit)
    for j in items:
        last = [x for x in (j["error"] or "").strip().splitlines() if x.strip()]
        j["error"] = last[-1][:300] if last else None
        j["result"] = {k: v for k, v in (j["result"] or {}).items() if isinstance(v, int | float | str | bool)}
    return {"items": items}


@router.post("/csv")
async def upload_csv(_: AdminDep, engine: EngineDep, type: csv_loaders.CsvType, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Structured upload (FR-KB-02): valid rows are applied, invalid ones come back with their line number."""
    data = await file.read(MAX_CSV + 1)
    if len(data) > MAX_CSV:
        raise HTTPException(413, "the file is larger than 2 MB")
    name = re.sub(r"[^\w.\- ]", "_", (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1])[-120:]
    try:
        job, res = await csv_loaders.load_csv(engine, get_embedder(), type, data, name)
    except (csv_loaders.BadFile, InvalidChange) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (Neo4jError, DriverError, ProviderError) as exc:
        raise HTTPException(503, "the graph or the embedding service can't be reached; nothing was saved") from exc
    if res.applied and type != "faqs":
        await refresh_rule_context()
    return {"job_id": job, "applied": res.applied, "rejected": res.rejected, "warnings": res.warnings}


# ---------- FAQ cache ----------

class FaqIn(BaseModel):
    question: str = Field(min_length=5, max_length=300)
    answer: str = Field(min_length=5, max_length=4000)
    status: Literal["active", "needs_review", "withdrawn"]


@router.get("/faqs")
async def faqs(_: AdminDep, engine: EngineDep, status: Literal["", "active", "needs_review", "withdrawn"] = "", q: Q = "") -> dict[str, Any]:
    async with engine.connect() as conn:
        return {"items": await rows(conn, """
            SELECT f.id, f.category, f.question, f.answer, f.source_doc, f.status, f.origin,
              (SELECT count(*) FROM faq_variants v WHERE v.faq_id = f.id) AS variants,
              (SELECT coalesce(sum(hits), 0) FROM answer_cache a WHERE a.answer->>'faq_id' = f.id) AS hits
            FROM faq_entries f WHERE (:s = '' OR f.status = :s) AND (:q = '' OR f.question ILIKE :l OR f.answer ILIKE :l OR f.id ILIKE :l)
            ORDER BY (f.status = 'needs_review') DESC, f.id""", s=status, q=q.strip(), l=like(q.strip()))}


@router.put("/faqs/{faq_id}")
async def put_faq(faq_id: str, body: FaqIn, _: AdminDep, engine: EngineDep) -> dict[str, Any]:
    vec = (await get_embedder().embed([body.question], kind="query"))[0]
    async with engine.begin() as conn:
        got = await rows(conn, "SELECT question, source_doc FROM faq_entries WHERE id = :i FOR UPDATE", i=faq_id)
        if not got:
            raise HTTPException(404, "no such FAQ")
        old = got[0]
        if body.status == "active" and old["source_doc"] != CURATED and not await rows(conn, "SELECT 1 FROM kb_documents WHERE doc_id = :d AND status = 'active'", d=old["source_doc"]):
            raise HTTPException(422, "its source document is no longer active, so this answer can't be approved")
        await conn.execute(text("UPDATE faq_entries SET question = :q, answer = :a, status = :s WHERE id = :i"),
                           {"q": body.question, "a": body.answer, "s": body.status, "i": faq_id})
        if body.question != old["question"]:
            def norm(t: str) -> str:
                return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t.lower())).strip()
            taken = await rows(conn, "SELECT faq_id FROM faq_variants WHERE normalized = :n AND faq_id <> :i", n=norm(body.question), i=faq_id)
            if taken:
                raise HTTPException(409, f"{taken[0]['faq_id']} already answers this question")
            await conn.execute(text("DELETE FROM faq_variants WHERE faq_id = :i AND normalized = :n"), {"i": faq_id, "n": norm(old["question"])})
            await conn.execute(text("INSERT INTO faq_variants (faq_id, text, normalized, embedding) VALUES (:i, :q, :n, CAST(:e AS vector)) "
                                    "ON CONFLICT (normalized) DO UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding"),
                               {"i": faq_id, "q": body.question, "n": norm(body.question), "e": _vec(vec)})
        await invalidate_faq(conn, faq_id)
        if body.status == "active":
            for v in await rows(conn, "SELECT text FROM faq_variants WHERE faq_id = :i", i=faq_id):
                try:
                    await write_answer(conn, v["text"], body.answer, [old["source_doc"]], faq_id=faq_id)
                except CacheWriteRejected as exc:
                    raise HTTPException(422, str(exc)) from exc
    return {"id": faq_id, "status": body.status}


# ---------- knowledge graph ----------

class Decision(BaseModel):
    decision: Literal["approve", "reject"]


@router.get("/kg/changes")
async def changes(_: AdminDep, engine: EngineDep, status: Literal["pending", "approved", "rejected", "applied", ""] = "pending") -> dict[str, Any]:
    async with engine.connect() as conn:
        return {"items": await rows(conn, "SELECT k.id, k.doc_id, k.chunk_id, k.op, k.payload, k.confidence, k.status, k.decided_by, k.decided_at, "
                                          "c.anchor FROM kg_change_requests k LEFT JOIN kb_chunks c ON c.id = k.chunk_id WHERE :s = '' OR "
                                          "k.status = :s ORDER BY k.confidence DESC NULLS LAST, k.id LIMIT 200", s=status)}


@router.post("/kg/changes/{change_id}/decision")
async def decide(change_id: uuid.UUID, body: Decision, staff: AdminDep, engine: EngineDep) -> dict[str, str]:
    status = "approved" if body.decision == "approve" else "rejected"
    async with engine.begin() as conn:
        got = await rows(conn, "SELECT op, payload, doc_id, chunk_id, status FROM kg_change_requests WHERE id = :i FOR UPDATE", i=change_id)
        if not got:
            raise HTTPException(404, "no such change")
        c = got[0]
        if c["status"] != "pending":
            raise HTTPException(409, f"already {c['status']}")
        if body.decision == "approve":
            try:                                    # statements are MERGEs: repeating one after a failed commit changes nothing
                await apply_change(get_write_driver(), c["op"], c["payload"] or {}, c["doc_id"] or "unknown", c["chunk_id"])
            except InvalidChange as exc:
                raise HTTPException(422, str(exc)) from exc
            await bump_kg_version(conn)
        await conn.execute(text("UPDATE kg_change_requests SET status = :s, decided_by = :b, decided_at = now() WHERE id = :i"),
                           {"s": status, "b": staff.email, "i": change_id})
    if body.decision == "approve":
        await refresh_rule_context()                # other processes follow within seconds
    return {"status": status}


_NODE = "n.id AS id, [l IN labels(n) WHERE l <> 'Entity'][0] AS label, coalesce(n.name, n.id) AS name, n.source AS source"


@router.get("/kg/search")
async def kg_search(_: AdminDep, q: Annotated[str, Query(min_length=1, max_length=80)]) -> dict[str, Any]:
    found, _ms = await read_query(get_read_driver(), f"MATCH (n:Entity) WHERE toLower(n.id) CONTAINS toLower($q) OR toLower(n.name) CONTAINS toLower($q) "
                                  f"OR toLower(n.display_text) CONTAINS toLower($q) RETURN {_NODE} ORDER BY size(n.id), n.id LIMIT 12",
                                  {"q": q.strip()}, database=get_settings().neo4j_database)
    return {"items": found}


@router.get("/kg/node/{node_id}")
async def kg_node(node_id: str, _: AdminDep, depth: Annotated[int, Query(ge=1, le=2)] = 1) -> dict[str, Any]:
    d, db = get_read_driver(), get_settings().neo4j_database
    me, _ms = await read_query(d, f"MATCH (n:Entity {{id: $id}}) RETURN {_NODE}, properties(n) AS props", {"id": node_id}, database=db)
    if not me:
        raise HTTPException(404, "no such node")
    limit = 40 if depth == 1 else 60
    near, _ms = await read_query(d, f"MATCH p = (c:Entity {{id: $id}})-[*1..{int(depth)}]-(n:Entity) WHERE n.id <> $id WITH n, min(length(p)) AS dist "
                                 f"ORDER BY dist, n.id LIMIT {limit + 1} RETURN {_NODE}, dist", {"id": node_id}, database=db)
    cut = len(near) > limit
    nodes = [{**me[0], "dist": 0}, *near[:limit]]
    edges, _ms = await read_query(d, "MATCH (a:Entity)-[r]->(b:Entity) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id AS src, type(r) AS type, "
                                  "b.id AS dst, r.source AS source", {"ids": [n["id"] for n in nodes]}, database=db)
    nodes[0]["props"] = json.loads(json.dumps(nodes[0]["props"], default=str))
    return {"nodes": nodes, "edges": edges, "truncated": cut, "as_of": dt.datetime.now(dt.UTC)}
