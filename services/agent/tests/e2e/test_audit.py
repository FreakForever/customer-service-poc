"""Async audit of chat answers on mcs_test (BS-29; FR-VF-05). Judge replies are scripted."""

import json
import uuid

import pytest
from sqlalchemy import text

from app.config.settings import get_settings
from app.db import get_engine
from app.jobs.queue import drain
from app.orchestrator.policies import get_policy
from app.providers import ProviderUnavailable, ResilientLLM
from app.providers.fake import FakeProvider
from app.verify import audit
from tests.e2e.test_chat_api import Client, rows
from tests.unit.test_staff_auth import SECRET, token

pytestmark = pytest.mark.network
QUESTION = "What is the rotor warranty?"


async def answered(http, monkeypatch, rate):
    p = get_policy("chat")
    every = p.model_copy(update={"verification": p.verification.model_copy(update={"judge_sample_rate": rate})})
    monkeypatch.setattr("app.channels.chat.get_policy", lambda ch: every)
    c = await Client(http).start()
    await c.say(QUESTION)
    [t] = await rows("SELECT id FROM traces WHERE conversation_id = :c", c=c.sid)
    return c, t.id


async def job_of(tid):
    got = await rows("SELECT status, attempts, payload, result FROM jobs WHERE kind = 'audit_sample' AND payload->>'trace_id' = :t", t=str(tid))
    return got[0] if got else None


async def work(reply):
    e = get_engine()
    llm = ResilientLLM(FakeProvider(dim=768, script={"judge.v1": reply}))
    return await drain(e, {"audit_sample": audit.make_audit_handler(e, lambda: llm)})


@pytest.mark.req("FR-VF-05")
@pytest.mark.parametrize(("score", "flagged"), [(0.3, True), (1.0, False)])
async def test_score_is_stored_and_low_ones_are_listed_with_their_trace(http, monkeypatch, score, flagged):
    c, tid = await answered(http, monkeypatch, 1.0)
    job = await job_of(tid)
    assert job.payload["question"] == QUESTION and job.payload["evidence"][0]["text"]
    await work(json.dumps({"faithfulness": score, "unsupported_claims": ["mail a@b.example"] if flagged else [], "answers_question": True}))
    [t] = await rows("SELECT groundedness, detail->'audit' AS a, detail->'path' AS p FROM traces WHERE id = :t", t=tid)
    assert t.groundedness == pytest.approx(score) and t.a["flagged"] is flagged and t.p and "a@b.example" not in json.dumps(t.a)
    job = await job_of(tid)
    assert job.status == "done" and job.payload == {"trace_id": str(tid)}                 # the conversation text doesn't stay in the queue
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    get_settings.cache_clear()
    items = (await http.get("/v1/analytics/flagged?limit=100", headers=token("agent"))).json()["items"]
    mine = [i for i in items if i["trace_id"] == str(tid)]
    assert bool(mine) is flagged
    if flagged:
        assert "low groundedness" in mine[0]["reasons"] and mine[0]["conversation_id"] == c.sid


async def test_unsampled_answers_are_not_queued(http, monkeypatch):
    _, tid = await answered(http, monkeypatch, 0.0)
    assert await job_of(tid) is None


async def test_judge_outage_leaves_the_answer_alone_and_gives_up_cleanly(http, monkeypatch):
    _, tid = await answered(http, monkeypatch, 1.0)
    for n in range(1, 4):
        await work(ProviderUnavailable("down", []))
        assert (await job_of(tid)).attempts == n
        async with get_engine().begin() as conn:
            await conn.execute(text("UPDATE jobs SET run_after = now() WHERE kind = 'audit_sample'"))
    job = await job_of(tid)
    assert job.status == "failed" and job.payload == {"trace_id": str(tid)}
    assert [t.groundedness for t in await rows("SELECT groundedness FROM traces WHERE id = :t", t=tid)] == [None]


async def test_trace_deleted_meanwhile(http, monkeypatch):
    _, tid = await answered(http, monkeypatch, 1.0)
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM traces WHERE id = :t"), {"t": tid})
    await work(json.dumps({"faithfulness": 1.0, "unsupported_claims": [], "answers_question": True}))
    assert (await job_of(tid)).result["skipped"] == "trace is gone" and isinstance(tid, uuid.UUID)
