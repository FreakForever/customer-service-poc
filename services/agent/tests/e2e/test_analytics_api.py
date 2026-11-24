"""Analytics on mcs_test (BS-27; FR-UI-06, NFR-COST-01). Expected numbers are worked out in Python from the seeded rows, not with
the query under test."""

import datetime as dt
import json
import statistics
import uuid
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config.settings import get_settings
from app.db import get_engine
from tests.e2e.test_chat_api import Client, rows
from tests.unit.test_staff_auth import SECRET, token

pytestmark = pytest.mark.network
IST = ZoneInfo("Asia/Kolkata")
DAY = dt.date(2024, 3, 10)


@pytest_asyncio.fixture
async def api(http, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    get_settings.cache_clear()

    class Api:
        async def raw(self, path, role="agent", **params):
            return await http.get(f"/v1/analytics{path}", params=params, headers=token(role) if role else {})

        async def get(self, path, **params):
            r = await self.raw(path, **params)
            assert r.status_code == 200, r.text
            return r.json()
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM traces WHERE created_at < '2025-01-01'"))
        await conn.execute(text("DELETE FROM eval_runs"))
    yield Api()


async def add(conv, tier, ms, *, channel="chat", at=None, cost=0.001, ground=None, handoff=False, detail=None, mode="live", rating=None):
    tid = uuid.uuid4()
    async with get_engine().begin() as conn:
        await conn.execute(text("""INSERT INTO traces (id, conversation_id, channel, answered_tier, groundedness, handoff, latency_ms, tokens_in,
            tokens_out, cost_usd, mode, created_at, detail) VALUES (:i, :c, :ch, :t, :g, :h, :ms, 100, 10, :cost, :m, :at, CAST(:d AS jsonb))"""),
            {"i": tid, "c": conv, "ch": channel, "t": tier, "g": ground, "h": handoff, "ms": ms, "cost": cost, "m": mode,
             "at": at or dt.datetime.combine(DAY, dt.time(12), IST), "d": json.dumps(detail or {})})
        if rating:
            await conn.execute(text("INSERT INTO feedback (trace_id, rating, reason) VALUES (:t, :r, 'wrong')"), {"t": tid, "r": rating})
    return tid


@pytest.mark.req("FR-UI-06")
@pytest.mark.req("NFR-COST-01")
async def test_summary_matches_the_traces(api):
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    h = {"handoff": {"action": "handoff"}}
    await add(a, "T0", 100, cost=0)
    await add(a, "T2", 900, cost=0.002, rating=1)
    await add(b, "T2", 1500, cost=0.003, handoff=True, detail=h, rating=-1)
    await add(c, "T2", 4000, cost=0.004, channel="email", ground=0.9, detail={"sent": True, "verification": {"passed": True, "coverage": 1.0}})
    await add(c, "T3", 9000, cost=None, channel="email", ground=0.5, detail={"sent": False, "verification": {"passed": False, "coverage": 0.5}})
    await add(d, None, 50, cost=0, at=dt.datetime.combine(DAY, dt.time(0, 10), IST))                # just after midnight IST, still 9 March in UTC
    await add(uuid.uuid4(), "T1", 1, mode="debug")
    await add(uuid.uuid4(), "T1", 1, at=dt.datetime.combine(DAY + dt.timedelta(1), dt.time(0), IST))
    s = await api.get("/summary", **{"from": "2024-03-10", "to": "2024-03-10"})
    ms = [100, 900, 1500, 4000, 9000, 50]
    assert s["volume"]["conversations"] == 4 and s["volume"]["turns"] == 6
    assert s["volume"]["by_channel"] == [{"channel": "chat", "conversations": 3, "turns": 4}, {"channel": "email", "conversations": 1, "turns": 2}]
    assert {x["tier"]: x["turns"] for x in s["tier_mix"]} == {"T0": 1, "T2": 3, "T3": 1, "none": 1}
    assert sum(x["share"] for x in s["tier_mix"]) == pytest.approx(1)
    lat = s["latency_p50_p95"]
    assert lat["overall"]["p50"] == statistics.median(ms)
    assert lat["overall"]["p95"] == pytest.approx(statistics.quantiles(ms, n=100, method="inclusive")[94])
    t2 = next(x for x in lat["by_tier"] if x["tier"] == "T2")
    assert (t2["n"], t2["p50"], t2["p95"]) == (3, 1500, pytest.approx(statistics.quantiles([900, 1500, 4000], n=100, method="inclusive")[94]))
    assert s["handoff_rate"] == {"rate": 0.25, "conversations": 1} and s["deflection"] == {"rate": 0.5, "conversations": 2}     # a and d
    assert s["groundedness"]["score"] == pytest.approx(0.7) and s["groundedness"]["judged"] == 2 and s["groundedness"]["citation_coverage"] == 0.75
    assert s["feedback"] == {"up": 1, "down": 1, "positive": 0.5}
    k = s["cost"]
    assert k["total"] == pytest.approx(0.009) and k["unpriced_turns"] == 1 and k["priced_conversations"] == 3
    assert k["per_conversation"] == pytest.approx((0.002 + 0.003 + 0) / 3)                          # c has an unpriced turn, so it is left out
    assert next(x for x in k["by_tier"] if x["tier"] == "T2")["total"] == pytest.approx(0.009)
    assert [x["id"] for x in k["top_conversations"]] == [str(b), str(a)]
    assert (k["tokens_in"], k["tokens_out"]) == (600, 60)
    [day] = s["by_day"]
    assert (day["turns"], day["t0"], day["t1"], day["t2"], day["t3"], day["t4"], day["other"], day["up"], day["down"]) == (6, 1, 0, 3, 1, 0, 1, 1, 1)
    e = await api.get("/summary", **{"from": "2024-03-10", "to": "2024-03-10", "channel": "email"})
    assert e["volume"]["turns"] == 2 and e["deflection"]["rate"] == 0
    items = (await api.get("/flagged", **{"from": "2024-03-10", "to": "2024-03-10"}))["items"]
    assert {tuple(i["reasons"]) for i in items} == {("thumbs down",), ("failed checks", "low groundedness")}


async def test_a_person_replying_ends_deflection(api, http):
    c = await Client(http).start()
    async with get_engine().begin() as conn:
        await conn.execute(text("INSERT INTO messages (conversation_id, role, content) VALUES (:c, 'agent', 'hi')"), {"c": c.sid})
    await add(uuid.UUID(c.sid), "T1", 5)
    assert (await api.get("/summary", **{"from": str(DAY), "to": str(DAY)}))["deflection"] == {"rate": 0, "conversations": 0}


async def test_empty_period(api):
    s = await api.get("/summary", **{"from": "2024-01-01", "to": "2024-01-02"})
    assert s["volume"] == {"conversations": 0, "turns": 0, "by_channel": []} and len(s["by_day"]) == 2
    assert s["deflection"]["rate"] is s["cost"]["per_conversation"] is s["latency_p50_p95"]["overall"]["p50"] is None


@pytest.mark.req("NFR-COST-01")
async def test_a_real_turn_stores_its_tokens_and_cost(api, http):
    c = await Client(http).start()
    await c.say("What is the rotor warranty?")
    [t] = await rows("SELECT id, tokens_in, tokens_out, cost_usd FROM traces WHERE conversation_id = :c", c=c.sid)
    calls = [x for r in await rows("SELECT attrs->'llm_calls' AS x FROM spans WHERE trace_id = :t", t=t.id) for x in r.x or []]
    assert calls and t.tokens_in == sum(x["tokens_in"] for x in calls) > 0
    assert float(t.cost_usd) == pytest.approx(sum(x["cost_usd"] for x in calls), abs=1e-8) and t.cost_usd > 0
    s = await api.get("/summary")
    assert any(x["id"] == c.sid for x in s["cost"]["top_conversations"]) and s["cost"]["by_model"]


async def test_summary_agrees_with_plain_sql_on_todays_traces(api, http):
    c = await Client(http).start()
    for m in ("hi", "talk to a human"):
        await c.say(m)
    s = await api.get("/summary")
    [x] = await rows("SELECT count(*) AS n, count(DISTINCT conversation_id) AS c, percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p, "
                     "sum(cost_usd) AS k FROM traces WHERE mode = 'live' AND created_at > '2025-01-01'")
    assert (s["volume"]["turns"], s["volume"]["conversations"], s["latency_p50_p95"]["overall"]["p95"]) == (x.n, x.c, x.p)
    assert s["cost"]["total"] == pytest.approx(float(x.k))
    assert s["handoff_rate"]["conversations"] >= 1


async def test_benchmark(api):
    assert await api.get("/benchmark") == {"runs": [], "by_mode": [], "by_type": []}
    async with get_engine().begin() as conn:
        for mode, at, summ in (("rag", "2026-01-01", {"n": 1, "correctness": 0.1}), ("rag", "2026-02-01", {"n": 9, "correctness": 0.6, "by_type": {
                "multi_hop": {"n": 4, "correctness": 0.4}}}), ("kg", "2026-02-01", {"n": 9, "correctness": 0.8, "by_type": {
                "multi_hop": {"n": 4, "correctness": 0.9}}}), ("bad", "2026-02-01", None)):
            await conn.execute(text("INSERT INTO eval_runs (id, mode, started_at, summary) VALUES (gen_random_uuid(), :m, CAST(CAST(:a AS text) AS timestamptz), CAST(:s AS jsonb))"),
                               {"m": mode, "a": at, "s": json.dumps(summ)})
    b = await api.get("/benchmark")
    assert len(b["runs"]) == 4 and [(m["mode"], m["correctness"]) for m in b["by_mode"]] == [("kg", 0.8), ("rag", 0.6)]
    assert b["by_type"] == [{"type": "multi_hop", "modes": {"kg": {"n": 4, "correctness": 0.9}, "rag": {"n": 4, "correctness": 0.4}}}]


async def test_access_and_bad_input(api):
    for p in ("/summary", "/flagged", "/benchmark"):
        assert (await api.raw(p, role=None)).status_code == 401
        assert (await api.raw(p, role="admin")).status_code == 200
    for bad in ({"from": "2024-03-10", "to": "2024-03-09"}, {"from": "2024-01-01", "to": "2024-12-31"}, {"from": "x"}, {"channel": "x"},
                {"to": "0001-01-01"}, {"from": "9999-12-31", "to": "9999-12-31"}):
        assert (await api.raw("/summary", **bad)).status_code == 422, bad
    assert (await api.raw("/flagged", limit=1000)).status_code == 422
