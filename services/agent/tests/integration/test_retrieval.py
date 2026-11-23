"""Hybrid retrieval against `mcs_test` (BS-08, LLD-8). Fake embeddings; the cross-encoder smoke test downloads the
ONNX model (network)."""

import asyncio
import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.ingest.seed_kb import seed_kb
from app.providers.fake import FakeProvider
from app.retrieval.hybrid import Filters, retrieve
from app.retrieval.rerank import CrossEncoderReranker, NoopReranker

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]
AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT.parents[1]
EMB = FakeProvider(dim=768)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set (run `make env` and `make proxy`)")
    for args in (("downgrade", "base"), ("upgrade", "head")):
        await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT,
                                check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    seed_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_mod)
    sync = create_engine(url)
    with sync.begin() as conn:
        seed_mod.seed(conn)
    sync.dispose()
    eng = create_async_engine(url)
    await seed_kb(eng, EMB)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def conn(engine):
    async with engine.connect() as c:
        yield c


class KeywordReranker:
    """Deterministic stand-in for the cross-encoder: counts query words in the passage."""
    name = "keyword"

    def score(self, query, documents):
        words = {w.strip("?.,").lower() for w in query.split() if len(w) > 2}
        return [float(sum(w in d.lower() for w in words)) for d in documents]


class SlowReranker:
    name = "slow"

    def score(self, query, documents):
        time.sleep(0.5)
        return [0.0] * len(documents)


class BrokenReranker:
    name = "broken"

    def score(self, query, documents):
        raise RuntimeError("model file missing")


Q_E04 = "What does error E04 mean on a DHD-150?"


@pytest.mark.req("FR-RG-01")
async def test_both_legs_contribute_and_fuse(conn):
    res = await retrieve(conn, EMB, "dew point high desiccant rotor", reranker=NoopReranker(), k_ctx=20)
    assert any(h.vector_rank for h in res.candidates) and any(h.lexical_rank for h in res.candidates)
    assert any(h.vector_rank and h.lexical_rank for h in res.candidates)   # fused, not just concatenated
    assert [h.rrf for h in res.candidates] == sorted((h.rrf for h in res.candidates), reverse=True)
    assert len(res.hits) <= 20 and res.rerank_status == "skipped"


@pytest.mark.req("FR-RG-01")
async def test_lexical_leg_matches_natural_questions(conn):
    res = await retrieve(conn, EMB, Q_E04, Filters(family="DHD", model="DHD-150"), reranker=NoopReranker())
    top5 = [h.evidence_id for h in res.hits[:5]]
    assert "DOC-MAN-DHD#e04-dew-point-high" in top5


@pytest.mark.req("FR-RG-03")
async def test_model_filter_excludes_other_family_manuals(conn):
    res = await retrieve(conn, EMB, "What does error E04 mean? pump overload dew point", Filters(family="DHD", model="DHD-150"),
                         reranker=NoopReranker(), k_ctx=50, rerank_top=50)
    manuals = {h.doc_id for h in res.candidates if h.doc_type == "manual"}
    assert manuals == {"DOC-MAN-DHD"}            # MTC also has an E04, but never shows up
    unfiltered = await retrieve(conn, EMB, "What does error E04 mean? pump overload dew point", reranker=NoopReranker())
    assert "DOC-MAN-MTC" in {h.doc_id for h in unfiltered.candidates}


@pytest.mark.req("FR-RG-03")
async def test_family_filter_keeps_family_less_policies_and_doc_type_filter(conn):
    res = await retrieve(conn, EMB, "warranty on desiccant rotor for my DHD-150", Filters(family="DHD"),
                         reranker=NoopReranker(), k_ctx=30)
    types = {h.doc_type for h in res.candidates}
    assert "policy" in types and {h.family for h in res.candidates if h.doc_type == "manual"} == {"DHD"}
    pol = await retrieve(conn, EMB, "warranty on desiccant rotor", Filters(doc_types=["policy"]), reranker=NoopReranker())
    assert pol.candidates and {h.doc_type for h in pol.candidates} == {"policy"}


@pytest.mark.req("FR-RG-06")
async def test_superseded_versions_only_with_past_cue(conn):
    now = await retrieve(conn, EMB, "Is the warranty 12 months from dispatch?", reranker=NoopReranker(), k_ctx=30)
    assert not now.include_superseded and "DOC-POL-WARRANTY-V1" not in {h.doc_id for h in now.candidates}
    assert "DOC-POL-WARRANTY-V2" in {h.doc_id for h in now.hits}
    past = await retrieve(conn, EMB, "What was the old warranty policy before 2025?", reranker=NoopReranker(), k_ctx=30)
    assert past.include_superseded and "DOC-POL-WARRANTY-V1" in {h.doc_id for h in past.candidates}
    forced = await retrieve(conn, EMB, "old warranty policy", Filters(include_superseded=False), reranker=NoopReranker())
    assert "DOC-POL-WARRANTY-V1" not in {h.doc_id for h in forced.candidates}


@pytest.mark.req("FR-RG-04")
async def test_reranker_reorders_and_cuts_to_k_ctx(conn):
    base = await retrieve(conn, EMB, "blower overload thermal relay", reranker=NoopReranker(), k_ctx=20)
    res = await retrieve(conn, EMB, "blower overload thermal relay", reranker=KeywordReranker(), k_ctx=6)
    assert res.rerank_status == "ok" and len(res.hits) == 6 and res.reranker == "keyword"
    scores = [h.rerank_score for h in res.hits]
    assert scores == sorted(scores, reverse=True)
    assert {h.chunk_id for h in res.hits} <= {h.chunk_id for h in base.candidates[:20]}


@pytest.mark.req("FR-RG-04")
async def test_slow_or_broken_reranker_falls_back_to_rrf_order(conn):
    base = await retrieve(conn, EMB, Q_E04, reranker=NoopReranker())
    slow = await retrieve(conn, EMB, Q_E04, reranker=SlowReranker(), rerank_timeout_ms=50)
    broken = await retrieve(conn, EMB, Q_E04, reranker=BrokenReranker())
    assert slow.rerank_status == "timeout" and broken.rerank_status == "error"
    assert [h.chunk_id for h in slow.hits] == [h.chunk_id for h in base.hits] == [h.chunk_id for h in broken.hits]


@pytest.mark.req("FR-RG-04")
async def test_cross_encoder_prefers_the_relevant_passage():
    enc = await asyncio.to_thread(CrossEncoderReranker, "Xenova/ms-marco-MiniLM-L-6-v2")
    scores = await asyncio.to_thread(enc.score, "dryer shows dew point high", [
        "Chiller > A01 High refrigerant pressure: condenser fouled, clean the condenser.",
        "Dehumidifying dryer > E04 Dew point high: desiccant rotor degraded, regeneration heater open.",
    ])
    assert scores[1] > scores[0]
