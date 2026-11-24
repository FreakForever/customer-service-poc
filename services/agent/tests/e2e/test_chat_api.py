"""Chat API over HTTP with the real graph and mcs_test (BS-20; FR-CH-01, FR-CH-02, FR-CH-06). Classifier and model
replies are scripted."""

import asyncio
import importlib.util
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import chat as api
from app.api import session_token
from app.channels import chat as channel
from app.channels import chat_store as store
from app.config.settings import Settings, get_settings
from app.db import get_engine, warm_pool
from app.ingest.seed_kb import seed_kb
from app.kg.client import get_read_driver
from app.orchestrator.graph import build_graph
from app.orchestrator.nodes import agents as agents_node
from app.orchestrator.nodes import rag as rag_node
from app.orchestrator.nodes import verify as verify_node
from app.orchestrator.nodes.rules import resolve_followup
from app.orchestrator.state import Intent
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.rerank import NoopReranker

pytestmark = pytest.mark.network
AGENT = Path(__file__).resolve().parents[2]
ANSWER = "Rotors have a separate 24-month warranty [E1]."


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


class Client:
    def __init__(self, http):
        self.http, self.seen = http, []

    async def start(self, persona=None):
        r = await self.http.post("/v1/chat/sessions", json={"persona_customer_id": persona} if persona else {})
        assert r.status_code == 201, r.text
        self.sid, self.token = r.json()["session_id"], r.json()["token"]
        return self

    @property
    def auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def raw(self, message):
        return await self.http.post(f"/v1/chat/sessions/{self.sid}/messages", json={"text": message}, headers=self.auth)

    async def say(self, message):
        r = await self.raw(message)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), r.text
        return parse(r.text)

    async def get(self):
        return (await self.http.get(f"/v1/chat/sessions/{self.sid}", headers=self.auth)).json()


def parse(body):
    out = []
    for block in filter(None, body.split("\n\n")):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def txt(events):
    return "".join(d["t"] for e, d in events if e == "token")


def one(events, name):
    return next(d for e, d in events if e == name)


@pytest_asyncio.fixture
async def http(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    for f in (get_settings, get_engine, get_read_driver):
        f.cache_clear()
    await warm_pool()
    await get_read_driver().verify_connectivity()
    intents = {"warranty": "faq.warranty", "LP TRIP": "troubleshoot.error_code", "complaint": "complaint", "person": "human"}

    async def classify(state):
        name = next((v for k, v in intents.items() if k in state["env"].text), "faq.general")
        return {} if state.get("intents") else {"intents": [Intent(name=name, confidence=0.95, source="knn")]}
    for mod in (rag_node, agents_node):
        monkeypatch.setattr(mod, "get_reranker", NoopReranker)
    for mod in (rag_node, agents_node, verify_node):
        monkeypatch.setattr(mod, "get_llm", lambda: ResilientLLM(FakeProvider(script={"t2.v1": ANSWER})))
    monkeypatch.setattr(channel, "graph", build_graph({"classify": classify}))
    from app.main import create_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t", timeout=60) as c:
        yield c
    await asyncio.gather(*channel._tasks, return_exceptions=True)
    await get_engine().dispose()
    await get_read_driver().close()
    get_read_driver.cache_clear()


async def rows(sql, **p):
    async with get_engine().connect() as c:
        return (await c.execute(text(sql), p)).all()


async def execute(sql, **p):
    async with get_engine().begin() as c:
        await c.execute(text(sql), p)


@pytest.mark.req("FR-CH-01")
async def test_event_order_citations_and_transcript(http):
    c = await Client(http).start()
    ev = await c.say("Does dust damage void the warranty on parts?")
    names = [e for e, _ in ev if e != "status"]
    assert names[0] == "token" and names[-2:] == ["citations", "done"] and names.count("token") > 3 and txt(ev) == ANSWER
    [cite] = one(ev, "citations")
    assert cite["marker"] == "E1" and cite["url"].startswith("/a/DOC-") and cite["title"]
    done = one(ev, "done")
    assert done["tier"] == "T2" and (await rows("SELECT mode, conversation_id FROM traces WHERE id = :i", i=done["trace_id"])) == [
        ("live", uuid.UUID(c.sid))]
    assert [(m["role"], m["content"]) for m in (await c.get())["messages"]] == [
        ("customer", "Does dust damage void the warranty on parts?"), ("assistant", ANSWER)]


@pytest.mark.req("FR-CH-01")
async def test_status_event_within_500ms_then_the_answer(http, monkeypatch):
    c = await Client(http).start()

    async def slow(*a, **k):
        await asyncio.sleep(1.2)
        raise RuntimeError("boom")
    monkeypatch.setattr(channel, "run_turn", slow)
    async with get_engine().begin() as conn:
        assert await store.begin_turn(conn, uuid.UUID(c.sid))
        conv = await store.load(conn, uuid.UUID(c.sid))
    t0, seen = time.perf_counter(), []
    async for event, data in channel.stream(get_engine(), uuid.UUID(c.sid), conv, "x"):
        seen.append((event, data, time.perf_counter() - t0))
    assert seen[0][0] == "status" and seen[0][2] < 0.5
    assert seen[-1][0] == "done" and txt([(e, d) for e, d, _ in seen])          # a crashed turn still answers
    assert any(e == "handoff" for e, _, _ in seen)


@pytest.mark.req("FR-CH-02")
async def test_sticky_agent_and_reload_resume(http):
    c = await Client(http).start()
    assert "Which model" in txt(await c.say("my chiller shows LP TRIP"))
    again = Client(http)                       # page reload: same session id and token, no server memory
    again.sid, again.token = c.sid, c.token
    got = await again.get()
    assert got["active_agent"] == "troubleshoot" and len(got["messages"]) == 2 and got["status"] == "bot"
    ev = await again.say("it's a CHL-10W")
    assert "Refrigerant leak" in txt(ev) and one(ev, "actions")[0]["label"] == "Raise a service request"


@pytest.mark.req("FR-CH-02")
async def test_CH_001_followup_uses_the_previous_question(http):
    c = await Client(http).start()
    assert one(await c.say("hi"), "done")["tier"] == "T0"
    await c.say("what's the warranty on a new dryer?")
    ev = await c.say("and the rotor?")
    [d] = await rows("SELECT attrs FROM spans WHERE trace_id = :t AND node = 'normalize'", t=one(ev, "done")["trace_id"])
    assert "warranty" in d[0]["followup_of"] and "rotor" in txt(ev).lower()
    assert (await c.get())["messages"][-2]["content"] == "and the rotor?"          # transcript keeps the raw text


@pytest.mark.parametrize(("t", "want"), [
    ("and the rotor?", "warranty on a dryer? and the rotor?"), ("what about filters", "warranty on a dryer? what about filters"),
    ("my chiller shows LP TRIP", "my chiller shows LP TRIP"),
    ("and can you please also send me the full price list today", "and can you please also send me the full price list today")])
def test_followup_rule(t, want):
    assert resolve_followup(t, "warranty on a dryer?") == want


async def test_form_creates_a_ticket(http):
    c = await Client(http).start("CUST-0010")
    ev = await c.say("I have a complaint")
    assert one(ev, "form")["form_id"] == "service_request"
    r = await http.post(f"/v1/chat/sessions/{c.sid}/forms/service_request", headers=c.auth, json={"fields": {
        "description": "GR-200 shows MOTOR OL", "serial_no": "MT-GR200-24-00233", "contact": "+91 98765 43210"}})
    ev = parse(r.text)
    assert "SR-2026-" in txt(ev), txt(ev)
    [t] = await rows("SELECT customer_id, serial_no FROM tickets WHERE id = :i", i=txt(ev).split("request is ")[1].split(" ")[0])
    assert tuple(t) == ("CUST-0010", "MT-GR200-24-00233")
    bad = await http.post(f"/v1/chat/sessions/{c.sid}/forms/service_request", headers=c.auth, json={"fields": {"customer_id": "X"}})
    assert bad.status_code == 422


async def test_handoff_queue_bot_continues_until_a_human_takes_over(http):
    c = await Client(http).start()
    ev = await c.say("I want a person")
    assert one(ev, "handoff")["status"] == "queued" and one(ev, "handoff")["position"] >= 1 and txt(ev)
    assert (await c.get())["status"] == "handoff"
    roles = [r[0] for r in await rows("SELECT role FROM messages WHERE conversation_id = :i ORDER BY created_at", i=c.sid)]
    assert roles == ["customer", "system", "assistant"]
    assert "system" not in {m["role"] for m in (await c.get())["messages"]}          # the note is for staff only
    assert one(await c.say("Does dust void the warranty?"), "done")["tier"] == "T2"
    await execute("UPDATE conversations SET status = 'human' WHERE id = :i", i=c.sid)
    ev = await c.say("hello?")
    assert [e for e, _ in ev] == ["handoff", "done"] and one(ev, "handoff")["status"] == "with_agent"
    await execute("UPDATE conversations SET status = 'closed' WHERE id = :i", i=c.sid)
    assert (await c.raw("x")).status_code == 409


async def test_staff_takeover_during_a_turn_is_not_overwritten(http):
    c = await Client(http).start()
    async with get_engine().begin() as conn:
        await store.begin_turn(conn, uuid.UUID(c.sid))
    await execute("UPDATE conversations SET status = 'human' WHERE id = :i", i=c.sid)
    async with get_engine().begin() as conn:
        await store.end_turn(conn, uuid.UUID(c.sid), session={"dialog": {}, "active_agent": None}, status="handoff", priority=None, retention_days=30)
    assert (await c.get())["status"] == "human"


async def test_human_messages_stream(http, monkeypatch):
    monkeypatch.setattr(api, "POLL_S", 0.05)
    monkeypatch.setattr(api, "STREAM_MAX_S", 2.0)
    c = await Client(http).start()

    async def agent():
        await asyncio.sleep(0.6)
        async with get_engine().begin() as conn:
            await store.add_message(conn, uuid.UUID(c.sid), "agent", "Karthik here")
    t = asyncio.create_task(agent())
    r = await http.get(f"/v1/chat/sessions/{c.sid}/events", headers=c.auth)
    await t
    assert r.text.count("Karthik here") == 1 and "event: agent_message" in r.text


# ---------- access ----------

async def test_session_token_is_required_and_bound_to_one_session(http):
    a, b = await Client(http).start(), await Client(http).start()
    for tok in ("", "x.y.z", b.token, a.token[:-2] + "aa", session_token.issue(uuid.UUID(a.sid), -1)):
        h = {"Authorization": f"Bearer {tok}"}
        assert (await http.get(f"/v1/chat/sessions/{a.sid}", headers=h)).status_code == 401
        assert (await http.post(f"/v1/chat/sessions/{a.sid}/messages", headers=h, json={"text": "x"})).status_code == 401
        assert (await http.get(f"/v1/chat/sessions/{a.sid}/events", headers=h)).status_code == 401
    gone = uuid.uuid4()
    r = await http.get(f"/v1/chat/sessions/{gone}", headers={"Authorization": f"Bearer {session_token.issue(gone, 60)}"})
    assert r.status_code == 404


async def test_persona_rules(http, monkeypatch):
    assert (await http.post("/v1/chat/sessions", json={"persona_customer_id": "CUST-9999"})).status_code == 404
    own = await Client(http).start("CUST-0005")
    assert "in progress" in txt(await own.say("status of SR-2026-00101"))
    other, anon = await Client(http).start("CUST-0009"), await Client(http).start()
    assert "couldn't find" in txt(await other.say("status of SR-2026-00101"))
    assert "verify your account" in txt(await anon.say("status of SR-2026-00101"))
    monkeypatch.setenv("DEMO_PERSONAS", "false")
    get_settings.cache_clear()
    assert (await http.post("/v1/chat/sessions", json={"persona_customer_id": "CUST-0005"})).status_code == 403


def test_personas_default_off_outside_dev_and_demo():
    assert [Settings(_env_file=None, env=e).personas_enabled for e in ("dev", "demo", "prod")] == [True, True, False]


async def test_limits(http):
    c = await Client(http).start()
    for body in ({"text": ""}, {"text": "   "}, {"text": "x" * 4001}, {}):
        r = await http.post(f"/v1/chat/sessions/{c.sid}/messages", headers=c.auth, json=body)
        assert r.status_code == 422, body


# ---------- feedback (FR-CH-06) ----------

@pytest.mark.req("FR-CH-06")
async def test_feedback_is_linked_to_the_trace(http):
    c, other = await Client(http).start(), await Client(http).start()
    done = one(await c.say("Does dust damage void the warranty?"), "done")
    body = {"message_id": done["message_id"], "rating": -1, "reason": "too short", "session_id": c.sid}
    assert (await http.post("/v1/feedback", json=body, headers=c.auth)).status_code == 204
    assert (await http.post("/v1/feedback", json={**body, "rating": 1}, headers=c.auth)).status_code == 204
    got = await rows("SELECT rating, reason, trace_id FROM feedback WHERE message_id = :m", m=done["message_id"])
    assert got == [(1, "too short", uuid.UUID(done["trace_id"]))]
    assert (await http.post("/v1/feedback", json=body, headers=other.auth)).status_code == 401
    assert (await http.post("/v1/feedback", json={**body, "session_id": other.sid}, headers=other.auth)).status_code == 404
    assert (await http.post("/v1/feedback", json={**body, "rating": 5}, headers=c.auth)).status_code == 422


# ---------- concurrency ----------

async def test_one_turn_at_a_time_per_session(http):
    c = await Client(http).start()
    codes = sorted(r.status_code for r in await asyncio.gather(*(c.raw("Does dust void the warranty?") for _ in range(3))))
    assert codes == [200, 409, 409]
    assert len((await c.get())["messages"]) == 2 and (await c.raw("thanks")).status_code == 200


async def test_turn_finishes_after_the_customer_disconnects(http):
    c = await Client(http).start()
    async with get_engine().begin() as conn:
        await store.begin_turn(conn, uuid.UUID(c.sid))
        conv = await store.load(conn, uuid.UUID(c.sid))
    gen = channel.stream(get_engine(), uuid.UUID(c.sid), conv, "Does dust void the warranty?")
    await gen.__anext__()
    await gen.aclose()
    await asyncio.gather(*channel._tasks)
    assert (await c.get())["messages"][-1]["content"] == ANSWER and (await c.raw("thanks")).status_code == 200


async def test_load_5_concurrent_sessions(http):
    async def session(i):
        c = await Client(http).start()
        out = []
        for q in ("Does dust damage void the warranty?", "my chiller shows LP TRIP", "it's a CHL-10W"):
            t0 = time.perf_counter()
            ev = await c.say(q)
            out.append((time.perf_counter() - t0, one(ev, "done")["tier"], txt(ev)))
        assert len((await c.get())["messages"]) == 6
        return out
    res = [x for s in await asyncio.gather(*(session(i) for i in range(5))) for x in s]
    secs = sorted(x[0] for x in res)
    print(f"LOAD n={len(secs)} p50={secs[7]:.2f}s max={secs[-1]:.2f}s tiers={sorted({x[1] for x in res})}")
    assert secs[-1] < 8 and all("Refrigerant leak" in x[2] for x in res[2::3])
