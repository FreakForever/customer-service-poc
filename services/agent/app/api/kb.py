"""Public help-centre content (FR-UI-01, FR-UI-02; LLD-14). Read-only, no model calls. Articles are rebuilt from the
active version's chunks, so every citation anchor exists on the page."""

import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.chat import EngineDep

router = APIRouter(prefix="/v1/kb", tags=["kb"])
_DOC = "doc_id, version, title, doc_type, family, status, effective_from"


async def _rows(conn: AsyncConnection, sql: str, **p: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in (await conn.execute(text(sql), p)).mappings()]


@router.get("/home")
async def home(engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        popular = await _rows(conn, f"""
            SELECT {_DOC} FROM kb_documents d WHERE status = 'active' AND doc_type NOT IN ('bulletin', 'company')
            ORDER BY (SELECT coalesce(sum(hits), 0) FROM answer_cache a WHERE d.doc_id = ANY(a.source_docs)) DESC,
                     (SELECT count(*) FROM faq_entries f WHERE f.source_doc = d.doc_id) DESC, doc_id LIMIT 6""")
        bulletins = await _rows(conn, f"SELECT {_DOC} FROM kb_documents WHERE status = 'active' AND doc_type = 'bulletin' "
                                      "ORDER BY effective_from DESC, doc_id LIMIT 5")
        recent = await _rows(conn, f"SELECT {_DOC} FROM kb_documents WHERE status = 'active' ORDER BY effective_from DESC, doc_id LIMIT 4")
    return {"popular": popular, "bulletins": bulletins, "recent": recent}


@router.get("/families/{family}")
async def family(family: str, engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        models = await _rows(conn, "SELECT id, name, specs FROM models WHERE family = :f ORDER BY id", f=family)
        if not models:
            raise HTTPException(404, "unknown product family")
        docs = await _rows(conn, f"SELECT {_DOC} FROM kb_documents WHERE status = 'active' AND (family = :f OR family IS NULL) "
                                 "ORDER BY family NULLS LAST, doc_type, title", f=family)
        codes = await _rows(conn, """
            SELECT e.id, e.code, e.title, e.display_text, e.severity, e.safety_stop, c.doc_id, c.anchor FROM error_codes e
            LEFT JOIN LATERAL (SELECT c.doc_id, c.anchor FROM kb_chunks c JOIN kb_documents d USING (doc_id, version)
              WHERE d.status = 'active' AND c.metadata->'error_codes' ? e.id ORDER BY c.id DESC LIMIT 1) c ON true
            WHERE e.family = :f ORDER BY e.code""", f=family)
        parts = await _rows(conn, "SELECT part_no, name, fits_models FROM spare_parts WHERE family = :f ORDER BY part_no", f=family)
    return {"models": models, "documents": docs, "error_codes": codes, "parts": parts}


@router.get("/articles/{doc_id}")
async def article(doc_id: str, engine: EngineDep, version: Annotated[str | None, Query(max_length=20)] = None) -> dict[str, Any]:
    async with engine.connect() as conn:
        docs = await _rows(conn, f"SELECT {_DOC}, effective_to FROM kb_documents WHERE doc_id = :d AND status <> 'deleted' "
                                 "ORDER BY (status = 'active') DESC, effective_from DESC, created_at DESC", d=doc_id)
        doc = next((d for d in docs if version in (None, d["version"])), None)
        if doc is None:
            raise HTTPException(404, "article not found")
        sections = await _rows(conn, "SELECT heading_path, anchor, content FROM kb_chunks WHERE doc_id = :d AND version = :v ORDER BY ord",
                               d=doc_id, v=doc["version"])
        related = await _rows(conn, f"SELECT {_DOC} FROM kb_documents WHERE status = 'active' AND doc_id <> :d ORDER BY "
                                    "(family IS NOT DISTINCT FROM CAST(:f AS text)) DESC, (doc_type = :t) DESC, title LIMIT 4",
                              d=doc_id, f=doc["family"], t=doc["doc_type"])
        replaced = await _rows(conn, "SELECT doc_id, title FROM kb_documents WHERE supersedes = :d AND status = 'active' LIMIT 1", d=doc_id)
    return {"document": doc, "sections": sections, "related": related, "superseded_by": replaced[0] if replaced else None,
            "versions": [{"version": d["version"], "status": d["status"], "effective_from": d["effective_from"]} for d in docs]}


@router.get("/search")
async def search(engine: EngineDep, q: Annotated[str, Query(max_length=200)], limit: Annotated[int, Query(ge=1, le=30)] = 15) -> dict[str, Any]:
    """Error codes, article sections and FAQs by full text; the last word also matches as a prefix (typeahead)."""
    words = re.findall(r"[A-Za-z0-9]+", q)[:12]
    if not words:
        return {"items": []}
    tsq = " | ".join([*words, words[-1] + ":*"])
    p = {"ts": tsq, "words": [w.upper() for w in words], "n": limit}
    async with engine.connect() as conn:
        codes = await _rows(conn, """
            SELECT 'error_code' AS kind, e.family || ' ' || e.code || ' — ' || e.title AS title, 'Display: ' || e.display_text AS snippet,
              c.doc_id, c.anchor FROM error_codes e JOIN LATERAL (SELECT c.doc_id, c.anchor FROM kb_chunks c
                JOIN kb_documents d USING (doc_id, version) WHERE d.status = 'active' AND c.metadata->'error_codes' ? e.id
                ORDER BY c.id DESC LIMIT 1) c ON true
            WHERE upper(e.code) = ANY(:words) OR upper(e.display_text) = upper(:q)
            ORDER BY (e.family = ANY(:words)) DESC, e.id LIMIT 8""", q=q.strip(), **p)
        arts = await _rows(conn, """
            WITH q AS (SELECT to_tsquery('english', :ts) AS tq)
            SELECT 'article' AS kind, d.title || coalesce(' › ' || c.heading_path[array_upper(c.heading_path, 1)], '') AS title,
              ts_headline('english', c.content, q.tq, 'StartSel="", StopSel="", MaxWords=28, MinWords=12') AS snippet, d.doc_id, c.anchor
            FROM kb_chunks c JOIN kb_documents d USING (doc_id, version), q
            WHERE d.status = 'active' AND c.tsv @@ q.tq ORDER BY ts_rank_cd(c.tsv, q.tq) DESC, c.id LIMIT :n""", **p)
        faqs = await _rows(conn, """
            WITH q AS (SELECT to_tsquery('english', :ts) AS tq)
            SELECT 'faq' AS kind, question AS title, answer AS snippet, source_doc AS doc_id, NULL AS anchor FROM faq_entries f, q
            WHERE f.status = 'active' AND to_tsvector('english', question) @@ q.tq
            ORDER BY ts_rank_cd(to_tsvector('english', question), q.tq) DESC, id LIMIT 4""", **p)
    seen: set[tuple[Any, Any]] = set()
    items = []
    for r in [*codes, *faqs, *arts]:
        key = (r["doc_id"], r["anchor"]) if r["kind"] != "faq" else ("faq", r["title"])
        if key not in seen:
            seen.add(key)
            items.append({**r, "snippet": re.sub(r"[*`|#]+|\s+", " ", r["snippet"]).strip()[:240],
                          "url": (f"/a/{r['doc_id']}" + (f"#{r['anchor']}" if r["anchor"] else "")) if r["doc_id"] else None})
    return {"items": items[:limit]}
