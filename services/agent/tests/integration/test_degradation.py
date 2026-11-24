"""Fault injection and channel policies through the real graph (BS-19; FR-OR-04, FR-OR-06). mcs_test + live graph;
the model and the classifier are scripted."""

import asyncio
import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings, get_settings
from app.db import get_engine, warm_pool
from app.ingest.seed_kb import seed_kb
from app.kg.client import get_read_driver
from app.obs.tracing import TraceRecorder
from app.orchestrator import graph as g
from app.orchestrator.budget import allocate_s
from app.orchestrator.graph import build_graph, run_turn
from app.orchestrator.nodes import agents as agents_node
from app.orchestrator.nodes import degrade as degrade_node
from app.orchestrator.nodes import rag as rag_node
from app.orchestrator.nodes import verify as verify_node
from app.orchestrator.policies import DEFAULT_PATH, get_policy, load_policies
from app.orchestrator.state import CustomerRef, Envelope, Intent
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.rerank import NoopReranker

pytestmark = pytest.mark.network
AGENT = Path(__file__).resolve().parents[2]
ASK = "Does dust damage void the warranty on electrical parts?"


@pytest.fixture(scope="module")
def url():
    s = Settings()
    u = os.environ.get("DATABASE_URL_TEST") or s.database_url_test.get_secret_value()
    if not u or not s.neo4j_uri:
        pytest.skip("DATABASE_URL_TEST / NEO4J_* not set")
    for a in (("downgrade", "base"), ("upgrade", "head")):
        subprocess.run(["uv", "run", "alembic", "-x", f"url={u}", *a], cwd=AGENT, check=True, capture_output=True)
    spec = importlib.util.spec_from_file_location("seed_script", AGENT.parents[1] / "scripts" / "seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = create_engine(u)
    with eng.begin() as c:
        mod.seed(c)
    eng.dispose()

    async def kb():
        aeng = create_async_engine(u)
        await seed_kb(aeng, FakeProvider(dim=768))
        await aeng.dispose()
    asyncio.run(kb())
    return u


@pytest_asyncio.fixture
async def ask(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    for f in (get_settings, get_engine, get_read_driver):
        f.cache_clear()
    await warm_pool()
    await get_read_driver().verify_connectivity()
    for mod in (rag_node, agents_node):
        monkeypatch.setattr(mod, "get_reranker", NoopReranker)

    async def _ask(text=ASK, intent="faq.warranty", channel="chat", llm=None, overrides=None, customer=None):
        async def classify(state):
            return {} if state.get("intents") else {"intents": [Intent(name=intent, confidence=0.95, source="knn")]}
        provider = ResilientLLM(llm or FakeProvider(script={"t2.v1": "It is excluded [E1]."}))
        for mod in (rag_node, agents_node, verify_node):
            monkeypatch.setattr(mod, "get_llm", lambda: provider)
        rec = TraceRecorder()
        f = await run_turn(Envelope(channel=channel, text=text, customer=customer), recorder=rec,
                           graph=build_graph({"classify": classify, **(overrides or {})}))
        ms = rec.latency_ms
        r = f["response"]
        assert r["text"] and "[stub]" not in r["text"] and f["path"][-1] == "respond", f["path"]
        return f, ms

    yield _ask
    await get_engine().dispose()
    await get_read_driver().close()
    get_read_driver.cache_clear()


def total(channel="chat"):
    return get_policy(channel).budget_ms["total"]


class Hang(FakeProvider):
    async def complete(self, *a, **k):
        await asyncio.sleep(60)


# ---------- faults ----------

@pytest.mark.req("FR-OR-06")
async def test_llm_timeout_gives_a_handoff_message_within_budget(ask):
    f, ms = await ask(llm=Hang())
    assert f["response"]["handoff"]["action"] == "handoff" and ms <= total() + 250, ms
    assert f["answered_tier"] == "T4" and f["evidence"]          # what was found is kept for the human


@pytest.mark.req("FR-OR-06")
@pytest.mark.parametrize("channel", ["chat", "email"])
async def test_llm_errors(ask, channel):
    f, ms = await ask(llm=FakeProvider().fail_next(99), channel=channel)
    want = get_policy(channel).on_low_confidence
    assert f["response"]["handoff"]["action"] == want and ms < total(channel)


async def test_slow_or_broken_summary_does_not_delay_the_handoff(ask):
    class SlowSummary(FakeProvider):
        async def complete(self, messages, **k):
            if messages[0].prompt_id == "summary.v1":
                await asyncio.sleep(60)
            return await super().complete(messages, **k)
    f, ms = await ask(intent="human", llm=SlowSummary())
    assert f["handoff"].summary and ms < total()


@pytest.mark.req("FR-OR-06")
async def test_database_down(ask, monkeypatch):
    class Down:
        def connect(self):
            raise ConnectionError("db down")
        begin = connect
    for mod in ("cache", "rag", "agents", "tools"):
        monkeypatch.setattr(f"app.orchestrator.nodes.{mod}.get_engine", Down)
    for text, intent in ((ASK, "faq.warranty"), ("status of SR-2026-00101", "x"), ("my CHL-10W shows LP TRIP", "troubleshoot.error_code")):
        f, _ = await ask(text, intent, customer=CustomerRef(customer_id="CUST-0005"))
        assert f["response"]["handoff"]["action"] == "handoff", (text, f["path"])


@pytest.mark.req("FR-OR-06")
async def test_graph_down(ask, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("neo4j down")
    for mod in ("app.agents.troubleshoot.run_template", "app.retrieval.graphrag.run_template", "app.retrieval.graphrag.read_query"):
        monkeypatch.setattr(mod, down)
    f, ms = await ask("my CHL-10W shows LP TRIP", "troubleshoot.error_code")
    assert f["response"]["handoff"]["action"] == "handoff" and ms < total()


@pytest.mark.req("FR-OR-06")
@pytest.mark.parametrize("fault", ["error", "hang"])
@pytest.mark.parametrize("name", ["normalize", "guardrails", "rule_layer", "classify", "cache_lookup", "rag_answer", "verify",
                                  "decide"])
async def test_any_node_can_fail(ask, monkeypatch, name, fault):
    fast = get_policy("chat").model_copy(update={"budget_ms": {"total": 2500}, "node_timeouts_ms": {"generate": 1500}})
    monkeypatch.setattr(g, "get_policy", lambda ch: fast)

    async def broken(state):
        if fault == "hang":
            await asyncio.sleep(60)
        raise RuntimeError("boom")
    f, ms = await ask(overrides={name: broken})
    assert "degrade" in f["path"] and ms < 3000, (f["path"], ms)
    r = f["response"]
    assert r["handoff"] or degrade_node.CAVEAT in r["text"]


@pytest.mark.req("FR-OR-06")
@pytest.mark.parametrize("name", ["degrade", "respond", "handoff_or_review"])
async def test_last_resort_when_a_terminal_node_fails(ask, name):
    async def broken(state):
        raise RuntimeError("boom")
    f, _ = await ask(intent="human", overrides={name: broken, **({"decide": broken, "rule_layer": broken} if name == "degrade" else {})})
    assert f["response"]["text"]


@pytest.mark.req("FR-OR-06")
async def test_safety_text_survives_a_broken_safety_node(ask):
    async def broken(state):
        raise RuntimeError("boom")
    f, _ = await ask("smoke is coming from the panel", overrides={"safety_handoff": broken})
    assert "switch the machine off" in f["response"]["text"] and f["response"]["handoff"]["priority"] == "P1"


# ---------- degraded answers ----------

@pytest.mark.req("FR-OR-06")
async def test_degraded_answer_quotes_evidence_with_caveat_and_never_unverified_model_text(ask):
    async def broken(state):
        raise RuntimeError("boom")
    invented = FakeProvider(script={"t2.v1": "Buy MT-FAKE-PART for INR 1 [E1]."})
    f, _ = await ask(llm=invented, overrides={"verify": broken})
    assert "MT-FAKE" not in f["response"]["text"]
    tool, _ = await ask("status of SR-2026-00101", "x", customer=CustomerRef(customer_id="CUST-0005"), overrides={"verify": broken})
    assert "SR-2026-00101" in tool["response"]["text"] and degrade_node.CAVEAT in tool["response"]["text"]
    mail, _ = await ask("status of SR-2026-00101", "x", channel="email", customer=CustomerRef(customer_id="CUST-0005"),
                        overrides={"verify": broken})
    assert mail["response"]["handoff"]["action"] == "draft_for_review"


# ---------- policies (FR-OR-04) ----------

def edited(tmp_path, channel, **changes):
    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw[channel].update(changes)
    p = tmp_path / "policies.yaml"
    p.write_text(yaml.safe_dump(raw))
    return load_policies(p)


@pytest.mark.req("FR-OR-04")
async def test_yaml_changes_behaviour_without_code(ask, monkeypatch, tmp_path):
    async def run(channel="chat", text=ASK, intent="faq.warranty", llm=None, **changes):
        roomy = {"budget_ms": {"total": 30000}, "node_timeouts_ms": {"retrieval": 5000, "generate": 15000, "tool": 5000}}
        pols = edited(tmp_path, channel, **{**roomy, **changes})
        monkeypatch.setattr(g, "get_policy", lambda ch: pols[ch])
        return (await ask(text, intent, channel, llm))[0]

    base = await run()
    assert base["answered_tier"] == "T2" and not base["response"]["handoff"]
    t = {"intent_knn": 0.8, "cache_semantic": 0.9, "confidence_answer": 0.999}
    assert (await run(thresholds=t))["response"]["handoff"]["action"] == "handoff"
    assert (await run(thresholds=t, on_low_confidence="draft_for_review"))["response"]["handoff"]["action"] == "draft_for_review"
    f = await run(allowed_tiers=["T0", "T1", "T4"])
    assert f["response"]["handoff"] and not {"rag_answer", "agents"} & set(f["path"])
    f = await run(allowed_tiers=["T0", "T1", "T2", "T4"], text="my CHL-10W shows LP TRIP", intent="troubleshoot.error_code")
    assert "rag_answer" in f["path"] and "agents" not in f["path"]
    f = await run(allowed_tiers=["T0", "T2", "T3", "T4"], text="What is the warranty period on Matsui equipment?")
    assert "cache_lookup" not in f["path"]
    bad = FakeProvider(script={"t2.v1": "It costs INR 77 [E1]."})
    assert (await run(llm=bad))["response"]["handoff"]
    off = {"rules": "off", "judge": "off"}
    assert "INR 77" in (await run(llm=FakeProvider(script={"t2.v1": "It costs INR 77 [E1]."}), verification=off))["response"]["text"]


@pytest.mark.req("FR-OR-04")
async def test_judge_mode_and_auto_send_come_from_the_policy(ask, monkeypatch, tmp_path):
    # this is about settings, not speed: a slow link to the test database must not degrade the chat turn
    slow = edited(tmp_path, "chat", budget_ms={"total": 30000}, node_timeouts_ms={"retrieval": 5000, "generate": 15000})
    monkeypatch.setattr(g, "get_policy", lambda ch: slow[ch])
    def llm(score):
        return FakeProvider(script={"t2.v1": "It is excluded [E1].",
                                    "judge.v1": f'{{"faithfulness": {score}, "unsupported_claims": [], "answers_question": true}}'})
    f, _ = await ask(channel="email", llm=llm(1.0))
    assert f["auto_send"] and not f["response"]["handoff"]
    chat, _ = await ask(llm=llm(1.0))
    assert chat["verification"].judge is None and chat["auto_send"] is False
    strict = edited(tmp_path, "email", thresholds={"intent_knn": 0.8, "cache_semantic": 0.92, "confidence_answer": 0.75, "auto_send": 0.999})
    monkeypatch.setattr(g, "get_policy", lambda ch: strict[ch])
    f, _ = await ask(channel="email", llm=llm(1.0))
    assert not f["auto_send"] and "auto-send" in f["handoff"].reason
    nojudge = edited(tmp_path, "email", verification={"rules": "sync", "judge": "off"})
    monkeypatch.setattr(g, "get_policy", lambda ch: nojudge[ch])
    f, _ = await ask(channel="email", llm=llm(0.0))
    assert f["verification"].judge is None


@pytest.mark.req("FR-OR-04")
def test_allocator_keeps_the_reserve_and_t4_is_mandatory(tmp_path):
    p = get_policy("chat")
    st = {"policy": p, "deadline": time.monotonic() + 1.0}
    assert allocate_s(st, "generate") == pytest.approx(1.0 - p.reserve_ms / 1000, abs=0.05)
    assert allocate_s(st, "generate", tail=True) == pytest.approx(1.0, abs=0.05)
    assert allocate_s({**st, "deadline": time.monotonic() - 1}, "generate") == 0
    assert allocate_s({**st, "deadline": time.monotonic() + 60}, "tool") == pytest.approx(0.8)
    with pytest.raises(ValueError, match="T4"):
        edited(tmp_path, "chat", allowed_tiers=["T0"])
