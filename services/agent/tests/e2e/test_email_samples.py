"""Email pipeline on mcs_test with the real graph (BS-21; FR-EM-01..06). Classifier and model replies are scripted;
mail goes to the demo mailbox only."""

import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import email as api
from app.channels.email import pipeline
from app.channels.email.adapter import InboundMail, MailAttachment
from app.channels.email.pipeline import (
    ingest,
    make_gmail_sync_handler,
    make_gmail_watch_handler,
    make_reply_handler,
)
from app.config.settings import Settings, get_settings
from app.db import get_engine, warm_pool
from app.ingest.seed_kb import seed_kb
from app.jobs.queue import run_once
from app.kg.client import get_read_driver
from app.orchestrator.graph import build_graph
from app.orchestrator.nodes import agents as agents_node
from app.orchestrator.nodes import rag as rag_node
from app.orchestrator.nodes import verify as verify_node
from app.orchestrator.state import Intent
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.rerank import NoopReranker

pytestmark = pytest.mark.network
AGENT = Path(__file__).resolve().parents[2]
DATA = AGENT.parents[1] / "mock-data" / "data"
EMAILS = {e["id"]: e for e in json.loads((DATA / "channels/emails.json").read_text())}
CUSTOMERS = json.loads((DATA / "ops/customers.json").read_text())
INTENTS = {"EM-001": ["troubleshoot.error_code", "amc.status"], "EM-002": ["sales.quote"], "EM-004": ["faq.process", "faq.maintenance"],
           "EM-005": ["parts.availability"], "EM-006": ["troubleshoot.error_code"], "EM-009": ["amc.renewal"], "EM-010": ["troubleshoot.error_code"]}


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


class Mail:
    def __init__(self, monkeypatch):
        self.mp, self.intents, self.judge = monkeypatch, ["faq.general"], 1.0

    async def deliver(self, m):
        return await ingest(get_engine(), m)

    async def work(self, adapter=None, expect_failure=False):
        async def classify(state):
            return {} if state.get("intents") else {"intents": [Intent(name=n, confidence=0.95 - i / 50, source="knn")
                                                                for i, n in enumerate(self.intents)]}
        script = {"t2.v1": lambda m: "The manual covers this [E1].", "summary.v1": lambda m: "note for staff",
                  "judge.v1": lambda m: json.dumps({"faithfulness": self.judge, "unsupported_claims": [], "answers_question": True}),
                  "lead.v1": lambda m: json.dumps({"product_interest": "2 chillers", "application": "PP", "location": "Chakan", "throughput": "450T"})}
        for mod in (rag_node, agents_node, verify_node):
            self.mp.setattr(mod, "get_llm", lambda: ResilientLLM(FakeProvider(script=script)))
        self.mp.setattr(pipeline, "graph", build_graph({"classify": classify}))
        out = []
        h = make_reply_handler(get_engine(), adapter)

        async def rec(job):
            out.append(await h(job))
            return out[-1]
        while (j := await run_once(get_engine(), {"email_reply": rec})) is not None:
            assert out or expect_failure, j
        return out

    async def sample(self, eid, **kw):
        e = EMAILS[eid]
        self.intents = INTENTS.get(eid, ["faq.general"])
        cid, _ = await self.deliver(InboundMail(message_id=f"<{eid}-{uuid.uuid4()}@t>", from_addr=e["from"], subject=e["subject"], text=e["body"], **kw))
        [r] = await self.work()
        [m] = await rows("SELECT content, delivery, citations, external_message_id FROM messages WHERE conversation_id = :c AND role = 'assistant' "
                         "ORDER BY created_at DESC LIMIT 1", c=cid)
        status = (await rows("SELECT status FROM conversations WHERE id = :c", c=cid))[0][0]
        return cid, r, m, status


async def rows(sql, **p):
    async with get_engine().connect() as c:
        return (await c.execute(text(sql), p)).all()


async def outbox(reply_id):
    return await rows("SELECT to_addr, subject, body_text, body_html FROM mailbox_messages WHERE box = 'outbox' AND message_id = :m", m=reply_id)


@pytest_asyncio.fixture
async def mail(url, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("GCS_BUCKET", "")
    monkeypatch.setattr("app.ingest.pipeline.LOCAL_UPLOADS", tmp_path)
    for f in (get_settings, get_engine, get_read_driver):
        f.cache_clear()
    await warm_pool()
    await get_read_driver().verify_connectivity()
    async with get_engine().begin() as c:
        await c.execute(text("DELETE FROM jobs"))
        await c.execute(text("DELETE FROM messages WHERE created_at < now()"))
    for mod in (rag_node, agents_node):
        monkeypatch.setattr(mod, "get_reranker", NoopReranker)
    yield Mail(monkeypatch)
    await get_engine().dispose()
    await get_read_driver().close()
    get_read_driver.cache_clear()


# ---------- the 10 samples ----------

@pytest.mark.req("FR-EM-03")
@pytest.mark.parametrize(("eid", "delivery", "status"), [
    ("EM-003", "draft", "handoff"), ("EM-004", "sent", "bot"), ("EM-008", "sent", "handoff"), ("EM-007", "sent", "bot"),
    ("EM-001", "sent", "bot"), ("EM-002", "sent", "bot"), ("EM-005", "sent", "bot"), ("EM-006", "sent", "bot"), ("EM-010", "sent", "bot")])
async def test_samples_send_or_review(mail, eid, delivery, status):
    _, r, m, got = await mail.sample(eid)
    assert (m.delivery, got) == (delivery, status), (r, m.content[:400])
    assert len(await outbox(m.external_message_id)) == (delivery == "sent")


@pytest.mark.req("FR-EM-02")
async def test_EM_004_answers_three_questions_with_separate_citations(mail):
    _, _r, m, _ = await mail.sample("EM-004")
    for q in ("What is the drying temperature for PC?", "How long does a desiccant rotor last?", "Do you have a service centre in Coimbatore?"):
        assert q in m.content
    assert len({c["marker"] for c in m.citations}) >= 3 and "Sources" in m.content


@pytest.mark.req("FR-EM-02")
async def test_unanswered_question_goes_to_review(mail):
    mail.intents = ["faq.process", "faq.general"]
    real = agents_node._question

    async def some(state, q, budget):
        if "second" in q:
            return agents_node.AgentResult("no info", abstained=True)
        return await real(state, q, budget)
    mail.mp.setattr(agents_node, "_question", some)
    cid, _ = await mail.deliver(InboundMail("<u1@t>", "a@x.example", "Two", "What is the drying temperature for PC?\nWhat about the second thing here?"))
    [r] = await mail.work()
    assert r["delivery"] == "draft" and r["status"] == "review" and "could not be answered" in r["reason"] and cid


@pytest.mark.req("FR-EM-03")
async def test_low_judge_score_goes_to_review(mail):
    mail.judge = 0.2
    _, _, m, status = await mail.sample("EM-004")
    assert (m.delivery, status) == ("draft", "review")


@pytest.mark.req("FR-EM-04")
async def test_EM_007_unknown_sender_gets_no_customer_data(mail):
    cid, _, m, _ = await mail.sample("EM-007")
    assert (await rows("SELECT customer_id FROM conversations WHERE id = :c", c=cid))[0][0] is None
    assert not any(c["company"] in m.content or c["contacts"][0]["phone"] in m.content for c in CUSTOMERS)


@pytest.mark.req("FR-EM-04")
async def test_EM_008_safety_reply_is_immediate_with_p1(mail):
    cid, _, m, _ = await mail.sample("EM-008")
    assert "switch the machine off" in m.content and (await rows("SELECT priority FROM conversations WHERE id = :c", c=cid))[0][0] == "P1"


@pytest.mark.req("FR-EM-05")
async def test_outgoing_mail_is_threaded_and_branded(mail):
    _, _, m, _ = await mail.sample("EM-001")
    [o] = await outbox(m.external_message_id)
    assert o.to_addr == EMAILS["EM-001"]["from"] and o.subject == "Re: " + EMAILS["EM-001"]["subject"]
    assert o.body_text.startswith("Dear Rohit,") and "Matsui Customer Care" in o.body_html and "AMC-2025-0001" in o.body_text


# ---------- sender (FR-EM-04) ----------

CONTACT, DOMAIN = "rohit.sharma@demo-aarav-polymers.example", "someone.else@demo-aarav-polymers.example"


@pytest.mark.req("FR-EM-04")
@pytest.mark.parametrize(("addr", "auth", "cust", "trust"), [
    (CONTACT, True, "CUST-0001", "contact"), (CONTACT.upper(), True, "CUST-0001", "contact"), (DOMAIN, True, "CUST-0001", "domain"),
    (CONTACT, False, None, None), ("x@gmail.example", True, None, None), ("x@sub.demo-aarav-polymers.example", True, None, None),
    ("rohit.sharma@demo-aarav-polymers.example.evil.com", True, None, None)])
async def test_identify(mail, addr, auth, cust, trust):
    async with get_engine().connect() as c:
        got = await pipeline.identify(c, InboundMail("<i@t>", addr, "s", "t", authenticated=auth))
    assert (got.customer_id, got.trust) == (cust, trust) if cust else got is None


@pytest.mark.req("FR-EM-04")
@pytest.mark.parametrize(("addr", "auth", "delivery", "leak"), [(CONTACT, True, "sent", True), (DOMAIN, True, "draft", True),
                                                               (CONTACT, False, "draft", False), ("x@gmail.example", True, "draft", False)])
async def test_account_answers_by_trust(mail, addr, auth, delivery, leak):
    cid, _ = await mail.deliver(InboundMail(f"<{uuid.uuid4()}@t>", addr, "Is AMC-2025-0001 active?", "Please tell me the status", authenticated=auth))
    [r] = await mail.work()
    [m] = await rows("SELECT content FROM messages WHERE conversation_id = :c AND role = 'assistant'", c=cid)
    assert r["delivery"] == delivery and (("Comprehensive" in m.content) is leak), (r, m.content)


# ---------- threading and idempotency (FR-EM-01) ----------

def inbound(mid, frm=CONTACT, subject="Help", body="Hello there", **kw):
    return InboundMail(f"<{mid}@t>", frm, subject, body, **kw)


@pytest.mark.req("FR-EM-01")
async def test_duplicate_delivery_is_processed_once_even_concurrently(mail):
    res = await asyncio.gather(*(mail.deliver(inbound("dup")) for _ in range(4)))
    assert len({c for c, _ in res}) == 1 and [n for _, n in res].count(True) == 1
    assert len(await mail.work()) == 1
    await mail.deliver(inbound("dup"))
    assert await mail.work() == []


@pytest.mark.req("FR-EM-01")
async def test_reply_attaches_to_the_same_conversation_and_keeps_context(mail):
    mail.intents = ["troubleshoot.error_code"]
    cid, _ = await mail.deliver(inbound("t1", body="My chiller shows LP TRIP"))
    await mail.work()
    [our] = await rows("SELECT external_message_id FROM messages WHERE conversation_id = :c AND role = 'assistant'", c=cid)
    for mid, kw in (("t2", {"in_reply_to": our[0]}), ("t3", {"references": ["<other@x>", "<t1@t>"]})):
        again, _ = await mail.deliver(inbound(mid, subject="Re: Help", body="It is a CHL-10W\n> My chiller shows LP TRIP", **kw))
        assert again == cid
    [a, _] = await mail.work()
    assert a["tier"] == "T3"
    texts = [r[0] for r in await rows("SELECT content FROM messages WHERE conversation_id = :c AND role = 'assistant' ORDER BY created_at", c=cid)]
    assert "Which model" in texts[0] and "Refrigerant leak" in texts[1]


@pytest.mark.req("FR-EM-01")
async def test_ticket_number_in_subject_joins_the_tickets_conversation(mail):
    mail.intents = ["service.create_ticket"]
    cid, _ = await mail.deliver(inbound("s1", body="Please send an engineer. MT-DHD150-23-00417 shows DEW PT HIGH"))
    await mail.work()
    [t] = await rows("SELECT id, customer_id FROM tickets WHERE conversation_id = :c", c=cid)
    assert t.customer_id == "CUST-0001"
    assert (await mail.deliver(inbound("s2", subject=f"About {t.id}")))[0] == cid
    for frm, kw in (("x@gmail.example", {}), (DOMAIN, {}), (CONTACT, {"authenticated": False})):
        assert (await mail.deliver(inbound(str(uuid.uuid4()), frm=frm, subject=f"About {t.id}", **kw)))[0] != cid


@pytest.mark.req("FR-EM-01")
async def test_a_stranger_cannot_join_a_thread_by_message_id(mail):
    cid, _ = await mail.deliver(inbound("h1"))
    for frm, kw in (("attacker@evil.example", {}), (CONTACT, {"authenticated": False})):
        assert (await mail.deliver(inbound(str(uuid.uuid4()), frm=frm, in_reply_to="<h1@t>", **kw)))[0] != cid


# ---------- robustness ----------

async def test_auto_mail_is_stored_but_never_answered(mail):
    cid, new = await mail.deliver(inbound("auto", auto=True))
    assert new and await mail.work() == [] and len(await rows("SELECT 1 FROM messages WHERE conversation_id = :c", c=cid)) == 1


async def test_flood_from_one_sender_goes_to_review(mail):
    mail.mp.setattr(pipeline, "MAX_PER_HOUR", 2)
    for i in range(4):
        cid, _ = await mail.deliver(inbound(f"f{i}", frm="flood@x.example"))
    assert {r.get("skipped") for r in await mail.work()} == {"rate limit"}
    assert (await rows("SELECT status FROM conversations WHERE id = :c", c=cid))[0][0] == "review"


async def test_interrupted_processing_is_not_rerun(mail):
    mail.intents = ["sales.quote"]
    n = (await rows("SELECT count(*) FROM leads"))[0][0]
    cid, _ = await mail.deliver(inbound("int", frm=EMAILS["EM-002"]["from"], body="Quote for 2 chillers"))
    mail.mp.setattr(pipeline, "render", crash_sync)
    await mail.work(expect_failure=True)
    mail.mp.setattr(pipeline, "render", pipeline_render)
    async with get_engine().begin() as c:
        await c.execute(text("UPDATE jobs SET run_after = now() WHERE kind = 'email_reply'"))
    [r] = await mail.work()
    assert r == {"review": "interrupted"} and (await rows("SELECT count(*) FROM leads"))[0][0] == n + 1
    assert (await rows("SELECT status FROM conversations WHERE id = :c", c=cid))[0][0] == "review"


pipeline_render = pipeline.render


def crash_sync(**k):
    raise RuntimeError("worker died")


async def test_human_owned_conversation_is_not_answered(mail):
    cid, _ = await mail.deliver(inbound("hum"))
    async with get_engine().begin() as c:
        await c.execute(text("UPDATE conversations SET status = 'human' WHERE id = :c"), {"c": cid})
    assert (await mail.work())[0]["skipped"].startswith("a person")


async def test_failed_gmail_send_goes_to_review_and_is_not_resent(mail):
    class Broken:
        name, transactional, n = "gmail", False, 0

        async def send(self, m, conn):
            self.n += 1
            raise ConnectionError("gmail down")
    a = Broken()
    cid, _ = await mail.deliver(inbound("snd", subject="Smoke", body="there is smoke from the panel"))
    await mail.work(a)
    assert (await rows("SELECT delivery FROM messages WHERE conversation_id = :c AND role = 'assistant'", c=cid))[0][0] == "failed"
    assert a.n == 1 and await mail.work(a) == []


@pytest.mark.req("FR-EM-06")
async def test_attachments_are_stored_and_listed(mail, tmp_path):
    cid, _ = await mail.deliver(inbound("att", attachments=[MailAttachment("../../etc/pass wd.png", "image/png", b"PNG"),
                                                           MailAttachment("big.bin", "application/octet-stream", b"0" * (pipeline.MAX_ATTACHMENT + 1))]))
    got = await rows("SELECT a.filename, a.mime, a.bytes, a.storage_uri FROM attachments a JOIN messages m ON m.id = a.message_id "
                     "WHERE m.conversation_id = :c ORDER BY a.bytes", c=cid)
    assert got[0].filename == "pass wd.png" and got[0].bytes == 3 and got[1].storage_uri is None
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].read_bytes() == b"PNG" and tmp_path in files[0].parents


@pytest.mark.req("FR-EM-06")
async def test_attachments_go_to_the_bucket(mail, monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "store_file", lambda *a: seen.append(a) or "gs://b/x")
    monkeypatch.setenv("GCS_BUCKET", "b")
    get_settings.cache_clear()
    await mail.deliver(inbound("gcs", attachments=[MailAttachment("a.png", "image/png", b"x")]))
    assert seen[0][4] == "b"


# ---------- HTTP and Gmail sync ----------

@pytest_asyncio.fixture
async def http(mail):
    from app.main import create_app
    from tests.unit.test_staff_auth import SECRET
    mail.mp.setenv("NEXTAUTH_SECRET", SECRET)      # CI has no .env
    get_settings.cache_clear()
    api._hits.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


async def test_contact_form(http, mail, monkeypatch):
    body = {"from": CONTACT, "name": "R", "subject": "Hi", "body": "Hello", "serial_no": "MT-DHD150-23-00417"}
    r = await http.post("/v1/email/inbound", json=body)
    assert r.status_code == 202
    cid = r.json()["conversation_id"]
    assert (await rows("SELECT customer_id FROM conversations WHERE id = :c", c=cid))[0][0] == "CUST-0001"
    assert "MT-DHD150-23-00417" in (await rows("SELECT content FROM messages WHERE conversation_id = :c", c=cid))[0][0]
    assert (await http.post("/v1/email/inbound", json={**body, "from": "a@b\r\nBcc: x@y.example"})).status_code == 422
    for env in ({"MAIL_ADAPTER": "gmail"}, {"MAIL_ADAPTER": "demo", "DEMO_PERSONAS": "false"}):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        cid = (await http.post("/v1/email/inbound", json=body)).json()["conversation_id"]
        assert (await rows("SELECT customer_id FROM conversations WHERE id = :c", c=cid))[0][0] is None      # typed address isn't proof
    codes = [(await http.post("/v1/email/inbound", json=body)).status_code for _ in range(api.PER_MINUTE + 1)]
    assert codes[-1] == 429


async def test_gmail_push_requires_a_valid_identity(http, monkeypatch):
    data = base64.b64encode(json.dumps({"emailAddress": "demo@m.example", "historyId": 77}).encode()).decode()
    push = {"message": {"data": data}}
    assert (await http.post("/v1/email/gmail/push", json=push)).status_code == 404          # not configured
    for k, v in {"GMAIL_PUSH_AUDIENCE": "aud", "GMAIL_PUSH_SERVICE_ACCOUNT": "push@p.example", "GMAIL_DELEGATED_USER": "demo@m.example"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    claims = {"email": "push@p.example", "email_verified": True}

    def verify(token, req, aud):
        if token != "good":
            raise ValueError("bad")
        assert aud == "aud"
        return claims
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", verify)
    assert (await http.post("/v1/email/gmail/push", json=push)).status_code == 401
    ok = {"Authorization": "Bearer good"}
    assert (await http.post("/v1/email/gmail/push", json=push, headers=ok)).status_code == 204
    assert (await rows("SELECT payload FROM jobs WHERE kind = 'gmail_sync'"))[0][0] == {"history_id": "77"}
    assert (await http.post("/v1/email/gmail/push", json={"message": {}}, headers=ok)).status_code == 400
    claims["email"] = "other@p.example"
    assert (await http.post("/v1/email/gmail/push", json=push, headers=ok)).status_code == 403


@pytest.mark.req("FR-EM-01")
async def test_gmail_sync_cursor_dedupe_and_expired_history(mail):
    class G:
        def __init__(self, expired=False):
            self.expired, self.since = expired, []

        async def watch(self, conn):
            return "5"

        async def new_messages(self, since):
            self.since.append(since)
            if self.expired:
                r = httpx.Response(404, request=httpx.Request("GET", "http://x"))
                raise httpx.HTTPStatusError("gone", request=r.request, response=r)
            return ["1", "2"], "20"

        async def recent_ids(self):
            return ["1", "3"], "30"

        async def fetch(self, i):
            return inbound(f"g{i}", frm=get_settings().support_address if i == "2" else CONTACT)

    async def run(g, hist="9"):
        async with get_engine().begin() as c:
            await pipeline.enqueue(c, "gmail_sync", {"history_id": hist})
        return (await rows("SELECT result FROM jobs WHERE id = :i", i=(await run_once(get_engine(), {"gmail_sync": make_gmail_sync_handler(get_engine(), g)})).id))[0][0]
    g = G()
    assert await run(g) == {"fetched": 2, "new": 1} and g.since == ["9"]            # our own mail is skipped
    assert await run(g) == {"fetched": 2, "new": 0} and g.since[-1] == "20"
    assert await run(G(expired=True)) == {"fetched": 2, "new": 1}
    assert (await rows("SELECT value FROM mail_state"))[0][0] == "30"
    assert make_gmail_watch_handler(get_engine(), g)


async def test_mailbox_view_needs_the_agent_role(http):
    from tests.unit.test_staff_auth import token
    assert (await http.get("/v1/console/mailbox")).status_code == 401
    assert (await http.get("/v1/console/mailbox?box=outbox", headers=token("agent"))).status_code == 200


async def test_send_sample_emails(http, monkeypatch):
    from tests.unit.test_staff_auth import token
    for _ in range(2):                                     # every click is a new set
        r = await http.post("/v1/demo/send-sample-emails", headers=token("admin"))
        assert (r.status_code, r.json()) == (202, {"count": 10})
    monkeypatch.setenv("MAIL_ADAPTER", "gmail")
    get_settings.cache_clear()
    assert (await http.post("/v1/demo/send-sample-emails", headers=token("admin"))).status_code == 403
