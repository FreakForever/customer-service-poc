"""Document ingestion (LLD-13 upload flow, LLD-8 chunking; FR-KB-01, FR-KB-05, FR-RG-02).

submit_document: store file → kb_documents (active; supersede the previous version) → job `ingest_document`.
handle_ingest_document (worker): read file → parse → chunk → embed (batches of 64) → replace the doc's chunks.
The HTTP route `POST /v1/admin/documents` (staff auth) calls submit_document; it lands with the admin UI (BS-26).
"""

import datetime as dt
import hashlib
import json
import re
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.ingest.chunker import Chunk, chunk_markdown
from app.ingest.parsers import SUPPORTED, UnsupportedDocument, parse_document
from app.jobs.queue import Job, enqueue
from app.providers.base import Embedder

EMBED_BATCH = 64
LOCAL_UPLOADS = Path(__file__).resolve().parents[2] / ".uploads"  # dev fallback when GCS_BUCKET is empty


# ---------- storage ----------

def store_file(doc_id: str, version: str, filename: str, data: bytes, bucket: str = "") -> str:
    name = f"kb/{doc_id}/{version}/{PurePath(filename).name}"
    if bucket:
        from google.cloud import storage

        storage.Client().bucket(bucket).blob(name).upload_from_string(data)
        return f"gs://{bucket}/{name}"
    path = LOCAL_UPLOADS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.as_uri()


def read_file(uri: str) -> bytes:
    u = urlparse(uri)
    if u.scheme == "gs":
        from google.cloud import storage

        return storage.Client().bucket(u.netloc).blob(u.path.lstrip("/")).download_as_bytes()
    if u.scheme == "file":
        return Path(u.path).read_bytes()
    raise ValueError(f"unsupported storage uri {uri!r}")


# ---------- documents and versions ----------

def doc_id_from_filename(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", PurePath(filename).stem).strip("-").upper()
    return stem if stem.startswith("DOC-") else f"DOC-{stem}"


async def next_version(conn: AsyncConnection, doc_id: str) -> str:
    versions = (await conn.execute(text("SELECT version FROM kb_documents WHERE doc_id = :d"), {"d": doc_id})).scalars()
    majors = [int(m.group(1)) for v in versions if (m := re.match(r"(\d+)", v))]
    return f"{max(majors) + 1}.0" if majors else "1.0"


async def supersede(conn: AsyncConnection, doc_id: str, *, except_version: str | None,
                    effective_to: dt.date) -> int:
    """Mark the active version(s) of doc_id superseded; effective_to defaults to the given date (FR-KB-05)."""
    rows = (await conn.execute(text("""
        UPDATE kb_documents SET status = 'superseded', effective_to = coalesce(effective_to, :to)
        WHERE doc_id = :d AND status = 'active' AND (CAST(:v AS text) IS NULL OR version <> :v)
        RETURNING version"""), {"d": doc_id, "v": except_version, "to": effective_to})).scalars().all()
    if rows:
        await conn.execute(text("""UPDATE kb_chunks SET metadata = jsonb_set(coalesce(metadata, '{}'), '{status}', '"superseded"')
                                   WHERE doc_id = :d AND version = ANY(:vs)"""), {"d": doc_id, "vs": list(rows)})
    return len(rows)


async def register_document(conn: AsyncConnection, *, doc_id: str, version: str, title: str, doc_type: str,
                            family: str | None = None, status: str = "active",
                            effective_from: dt.date | None = None, effective_to: dt.date | None = None,
                            supersedes: str | None = None, storage_uri: str | None = None,
                            checksum: str | None = None, language: str = "en", source: str = "upload") -> None:
    if status == "active":
        cutover = effective_from or dt.date.today()
        # a new version of the same document, and/or the document it explicitly supersedes
        await supersede(conn, doc_id, except_version=version, effective_to=cutover)
        if supersedes and supersedes != doc_id:
            await supersede(conn, supersedes, except_version=None, effective_to=cutover)
    await conn.execute(text("""
        INSERT INTO kb_documents (doc_id, version, title, doc_type, family, status, effective_from, effective_to,
                                  supersedes, storage_uri, checksum, language, source)
        VALUES (:doc_id, :version, :title, :doc_type, :family, :status, :effective_from, :effective_to,
                :supersedes, :storage_uri, :checksum, :language, :source)
        ON CONFLICT (doc_id, version) DO UPDATE SET title = EXCLUDED.title, doc_type = EXCLUDED.doc_type,
          family = EXCLUDED.family, status = EXCLUDED.status, effective_from = EXCLUDED.effective_from,
          effective_to = EXCLUDED.effective_to, supersedes = EXCLUDED.supersedes,
          storage_uri = EXCLUDED.storage_uri, checksum = EXCLUDED.checksum, language = EXCLUDED.language,
          source = EXCLUDED.source"""),
        {"doc_id": doc_id, "version": version, "title": title, "doc_type": doc_type, "family": family,
         "status": status, "effective_from": effective_from or dt.date.today(), "effective_to": effective_to,
         "supersedes": supersedes, "storage_uri": storage_uri, "checksum": checksum, "language": language,
         "source": source})


async def submit_document(conn: AsyncConnection, *, filename: str, data: bytes, title: str, doc_type: str,
                          family: str | None = None, supersedes: str | None = None, doc_id: str | None = None,
                          bucket: str = "") -> dict[str, Any]:
    """Runtime upload (FR-KB-01): validate, store, register (active, superseding), enqueue ingestion."""
    if PurePath(filename).suffix.lower() not in SUPPORTED:
        raise UnsupportedDocument(f"unsupported file type: {filename}")
    doc_id = doc_id or doc_id_from_filename(filename)
    version = await next_version(conn, doc_id)
    uri = store_file(doc_id, version, filename, data, bucket)
    await register_document(conn, doc_id=doc_id, version=version, title=title, doc_type=doc_type, family=family,
                            supersedes=supersedes, storage_uri=uri, checksum=hashlib.sha256(data).hexdigest())
    job_id = await enqueue(conn, "ingest_document", {"doc_id": doc_id, "version": version, "filename": filename})
    return {"doc_id": doc_id, "version": version, "job_id": str(job_id), "storage_uri": uri}


# ---------- chunks ----------

def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def embed_chunks(embedder: Embedder, chunks: list[Chunk]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        out.extend(await embedder.embed([c.embed_text for c in chunks[i:i + EMBED_BATCH]], kind="document"))
    if len(out) != len(chunks):
        raise RuntimeError(f"embedder returned {len(out)} vectors for {len(chunks)} chunks")
    return out


async def replace_chunks(conn: AsyncConnection, doc_id: str, version: str, chunks: list[Chunk],
                         vectors: list[list[float]]) -> int:
    await conn.execute(text("DELETE FROM kb_chunks WHERE doc_id = :d AND version = :v"), {"d": doc_id, "v": version})
    if chunks:
        await conn.execute(text("""
            INSERT INTO kb_chunks (doc_id, version, ord, heading_path, anchor, content, token_count, metadata, embedding)
            VALUES (:d, :v, :ord, :hp, :anchor, :content, :tokens, CAST(:meta AS jsonb), CAST(:emb AS vector))"""),
            [{"d": doc_id, "v": version, "ord": c.ord, "hp": c.heading_path, "anchor": c.anchor,
              "content": c.content, "tokens": c.token_count, "meta": json.dumps(c.metadata), "emb": _vec(e)}
             for c, e in zip(chunks, vectors, strict=True)])
    return len(chunks)


async def known_models(conn: AsyncConnection) -> set[str] | None:
    ids = set((await conn.execute(text("SELECT id FROM models"))).scalars())
    return ids or None


async def ingest_markdown(engine: AsyncEngine, embedder: Embedder, doc_id: str, version: str,
                          markdown: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        doc = (await conn.execute(text("SELECT doc_id, version, title, doc_type, family, status FROM kb_documents "
                                       "WHERE doc_id = :d AND version = :v"), {"d": doc_id, "v": version})).first()
        if doc is None:
            raise LookupError(f"kb_documents row {doc_id}@{version} not found")
        models = await known_models(conn)
    chunks = chunk_markdown(markdown, dict(doc._mapping), known_models=models)
    vectors = await embed_chunks(embedder, chunks)  # outside the transaction: no locks held during model calls
    async with engine.begin() as conn:
        n = await replace_chunks(conn, doc_id, version, chunks, vectors)
    return {"doc_id": doc_id, "version": version, "chunks": n,
            "error_code_chunks": sum(1 for c in chunks if c.metadata["error_codes"])}


def make_ingest_handler(engine: AsyncEngine, embedder: Embedder):
    async def handle_ingest_document(job: Job) -> dict[str, Any]:
        doc_id, version = job.payload["doc_id"], job.payload["version"]
        async with engine.connect() as conn:
            uri = (await conn.execute(text("SELECT storage_uri FROM kb_documents WHERE doc_id = :d AND version = :v"),
                                      {"d": doc_id, "v": version})).scalar()
        if not uri:
            raise LookupError(f"no storage_uri for {doc_id}@{version}")
        markdown = parse_document(job.payload.get("filename") or PurePath(urlparse(uri).path).name, read_file(uri))
        return await ingest_markdown(engine, embedder, doc_id, version, markdown)
    return handle_ingest_document
