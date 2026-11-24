"""Agent console API on mcs_test (BS-25; FR-HO-01..03, FR-CA-05)."""

import asyncio
import json
import time
import uuid

import pytest
import pytest_asyncio

from app.channels import console
from app.channels.email.adapter import InboundMail
from app.config.settings import get_settings
from app.db import get_engine
from app.providers.fake import FakeProvider
from tests.e2e.test_chat_api import Client, one, rows, txt
from tests.unit.test_staff_auth import SECRET, token

pytestmark = pytest.mark.network
A, B = {"sub": "karthik@m.example", "name": "Karthik Rao"}, {"sub": "tanvi@m.example", "name": "Tanvi Shetty"}


@pytest_asyncio.fixture
async def api(http, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    monkeypatch.setenv("MAIL_ADAPTER", "demo")
    get_settings.cache_clear()
    from app.api import console as routes
    monkeypatch.setattr(routes, "get_embedder", lambda: FakeProvider(dim=768))

    class Api:
        async def call(self, method, path, who=A, role="agent", **body):
            return await http.request(method, f"/v1/console{path}", headers=token(role, **who), **({"json": body} if body else {}))

        async def get(self, path, **kw):
            r = await self.call("GET", path, **kw)
            assert r.status_code == 200, r.text
            return r.json()
    yield Api()


async def handed_off(http, persona=None):
    c = await Client(http).start(persona)
    await c.say("talk to a human")
    return c


@pytest.mark.req("FR-HO-01")
async def test_takeover_reply_release(api, http):
    c = await handed_off(http)
    assert c.sid in [i["id"] for i in (await api.get("/conversations?view=live"))["items"]]
    assert (await api.call("POST", f"/conversations/{c.sid}/messages", text="Hi")).status_code == 409            # not taken over yet
    assert (await api.call("POST", f"/conversations/{c.sid}/takeover")).status_code == 204
    assert (await api.call("POST", f"/conversations/{c.sid}/takeover", who=B)).status_code == 409
    assert (await api.call("POST", f"/conversations/{c.sid}/messages", who=B, text="Hi")).status_code == 409
    assert (await api.call("POST", f"/conversations/{c.sid}/release", who=B)).status_code == 409
    assert (await api.call("POST", f"/conversations/{c.sid}/messages", text="Hello, Karthik here")).status_code == 201
    last = (await c.get())["messages"][-1]
    assert (last["role"], last["content"], last["sender"]) == ("agent", "Hello, Karthik here", "Karthik (Matsui)")
    assert "karthik@" not in json.dumps(await c.get())
    ev = await c.say("is anyone there?")
    assert one(ev, "handoff")["status"] == "with_agent" and not txt(ev)
    assert c.sid in [i["id"] for i in (await api.get("/conversations?view=mine"))["items"]]
    assert (await api.call("POST", f"/conversations/{c.sid}/release")).status_code == 204
    assert txt(await c.say("hi")) and (await c.get())["status"] == "bot"
    await api.call("POST", f"/conversations/{c.sid}/close")
    assert (await c.raw("hi")).status_code == 409 and (await api.call("POST", f"/conversations/{c.sid}/takeover")).status_code == 409


async def test_events_announce_the_agent_and_deliver_messages(api, http):
    from app.channels import chat_store
    c = await handed_off(http)
    sid = uuid.UUID(c.sid)
    async with get_engine().connect() as conn:
        assert await chat_store.human_state(conn, sid) == {"status": "handoff", "from": None}
    await api.call("POST", f"/conversations/{c.sid}/takeover")
    await api.call("POST", f"/conversations/{c.sid}/messages", text="one")
    async with get_engine().connect() as conn:
        assert await chat_store.human_state(conn, sid) == {"status": "human", "from": "Karthik (Matsui)"}
        [m] = await chat_store.human_messages_after(conn, sid, set())
    assert (m["content"], m["sender"]) == ("one", "Karthik (Matsui)")


async def test_takeover_during_a_bot_turn_wins(api, http):
    c = await handed_off(http)
    t = asyncio.create_task(c.say("hi"))
    await asyncio.sleep(0.05)
    await api.call("POST", f"/conversations/{c.sid}/takeover")
    await t
    d = await api.get(f"/conversations/{c.sid}")
    assert d["conversation"]["status"] == "human" and d["conversation"]["agent_name"] == "Karthik Rao"


@pytest.mark.req("FR-HO-02")
async def test_detail_and_trace(api, http):
    c = await Client(http).start("CUST-0001")
    ev = await c.say("Does dust void the warranty?")
    d = await api.get(f"/conversations/{c.sid}")
    assert d["customer"]["company"] == "Aarav Polymers" and d["assets"] and d["contracts"]
    assert all(isinstance(a["in_warranty"], bool) and isinstance(a["in_amc"], bool) for a in d["assets"])
    assert next(a for a in d["assets"] if a["serial_no"] == "MT-DHD150-23-00417")["in_warranty"] is False
    assert all(t["status"] not in ("resolved", "closed") for t in d["tickets"])
    t0 = time.perf_counter()
    t = await api.get(f"/traces/{one(ev, 'done')['trace_id']}")
    print("trace ms", round((time.perf_counter() - t0) * 1000))
    assert t["trace"]["answered_tier"] == "T2" and t["evidence"] and t["evidence"][0]["text"] and t["verification"]["passed"]
    assert next(s["node"] for s in t["spans"]) == "normalize" and all(s["duration_ms"] is not None for s in t["spans"])
    c = await handed_off(http)
    assert (await api.get(f"/conversations/{c.sid}"))["summary"]
    for p in ("/conversations/", "/traces/"):
        assert (await api.call("GET", f"{p}{uuid.uuid4()}")).status_code == 404


async def draft(subject="Help", frm="rohit.sharma@demo-aarav-polymers.example"):
    """An e-mail conversation in review with one draft."""
    from sqlalchemy import text

    from app.channels.chat_store import add_message
    from app.channels.email.pipeline import ingest
    cid, _ = await ingest(get_engine(), InboundMail(f"<{uuid.uuid4()}@t>", frm, subject, "Hello"))
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM jobs WHERE kind = 'email_reply'"))
        mid = await add_message(conn, cid, "assistant", "Dear Rohit,\n\nDraft text.\n\nMatsui Customer Care")
        await conn.execute(text("UPDATE messages SET delivery = 'draft', external_message_id = :e WHERE id = :m"), {"e": f"<d-{mid}@t>", "m": mid})
        await conn.execute(text("UPDATE conversations SET status = 'review' WHERE id = :c"), {"c": cid})
    return cid, mid


@pytest.mark.req("FR-HO-01")
async def test_review_edit_and_send_once(api):
    cid, mid = await draft("Smoke <b>test</b>")
    assert str(cid) in [i["id"] for i in (await api.get("/conversations?view=review"))["items"]]
    body = {"text": "Dear Rohit,\n\nEdited <script>x</script>.\n\nMatsui Customer Care", "approve_draft_id": str(mid)}
    res = await asyncio.gather(*(api.call("POST", f"/conversations/{cid}/messages", who=w, **body) for w in (A, B, A)))
    assert sorted(r.status_code for r in res) == [201, 409, 409]
    [o] = await rows("SELECT to_addr, subject, body_text, body_html, in_reply_to FROM mailbox_messages WHERE box = 'outbox' AND message_id = :m", m=f"<d-{mid}@t>")
    assert o.to_addr == "rohit.sharma@demo-aarav-polymers.example" and o.subject == "Re: Smoke <b>test</b>" and "Edited" in o.body_text
    assert "<script>" not in o.body_html and o.in_reply_to
    d = await api.get(f"/conversations/{cid}")
    assert d["conversation"]["status"] == "bot" and d["messages"][-1]["delivery"] == "sent"
    assert {a["type"]: a["edited"] for a in d["messages"][-1]["actions"]}["reviewed_by"] is True


async def test_new_email_reply_and_second_draft_keeps_review(api):
    cid, mid = await draft()
    r = await api.call("POST", f"/conversations/{cid}/messages", text="A separate note")
    assert r.status_code == 201 and (await api.get(f"/conversations/{cid}"))["conversation"]["status"] == "review"
    assert (await rows("SELECT count(*) FROM mailbox_messages WHERE box = 'outbox' AND body_text LIKE 'A separate note%'"))[0][0] == 1
    other, _ = await draft()
    assert (await api.call("POST", f"/conversations/{other}/messages", text="x", approve_draft_id=str(mid))).status_code == 409
    await api.call("POST", f"/conversations/{other}/takeover", who=B)
    assert (await api.call("POST", f"/conversations/{other}/messages", text="x")).status_code == 409


async def test_failed_provider_is_reported_and_not_lost(api, monkeypatch):
    class Broken:
        name, transactional = "gmail", False

        async def send(self, m, conn):
            raise ConnectionError
    from app.api import console as routes
    monkeypatch.setattr(routes, "get_adapter", Broken)
    cid, mid = await draft()
    assert (await api.call("POST", f"/conversations/{cid}/messages", text="Hi", approve_draft_id=str(mid))).status_code == 502
    d = await api.get(f"/conversations/{cid}")
    assert d["conversation"]["status"] == "review" and d["messages"][-1]["delivery"] == "failed"


@pytest.mark.req("FR-HO-03")
async def test_views_search_paging_counts(api, http):
    cid, _ = await draft("Needle in subject")
    got = await api.get("/conversations?view=all&q=needle")
    assert [i["id"] for i in got["items"]] == [str(cid)] and got["counts"]["review"] >= 1 and got["counts"]["tickets"] >= 20
    assert (await api.get("/conversations?q=%25%25"))["items"] == []                       # wildcards are literal
    p1, p2 = await api.get("/conversations?limit=2"), await api.get("/conversations?limit=2&offset=2")
    assert p1["next"] == 2 and not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    assert (await api.call("GET", "/conversations?view=x")).status_code == 422


@pytest.mark.req("FR-HO-03")
async def test_tickets(api):
    seed = await api.get("/tickets?limit=100")
    assert {"SR-2026-00101", "SR-2026-00112"} <= {t["id"] for t in seed["items"]}
    assert all(t["priority"] == "P1" and t["status"] not in ("resolved", "closed") for t in (await api.get("/tickets?status=open&priority=P1"))["items"])
    assert all(t["assigned_engineer"] == "Tanvi Shetty" for t in (await api.get("/tickets?mine=true", who=B))["items"])
    assert [t["id"] for t in (await api.get("/tickets?q=00101"))["items"]] == ["SR-2026-00101"]
    r = await api.call("PATCH", "/tickets/SR-2026-00101", status="scheduled", scheduled_visit="2026-12-01")
    h = r.json()["history"]
    assert r.json()["status"] == "scheduled" and h[-1]["by"] == "Karthik Rao" and h[0]["event"] == "created" and h[-1]["at"]
    assert (await api.call("PATCH", "/tickets/SR-2026-00101", status="bogus")).status_code == 422
    assert (await api.call("PATCH", "/tickets/NOPE", status="closed")).status_code == 404
    assert (await api.call("PATCH", "/tickets/SR-2026-00101", priority="P3")).status_code == 422


@pytest.mark.req("FR-CA-05")
async def test_promote_answers_the_next_similar_query(api, http):
    c = await Client(http).start()
    q = "Does abrasive filler wear count as normal wear under warranty?"
    ev = await c.say(q)
    assert one(ev, "done")["tier"] == "T2"
    mid = one(ev, "done")["message_id"]
    good = {"question": q, "answer": "Wear from abrasive fillers is normal wear [E1].", "source_doc": "DOC-POL-WARRANTY-V2"}
    for bad, code in (({"source_doc": "DOC-POL-WARRANTY-V1"}, 422), ({"answer": "Call 98765 43210 for details"}, 422), ({"question": "Is SR-2026-00101 covered?"}, 422),
                      ({"answer": "There are 4 units left in Pune"}, 422), ({"question": "What is the warranty period on Matsui equipment?"}, 409)):
        assert (await api.call("POST", f"/messages/{mid}/promote-faq", **{**good, **bad})).status_code == code, bad
    assert (await api.call("POST", f"/messages/{uuid.uuid4()}/promote-faq", **good)).status_code == 404
    assert (await api.call("POST", f"/messages/{mid}/promote-faq", **good)).status_code == 201
    for again in (q, "does abrasive filler wear count as a normal wear under the warranty"):
        ev = await (await Client(http).start()).say(again)
        assert one(ev, "done")["tier"] == "T1" and "Wear from abrasive fillers" in txt(ev), again
    assert console.SIGNATURE
