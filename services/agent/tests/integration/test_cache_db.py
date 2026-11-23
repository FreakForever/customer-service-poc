"""FAQ caches against mcs_test (BS-14, LLD-7; FR-CA-01..04). Fake embeddings."""

import asyncio
import datetime as dt
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.cache.normalize import cache_key
from app.cache.store import (
    CacheWriteRejected,
    exact_lookup,
    invalidate_document,
    semantic_lookup,
    write_answer,
)
from app.config.settings import Settings
from app.ingest.pipeline import register_document
from app.ingest.seed_kb import seed_kb
from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]
AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT.parents[1]
EMB = FakeProvider(dim=768)
FAQS = json.loads((ROOT / "mock-data" / "data" / "kb" / "faqs.json").read_text())
SEED: dict = {}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    for args in (("downgrade", "base"), ("upgrade", "head")):
        await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT,
                                check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sync = create_engine(url)
    with sync.begin() as c:
        mod.seed(c)
    sync.dispose()
    eng = create_async_engine(url)
    SEED.update(await seed_kb(eng, EMB))
    yield eng
    await eng.dispose()


async def q(engine, sql, **p):
    async with engine.connect() as c:
        return (await c.execute(text(sql), p)).all()


async def test_seed_fills_exact_cache_from_curated_faqs(engine):
    n_variants = sum(1 + len(f["paraphrases"]) for f in FAQS)
    assert SEED["answer_cache_rows"] == n_variants
    rows = await q(engine, "SELECT count(*), count(DISTINCT answer->>'faq_id') FROM answer_cache")
    assert rows[0][1] == len(FAQS) and rows[0][0] <= n_variants      # normalized duplicates collapse


@pytest.mark.req("FR-CA-01")
async def test_exact_hit_under_20ms_server_time(engine):
    async with engine.begin() as c:
        hit = await exact_lookup(c, "Hi, how long is the warranty??")
        assert hit and hit.layer == "exact" and hit.faq_id == "FAQ-001" and hit.source_docs == ["DOC-POL-WARRANTY-V2"]
        plan = (await c.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) SELECT answer, source_docs FROM answer_cache "
                                     "WHERE key = :k AND (expires_at IS NULL OR expires_at > now())"),
                                {"k": cache_key("how long is the warranty")})).scalar()
        assert plan[0]["Execution Time"] < 20
        assert await exact_lookup(c, "how long is the warranty on a moon rover") is None


@pytest.mark.req("FR-CA-02")
async def test_semantic_hit_threshold_and_guard(engine):
    variant = FAQS[4]["paraphrases"][0]
    qvec = (await EMB.embed([variant], kind="query"))[0]
    async with engine.connect() as c:
        hit = await semantic_lookup(c, qvec, 0.90, intent="faq.amc")
        assert hit and hit.faq_id == FAQS[4]["id"] and hit.similarity > 0.99
        assert await semantic_lookup(c, qvec, 0.90, has_account_ids=True) is None
        assert await semantic_lookup(c, qvec, 0.90, intent="amc.status") is None
        other = (await EMB.embed(["granulator blade sharpening torque values"], kind="query"))[0]
        assert await semantic_lookup(c, other, 0.90) is None


@pytest.mark.req("FR-CA-03")
async def test_superseding_a_policy_removes_cached_answers_that_cite_it(engine):
    before = (await q(engine, "SELECT count(*) FROM answer_cache WHERE 'DOC-POL-WARRANTY-V2' = ANY(source_docs)"))[0][0]
    assert before > 0
    async with engine.begin() as c:
        await register_document(c, doc_id="DOC-POL-WARRANTY-V3", version="3.0", title="Warranty v3", doc_type="policy",
                                supersedes="DOC-POL-WARRANTY-V2", effective_from=dt.date(2026, 12, 1))
    assert (await q(engine, "SELECT count(*) FROM answer_cache WHERE 'DOC-POL-WARRANTY-V2' = ANY(source_docs)"))[0][0] == 0
    status = {r[0] for r in await q(engine, "SELECT status FROM faq_entries WHERE source_doc = 'DOC-POL-WARRANTY-V2'")}
    assert status == {"needs_review"}
    async with engine.connect() as c:                       # still served semantically until reviewed
        qvec = (await EMB.embed([FAQS[0]["question"]], kind="query"))[0]
        assert (await semantic_lookup(c, qvec, 0.90)).meta["status"] == "needs_review"
        assert await exact_lookup(c, FAQS[0]["question"]) is None


@pytest.mark.req("FR-CA-03")
async def test_deleted_source_withdraws_faqs(engine):
    async with engine.begin() as c:
        out = await invalidate_document(c, "DOC-POL-PARTS", "deleted")
    assert out["answer_cache_deleted"] > 0 and out["faqs_flagged"] > 0
    parts = next(f for f in FAQS if f["source_doc"] == "DOC-POL-PARTS")
    async with engine.connect() as c:
        qvec = (await EMB.embed([parts["question"]], kind="query"))[0]
        hit = await semantic_lookup(c, qvec, 0.90)
        assert hit is None or hit.faq_id != parts["id"]


@pytest.mark.req("FR-CA-04")
async def test_writer_guard_and_ttl(engine):
    async with engine.begin() as c:
        with pytest.raises(CacheWriteRejected):
            await write_answer(c, "is my dryer in warranty", "Yes", ["DOC-POL-WARRANTY-V2"], customer_scoped=True)
        await write_answer(c, "do you have a training centre", "Yes, in Ahmedabad.", ["DOC-CO-OVERVIEW"],
                           origin="promoted", ttl_days=30)
        hit = await exact_lookup(c, "Do you have a training centre?")
        assert hit and hit.answer == "Yes, in Ahmedabad."
