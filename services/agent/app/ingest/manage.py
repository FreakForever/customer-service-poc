"""Delete and re-index documents (LLD-13, FR-KB-04)."""

from pathlib import PurePath
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.cache.store import invalidate_document
from app.jobs.queue import Job, enqueue
from app.kg.changes import get_write_driver, remove_document_facts
from app.rules.context import bump_kg_version


async def delete_document(conn: AsyncConnection, doc_id: str) -> str | None:
    """Out of search and cache at once; the job removes chunks and graph facts. An older version or a superseded
    document isn't brought back."""
    n = (await conn.execute(text("UPDATE kb_documents SET status = 'deleted' WHERE doc_id = :d AND status <> 'deleted'"), {"d": doc_id})).rowcount
    if not n:
        return None
    await invalidate_document(conn, doc_id, "deleted")
    await conn.execute(text("UPDATE kg_change_requests SET status = 'rejected', decided_by = 'document deleted', decided_at = now() "
                            "WHERE doc_id = :d AND status = 'pending'"), {"d": doc_id})
    return str(await enqueue(conn, "delete_document", {"doc_id": doc_id}))


async def reindex_document(conn: AsyncConnection, doc_id: str) -> str | None:
    row = (await conn.execute(text("SELECT version, storage_uri FROM kb_documents WHERE doc_id = :d AND status = 'active'"), {"d": doc_id})).first()
    if row is None:
        return None
    return str(await enqueue(conn, "reindex_document", {"doc_id": doc_id, "version": row.version,
                                                        "filename": PurePath(unquote(urlparse(row.storage_uri or "").path)).name}))


def make_delete_handler(engine: AsyncEngine):
    async def handle(job: Job) -> dict[str, Any]:
        doc_id = job.payload["doc_id"]
        async with engine.connect() as conn:
            live = (await conn.execute(text("SELECT count(*) FROM kb_documents WHERE doc_id = :d AND status <> 'deleted'"), {"d": doc_id})).scalar_one()
        # uploaded again meanwhile: its facts now belong to the new version
        facts = await remove_document_facts(get_write_driver(), doc_id) if not live else {}
        async with engine.begin() as conn:
            n = (await conn.execute(text("DELETE FROM kb_chunks c USING kb_documents d WHERE c.doc_id = d.doc_id AND c.version = d.version AND "
                                         "d.doc_id = :d AND d.status = 'deleted'"), {"d": doc_id})).rowcount
            if not live:
                await invalidate_document(conn, doc_id, "deleted")
                await bump_kg_version(conn)
        return {"chunks": n, **facts}
    return handle
