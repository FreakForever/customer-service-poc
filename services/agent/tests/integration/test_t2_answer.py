"""T2 answers through the real graph front and retrieval on mcs_test (BS-15; FR-RG-05, FR-OR-03).
The model is scripted; `make eval-t2` checks real-model abstention and citations."""

import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from app.agents.rag_answerer import ABSTAIN
from app.config.settings import Settings
from app.ingest.seed_kb import seed_kb
from app.obs.tracing import TraceRecorder
from app.orchestrator.graph import build_graph, run_turn
from app.orchestrator.nodes import cache as cache_node
from app.orchestrator.nodes import rag as rag_node
from app.orchestrator.policies import get_policy
from app.orchestrator.state import Envelope, Intent
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.rerank import NoopReranker

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module")]
AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT.parents[1]
EMB = FakeProvider(dim=768)
UPLOAD = [q for q in map(json.loads, (ROOT / "mock-data/data/eval/questions.jsonl").open())
          if q["type"] == "requires_upload"]


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
    await seed_kb(eng, EMB)
    yield eng
    await eng.dispose()


async def turn(engine, monkeypatch, text, reply, intent="faq.warranty", fail=0):
    fake = FakeProvider(script={"t2.v1": reply}).fail_next(fail)
    for mod in (cache_node, rag_node):
        monkeypatch.setattr(mod, "get_engine", lambda: engine)
        monkeypatch.setattr(mod, "get_embedder", lambda: EMB)
    monkeypatch.setattr(rag_node, "get_llm", lambda: ResilientLLM(fake))
    monkeypatch.setattr(rag_node, "get_reranker", NoopReranker)

    async def classify(state):
        qvec = (await EMB.embed([state["env"].text], kind="query"))[0]
        return {"intents": [Intent(name=intent, confidence=0.95, source="knn")], "query_embedding": qvec}

    # a remote test database is slower than the chat budget allows for
    monkeypatch.setattr("app.orchestrator.graph.get_policy", lambda ch: get_policy("email").model_copy(
        update={"name": ch}))
    rec = TraceRecorder()
    final = await run_turn(Envelope(channel="chat", text=text), graph=build_graph({"classify": classify}), recorder=rec)
    return final, rec, fake


@pytest.mark.req("FR-OR-03")
async def test_cascade_t1_then_t2_records_tier(engine, monkeypatch):
    hit, rec, fake = await turn(engine, monkeypatch, "What is the warranty period on Matsui equipment?", "x [E1]")
    assert hit["answered_tier"] == rec.root["answered_tier"] == "T1" and fake.calls == []
    miss, rec, fake = await turn(engine, monkeypatch, "Does dust damage void the warranty on electrical parts?",
                                 "It is excluded [E1].")
    assert miss["path"][-5:] == ["cache_lookup", "rag_answer", "verify", "decide", "respond"]
    assert miss["answered_tier"] == rec.root["answered_tier"] == "T2" and len(fake.calls) == 1


@pytest.mark.req("FR-RG-05")
async def test_citations_resolve_to_retrieved_chunks(engine, monkeypatch):
    final, _, _ = await turn(engine, monkeypatch, "Does dust damage void the warranty on electrical parts?",
                             "It is excluded [E1].")
    cite = final["response"]["citations"][0]
    assert cite["evidence_id"] == final["evidence"][0].citation == f"{cite['doc_id']}#{cite['anchor']}"
    assert "DOC-POL-WARRANTY-V1" not in {e.doc_id for e in final["evidence"]}


@pytest.mark.req("FR-RG-05")
@pytest.mark.parametrize("q", UPLOAD, ids=lambda q: q["id"])
async def test_requires_upload_abstains_before_upload(engine, monkeypatch, q):
    final, _, _ = await turn(engine, monkeypatch, q["question"], ABSTAIN, intent="amc.renewal")
    assert final["draft"].abstained and final["response"]["text"] == ABSTAIN
    assert "rag_answer" in final["path"] and "planner" in final["path"]      # abstention escalates to T3
    unseen = {"Q-180": "Rotor drive motor stall", "Q-181": "MT-DHD-ROTOR-R2"}.get(q["id"])
    assert unseen is None or not any(unseen in e.text for e in final["evidence"])


async def test_model_outage_abstains_and_keeps_evidence(engine, monkeypatch):
    final, rec, _ = await turn(engine, monkeypatch, "Does dust damage void the warranty?", "x [E1]", fail=5)
    assert final["draft"].abstained and final["evidence"] and "[stub]" not in final["response"]["text"]
    assert next(s for s in rec.spans if s.node == "rag_answer").status == "error"
