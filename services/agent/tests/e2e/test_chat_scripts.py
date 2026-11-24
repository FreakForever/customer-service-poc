"""Chat and email scripts through the real graph, knowledge graph and mcs_test (BS-17; FR-AG-01/02/04/05, FR-OR-05).
Only the intent classifier and the model's replies are scripted. Expected facts come from the catalog."""

import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.db import get_engine, warm_pool
from app.ingest.seed_kb import seed_kb
from app.obs.tracing import TraceRecorder
from app.orchestrator.graph import build_graph, run_turn, session_of
from app.orchestrator.nodes import agents as node
from app.orchestrator.state import CustomerRef, Envelope, Intent
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.rerank import NoopReranker

pytestmark = pytest.mark.network
AGENT = Path(__file__).resolve().parents[2]
DATA = AGENT.parents[1] / "mock-data" / "data"
SCRIPTS = {c["id"]: c for c in json.loads((DATA / "channels/chat_scripts.json").read_text())}
EMAILS = {e["id"]: e for e in json.loads((DATA / "channels/emails.json").read_text())}
CUSTOMERS = json.loads((DATA / "ops/customers.json").read_text())


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
async def chat(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config.settings import get_settings
    from app.kg.client import get_read_driver
    for f in (get_settings, get_engine, get_read_driver):
        f.cache_clear()
    await warm_pool()
    await get_read_driver().verify_connectivity()
    monkeypatch.setattr(node, "get_reranker", NoopReranker)

    class Chat:
        def __init__(self):
            self.session, self.customer, self.channel, self.script, self.turns = {}, None, "chat", {}, []

        async def say(self, message, *intents, meta=None):
            async def classify(state):
                if state.get("intents"):
                    return {}                        # rule intents stand, as in the real node
                return {"intents": [Intent(name=n, confidence=0.9 - i / 100, source="knn") for i, n in enumerate(intents)]}
            monkeypatch.setattr(node, "get_llm", lambda: ResilientLLM(FakeProvider(script=dict(self.script))))
            rec = TraceRecorder()
            f = await run_turn(Envelope(channel=self.channel, text=message, customer=self.customer, meta=meta or {}),
                               graph=build_graph({"classify": classify}), recorder=rec, session=self.session)
            # sessions are stored as JSON between turns
            self.session = json.loads(json.dumps(session_of(f)))
            self.turns.append((f, rec))
            assert f.get("budget_exceeded") is None, f["path"]
            return f

    yield Chat()
    await get_engine().dispose()
    await get_read_driver().close()
    get_read_driver.cache_clear()


def reply(f):
    return f["response"]["text"]


async def rows(sql, **p):
    async with get_engine().connect() as c:
        return (await c.execute(text(sql), p)).all()


def customer(eid):
    addr = EMAILS[eid]["from"]
    c = next((c for c in CUSTOMERS if any(x["email"] == addr for x in c["contacts"])), None) or \
        next(c for c in CUSTOMERS if c["email_domain"] == addr.split("@")[1])
    return CustomerRef(customer_id=c["id"], contact_email=addr, trust="contact")


def body(eid):
    return f"{EMAILS[eid]['subject']}\n{EMAILS[eid]['body']}"


@pytest.mark.req("FR-OR-05")
@pytest.mark.parametrize("script", ["CH-002"])          # the id the verify command selects
async def test_sticky_troubleshooting_then_parts(chat, script):
    t = SCRIPTS[script]["turns"]
    a = await chat.say(t[0], "troubleshoot.error_code")
    assert "Which model" in reply(a) and "Low refrigerant pressure" in reply(a)
    b = await chat.say(t[1])
    assert "Most likely cause: Refrigerant leak" in reply(b) and "Evaporator freezing" in reply(b)
    assert "Safety:" in reply(b) and reply(b).index("You can check") < reply(b).index("Needs a technician")
    assert b["response"]["citations"][0]["evidence_id"].startswith("kg:T-DIAG:CHL-10W>CHL-A02")
    c = await chat.say(t[2])
    assert "MT-CHL-REFKIT" in reply(c) and "WH-PUN" in reply(c)
    assert [f["answered_tier"] for f, _ in chat.turns] == SCRIPTS[script]["expect"]["tiers"]
    for f, rec in chat.turns[1:]:
        assert not {"classify", "llm_router"} & set(f["path"])
        assert next(s for s in rec.spans if s.node == "agents").attrs["sticky_agent"] == "troubleshoot"


@pytest.mark.req("FR-AG-02")
@pytest.mark.parametrize("script", ["CH-004"])          # the id the verify command selects
async def test_ticket_by_slot_filling(chat, script):
    chat.customer = CustomerRef(customer_id="CUST-0010")              # owns MT-GR200-24-00233
    t = SCRIPTS[script]["turns"]
    await chat.say(t[0], "complaint")
    b = await chat.say(t[1])
    assert "Most likely cause: Overfeeding" in reply(b) and "serial number" in reply(b)
    c = await chat.say(t[2])
    assert "phone number or email" in reply(c) and c["response"]["answered_tier"] == "T0"
    d = await chat.say(t[3])
    assert [f["answered_tier"] for f, _ in chat.turns] == SCRIPTS[script]["expect"]["tiers"]
    tid = d["draft"].actions[0]["ticket_id"]
    assert tid in reply(d) and d["active_agent"] is None
    [r] = await rows("SELECT customer_id, serial_no, error_code, priority, status FROM tickets WHERE id = :i", i=tid)
    assert tuple(r) == ("CUST-0010", "MT-GR200-24-00233", "RCY-G1", "P1", "new")     # G1 is a safety stop


@pytest.mark.req("FR-AG-01")
@pytest.mark.parametrize("script", ["CH-005"])          # the id the verify command selects
async def test_two_symptoms_one_cause_and_ruled_out_filter(chat, script):
    f = await chat.say(SCRIPTS[script]["turns"][0], "troubleshoot.symptom")
    assert f["answered_tier"] == "T3"
    top = reply(f).split("Most likely cause: ")[1].split("\n")[0]
    assert top.startswith("Regeneration heater open circuit.") and "explains both E03 and E04" in top
    assert "already checked: Air filter" in reply(f)
    assert "Air filter" not in reply(f).split("I've set aside")[0]
    assert {"kg:T-DIAG:DHD-100>DHD-E03>C-RHTR-OPEN>R-DHD-C-RHTR-OPEN", "kg:T-DIAG:DHD-100>DHD-E04>C-RHTR-OPEN>R-DHD-C-RHTR-OPEN"} \
        <= {c["evidence_id"] for c in f["response"]["citations"]}


async def test_yes_after_diagnosis_prefills_and_confirms(chat):
    chat.customer = CustomerRef(customer_id="CUST-0010")
    await chat.say("GR-200 MT-GR200-24-00233 shows MOTOR OL", "troubleshoot.error_code")
    a = await chat.say("yes please")
    assert "phone number or email" in reply(a)
    b = await chat.say("rohit@demo.example")
    assert "Shall I raise this?" in reply(b) and "MT-GR200-24-00233" in reply(b)
    assert not await rows("SELECT 1 FROM tickets WHERE customer_id = 'CUST-0010' AND summary LIKE 'GR-200 G1%'")
    c = await chat.say("yes")
    assert c["draft"].actions[0]["type"] == "ticket_created"


async def test_fsm_rejects_foreign_serial_cancel_and_rule_switch(chat):
    chat.customer = CustomerRef(customer_id="CUST-0001")
    await chat.say("please send an engineer", "service.create_ticket")
    n = (await rows("SELECT count(*) FROM tickets"))[0][0]
    f = await chat.say("serial MT-GR200-24-00233")
    assert "couldn't find" in reply(f) and f["active_agent"] == "service_request"
    f = await chat.say("status of SR-2026-00112")                        # rule intent ends the sticky agent
    assert f["active_agent"] is None and f["answered_tier"] == "T0"
    await chat.say("please send an engineer", "service.create_ticket")
    f = await chat.say("never mind")
    assert f["active_agent"] is None and (await rows("SELECT count(*) FROM tickets"))[0][0] == n


@pytest.mark.req("FR-OR-05")
async def test_unrelated_turn_is_handed_back_to_the_router(chat):
    await chat.say("my chiller shows LP TRIP", "troubleshoot.error_code")
    f = await chat.say("how long does shipping normally take to Nepal please", "sales.quote")
    assert f["path"].index("agents") < f["path"].index("classify") and "quotation" in reply(f)


@pytest.mark.req("FR-AG-01")
@pytest.mark.parametrize(("eid", "cause", "also"), [
    ("EM-001", "cause: Desiccant rotor degraded", "already checked: Air filter"),
    ("EM-006", "cause: Pump seized", "also check early: Scale build-up"),
    ("EM-010", "causes (equally likely): Conveying hose blocked by lumps or long strands [E2]; Vacuum leak", "already checked: Material bridging")])
async def test_email_diagnoses(chat, eid, cause, also):
    chat.channel, chat.customer = "email", customer(eid)
    f = await chat.say(body(eid), "troubleshoot.error_code")
    assert f"Most likely {cause}" in reply(f) and also in reply(f)
    assert any(c["evidence_id"].startswith("DOC-MAN-") for c in f["response"]["citations"])


async def test_EM_001_multi_intent_sections(chat):
    chat.channel, chat.customer = "email", customer("EM-001")
    f = await chat.say(body("EM-001"), "troubleshoot.error_code", "troubleshoot.symptom", "amc.status")
    assert "**Troubleshooting**" in reply(f) and "**AMC**" in reply(f) and "AMC-2025-0001: Comprehensive AMC, expired" in reply(f)
    assert f["answered_tier"] == "T3" and reply(f).count("**Troubleshooting**") == 1


async def test_EM_003_complaint_with_ticket_hands_off(chat):
    chat.channel = "email"         # the sender's domain isn't in customers.json: use the ticket's owner
    [r] = await rows("SELECT customer_id FROM tickets WHERE id = 'SR-2026-00105'")
    chat.customer = CustomerRef(customer_id=r[0])
    f = await chat.say(body("EM-003"), "service.ticket_status", "complaint")
    assert "SR-2026-00105" in reply(f) and f["handoff"].reason == "complaint" and f["handoff"].action == "handoff"


@pytest.mark.req("FR-AG-04")
async def test_EM_005_parts_price_and_ludhiana_supply(chat):
    chat.channel, chat.customer = "email", customer("EM-005")
    f = await chat.say(body("EM-005"), "parts.availability")
    assert "MT-RCY-BLADESET" in reply(f) and "INR 19800" in reply(f)
    assert "not in stock at WH-NOI, which supplies Ludhiana" in reply(f) and "WH-CHE 4" in reply(f)
    assert "MT-RCY-BELT" not in reply(f) and "MT-RCY-SCREEN" not in reply(f)
    q = await chat.say("please send a quote for 2 sets")
    again = await chat.say("parts quote for 2 sets", "parts.availability")
    qid = q["draft"].actions[0]["quote_request_id"]
    assert (await rows("SELECT lines FROM parts_orders WHERE id = :i", i=qid))[0][0][0]["qty"] == 2
    assert len(await rows("SELECT 1 FROM parts_orders WHERE status = 'quote_requested'")) == 1 and again


@pytest.mark.req("FR-AG-05")
async def test_EM_002_creates_one_lead(chat):
    chat.channel, chat.customer = "email", customer("EM-002")
    chat.script = {"lead.v1": json.dumps({"product_interest": "2 chillers", "application": "PP moulding",
                                          "throughput": "450T and 650T machines", "location": "Chakan", "contact_name": "Priya"})}
    f = await chat.say(body("EM-002"), "sales.quote")
    assert "2 business days" in reply(f) and f["answered_tier"] == "T0"
    f = await chat.say(body("EM-002"), "sales.quote")
    got = await rows("SELECT product_interest, application, throughput, location, contact FROM leads WHERE location = 'Chakan'")
    assert len(got) == 1 and got[0][0] == "2 chillers" and got[0][4] == {"name": "Priya", "email": EMAILS["EM-002"]["from"]}


async def test_lead_survives_a_model_outage(chat):
    chat.channel, chat.customer = "email", customer("EM-002")
    await chat.say("Quote for a granulator at Nashik", "sales.quote")          # no scripted reply → extraction fails
    assert await rows("SELECT 1 FROM leads WHERE product_interest LIKE '%granulator at Nashik%'")


async def test_agent_failure_hands_off(chat, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("graph down")
    monkeypatch.setattr("app.agents.troubleshoot.run_template", down)
    f = await chat.say("my CHL-10W shows LP TRIP", "troubleshoot.error_code")
    assert f["handoff"].action == "handoff" and "service team" in reply(f) and "[stub]" not in reply(f)


