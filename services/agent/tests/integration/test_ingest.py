"""Ingestion pipeline, job queue and versioning against `mcs_test` (BS-07; FakeProvider embeddings, no model calls)."""

import asyncio
import datetime as dt
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.ingest import pipeline
from app.ingest.parsers import UnsupportedDocument
from app.ingest.seed_kb import seed_kb
from app.jobs.queue import MAX_ATTEMPTS, claim, drain, enqueue, run_once
from app.providers.fake import FakeProvider, hash_embedding

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]
AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT.parents[1]
SB = ROOT / "mock-data" / "upload-samples" / "SB-2026-12_DHD_rotor_revision.md"
DOCS = json.loads((ROOT / "mock-data" / "data" / "kb" / "documents.json").read_text())
EMB = FakeProvider(dim=768)
FIRST_SEED: dict = {}


def _url():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set (run `make env` and `make proxy`)")
    return url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    url = _url()
    for args in (("downgrade", "base"), ("upgrade", "head")):
        await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT,
                                check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    seed_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_mod)
    sync = create_engine(url)
    with sync.begin() as conn:   # ops/catalog/FAQ rows (models table feeds chunk metadata)
        seed_mod.seed(conn)
    sync.dispose()
    eng = create_async_engine(url)
    FIRST_SEED.update(await seed_kb(eng, EMB))
    yield eng
    await eng.dispose()


async def q(engine, sql, **p):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), p)).all()


# ---------- seed (15 docs, versions) ----------

async def test_seed_ingests_all_documents_through_jobs(engine):
    first = FIRST_SEED
    assert first["documents_queued"] == len(DOCS) == first["jobs_run"] and first["jobs_pending_or_failed"] == 0
    assert first["faq_variants_embedded"] > 100
    per_doc = {r[0]: r[1] for r in await q(engine, "SELECT doc_id, count(*) FROM kb_chunks GROUP BY doc_id")}
    assert set(per_doc) == {d["doc_id"] for d in DOCS} and all(n > 0 for n in per_doc.values())
    coded = (await q(engine, "SELECT count(*) FROM kb_chunks WHERE jsonb_array_length(metadata->'error_codes') > 0"))[0][0]
    assert coded == 44
    assert (await q(engine, "SELECT count(*) FROM kb_chunks WHERE embedding IS NULL"))[0][0] == 0
    assert (await q(engine, "SELECT count(*) FROM faq_variants WHERE embedding IS NULL"))[0][0] == 0


@pytest.mark.req("FR-KB-05")
async def test_seed_warranty_v1_superseded_v2_active(engine):
    rows = {r.doc_id: r for r in await q(engine, "SELECT doc_id, status, effective_from, effective_to, supersedes "
                                                 "FROM kb_documents WHERE doc_id LIKE 'DOC-POL-WARRANTY-%'")}
    assert rows["DOC-POL-WARRANTY-V1"].status == "superseded" and str(rows["DOC-POL-WARRANTY-V1"].effective_to) == "2025-03-31"
    assert rows["DOC-POL-WARRANTY-V2"].status == "active" and rows["DOC-POL-WARRANTY-V2"].supersedes == "DOC-POL-WARRANTY-V1"
    status = {r[0] for r in await q(engine, "SELECT DISTINCT metadata->>'status' FROM kb_chunks "
                                            "WHERE doc_id = 'DOC-POL-WARRANTY-V1'")}
    assert status == {"superseded"}


async def test_reseed_skips_unchanged_documents(engine):
    again = await seed_kb(engine, EMB)
    assert again["documents_queued"] == 0 and again["jobs_run"] == 0 and again["faq_variants_embedded"] == 0


async def test_chunks_are_searchable_by_fts_and_vector(engine):
    hit = await q(engine, "SELECT doc_id, anchor FROM kb_chunks WHERE tsv @@ websearch_to_tsquery('english', 'dew point high') "
                          "AND metadata->>'family' = 'DHD' ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', 'dew point high')) DESC LIMIT 1")
    assert hit[0].doc_id == "DOC-MAN-DHD" and hit[0].anchor == "e04-dew-point-high"
    qv = pipeline._vec(hash_embedding("Dehumidifying dryer E04 Dew point high desiccant rotor", 768))
    near = await q(engine, "SELECT doc_id FROM kb_chunks ORDER BY embedding <=> CAST(:v AS vector) LIMIT 3", v=qv)
    assert "DOC-MAN-DHD" in {r.doc_id for r in near}


# ---------- runtime upload (FR-KB-01) and versioning (FR-KB-05) ----------

@pytest.mark.req("FR-KB-01")
async def test_upload_is_searchable_within_60s(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "LOCAL_UPLOADS", tmp_path)
    start = time.perf_counter()
    async with engine.begin() as conn:
        sub = await pipeline.submit_document(conn, filename=SB.name, data=SB.read_bytes(),
                                             title="SB-2026-12 DHD rotor revision", doc_type="bulletin", family="DHD")
    job = await run_once(engine, {"ingest_document": pipeline.make_ingest_handler(engine, EMB)})
    assert job is not None and str(job.id) == sub["job_id"]
    hit = await q(engine, "SELECT c.anchor, d.status FROM kb_chunks c JOIN kb_documents d USING (doc_id, version) "
                          "WHERE c.tsv @@ websearch_to_tsquery('english', 'rotor stall') AND c.doc_id = :d", d=sub["doc_id"])
    elapsed = time.perf_counter() - start
    assert hit and hit[0].status == "active" and elapsed < 60
    assert sub["doc_id"] == "DOC-SB-2026-12-DHD-ROTOR-REVISION" and sub["version"] == "1.0"
    assert sub["storage_uri"].startswith("file://") and Path(sub["storage_uri"][7:]).exists()
    res = (await q(engine, "SELECT status, result FROM jobs WHERE id = :i", i=job.id))[0]
    assert res.status == "done" and res.result["error_code_chunks"] == 1


@pytest.mark.req("FR-KB-05")
async def test_new_version_supersedes_previous(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "LOCAL_UPLOADS", tmp_path)
    handlers = {"ingest_document": pipeline.make_ingest_handler(engine, EMB)}
    for body in ("# Policy\n\n## Rule\n\nOld rule.", "# Policy\n\n## Rule\n\nNew rule."):
        async with engine.begin() as conn:
            await pipeline.submit_document(conn, filename="returns-policy.md", data=body.encode(),
                                           title="Returns", doc_type="policy")
        await drain(engine, handlers)
    rows = await q(engine, "SELECT version, status, effective_to FROM kb_documents WHERE doc_id = 'DOC-RETURNS-POLICY' "
                           "ORDER BY version")
    assert [(r.version, r.status) for r in rows] == [("1.0", "superseded"), ("2.0", "active")]
    assert rows[0].effective_to == dt.date.today() and rows[1].effective_to is None
    meta = await q(engine, "SELECT version, metadata->>'status' AS s FROM kb_chunks WHERE doc_id = 'DOC-RETURNS-POLICY' "
                           "ORDER BY version")
    assert [(m.version, m.s) for m in meta] == [("1.0", "superseded"), ("2.0", "active")]


@pytest.mark.req("FR-KB-05")
async def test_upload_superseding_another_document(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "LOCAL_UPLOADS", tmp_path)
    async with engine.begin() as conn:
        await pipeline.register_document(conn, doc_id="DOC-SLA-OLD", version="1.0", title="SLA old", doc_type="policy",
                                         effective_from=dt.date(2024, 1, 1))
        await pipeline.submit_document(conn, filename="sla-2027.md", data=b"# SLA 2027\n\n## Times\n\nFaster.",
                                       title="SLA 2027", doc_type="policy", supersedes="DOC-SLA-OLD")
    old = (await q(engine, "SELECT status, effective_to FROM kb_documents WHERE doc_id = 'DOC-SLA-OLD'"))[0]
    assert old.status == "superseded" and old.effective_to == dt.date.today()


async def test_unsupported_upload_is_rejected(engine):
    async with engine.begin() as conn:
        with pytest.raises(UnsupportedDocument):
            await pipeline.submit_document(conn, filename="prices.xlsx", data=b"x", title="x", doc_type="policy")


# ---------- job queue ----------

async def test_failing_job_retries_with_backoff_then_fails(engine):
    calls = []

    async def boom(job):
        calls.append(job.attempts)
        raise RuntimeError("parser exploded")

    async with engine.begin() as conn:
        jid = await enqueue(conn, "test_boom", {})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert (await run_once(engine, {"test_boom": boom})) is not None
        row = (await q(engine, "SELECT status, attempts, error, run_after > now() AS later FROM jobs WHERE id = :i", i=jid))[0]
        assert row.attempts == attempt and "parser exploded" in row.error
        if attempt < MAX_ATTEMPTS:
            assert row.status == "queued" and row.later   # backoff: not claimable yet
            assert await run_once(engine, {"test_boom": boom}) is None
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": jid})
        else:
            assert row.status == "failed"
    assert calls == [1, 2, 3]


async def test_concurrent_claims_skip_locked_rows(engine):
    async with engine.begin() as conn:
        ids = {await enqueue(conn, "test_pair", {"n": n}) for n in range(2)}
    async with engine.connect() as a, engine.connect() as b:
        await a.begin()
        await b.begin()
        ja, jb = await asyncio.gather(claim(a, ["test_pair"]), claim(b, ["test_pair"]))
        assert ja and jb and ja.id != jb.id and {ja.id, jb.id} == ids
        await a.rollback()
        await b.rollback()
