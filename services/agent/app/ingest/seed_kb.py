"""Seed the knowledge base from mock-data/data/kb (BS-07, `make seed-kb` / `scripts/seed.py --kb`).

Registers the 15 documents with their versions and statuses, enqueues `ingest_document` jobs and drains them
through the same handler the worker uses, then embeds FAQ variants that have no embedding yet. Re-runs skip
documents whose checksum is unchanged and already have chunks, so no embeddings are recomputed.
"""

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.pipeline import _vec, make_ingest_handler, register_document
from app.jobs.queue import drain, enqueue
from app.providers.base import Embedder

KB_DIR = Path(__file__).resolve().parents[4] / "mock-data" / "data" / "kb"
FAQ_BATCH = 64


def _date(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


async def seed_documents(engine: AsyncEngine, kb_dir: Path = KB_DIR) -> list[str]:
    queued = []
    docs = json.loads((kb_dir / "documents.json").read_text(encoding="utf-8"))
    # superseded versions first, so the active one wins the one-active-version index
    for d in sorted(docs, key=lambda d: d["status"] == "active"):
        path = kb_dir.parent / d["path"]
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        async with engine.begin() as conn:
            prev = (await conn.execute(text(
                "SELECT d.checksum, (SELECT count(*) FROM kb_chunks c WHERE c.doc_id = d.doc_id AND c.version = d.version) "
                "FROM kb_documents d WHERE d.doc_id = :d AND d.version = :v"), {"d": d["doc_id"], "v": d["version"]})).first()
            await register_document(
                conn, doc_id=d["doc_id"], version=d["version"], title=d["title"], doc_type=d["doc_type"],
                family=d.get("family"), status=d["status"], effective_from=_date(d.get("effective_from")),
                effective_to=_date(d.get("effective_to")), supersedes=d.get("supersedes"),
                storage_uri=path.resolve().as_uri(), checksum=checksum, language=d.get("language", "en"),
                source="seed")
            if prev and prev[0] == checksum and prev[1] > 0:
                continue
            await enqueue(conn, "ingest_document", {"doc_id": d["doc_id"], "version": d["version"],
                                                    "filename": path.name})
            queued.append(d["doc_id"])
    return queued


async def embed_faq_variants(engine: AsyncEngine, embedder: Embedder) -> int:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, text FROM faq_variants WHERE embedding IS NULL ORDER BY id"))).all()
    for i in range(0, len(rows), FAQ_BATCH):
        batch = rows[i:i + FAQ_BATCH]
        # variants are questions matched against incoming questions: embed them as queries (symmetric)
        vecs = await embedder.embed([r.text for r in batch], kind="query")
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE faq_variants SET embedding = CAST(:e AS vector) WHERE id = :id"),
                               [{"id": r.id, "e": _vec(v)} for r, v in zip(batch, vecs, strict=True)])
    return len(rows)


async def seed_kb(engine: AsyncEngine, embedder: Embedder, kb_dir: Path = KB_DIR) -> dict[str, Any]:
    queued = await seed_documents(engine, kb_dir)
    ran = await drain(engine, {"ingest_document": make_ingest_handler(engine, embedder)})
    async with engine.connect() as conn:
        failed = (await conn.execute(text("SELECT count(*) FROM jobs WHERE kind = 'ingest_document' "
                                          "AND status IN ('failed', 'queued')"))).scalar_one()
        chunks = (await conn.execute(text("SELECT count(*) FROM kb_chunks"))).scalar_one()
    faqs = await embed_faq_variants(engine, embedder)
    return {"documents_queued": len(queued), "jobs_run": ran, "jobs_pending_or_failed": failed,
            "kb_chunks": chunks, "faq_variants_embedded": faqs}
