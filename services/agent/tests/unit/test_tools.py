"""Customer-scoped tools (BS-16, LLD-10; FR-AG-03, FR-AG-06). In-memory repo from mock data; no database."""

import datetime as dt
import json
from pathlib import Path

import pytest

from app.tools import account as acc
from app.tools.account import TOOLS, ToolContext, amc_status, call_tool, ticket_priority
from app.tools.render import render

DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data"
TODAY = dt.date(2026, 11, 23)
CONTACT = {"name": "Rohit", "phone": "+91-90000-000010"}


def load(rel):
    return json.loads((DATA / rel).read_text())


def day(v):
    return dt.date.fromisoformat(v) if v else None


class MemRepo:
    def __init__(self):
        self.tickets = load("ops/tickets.json")
        self.assets = [{**a, **{k: day(a[k]) for k in ("warranty_end", "commissioned_on", "dispatched_on")}}
                       for a in load("ops/assets.json")]
        self.amc = [{**c, "start_date": day(c["start"]), "end_date": day(c["end"])} for c in load("ops/amc_contracts.json")]
        self.parts = {p["part_no"]: {**p, "lead_time_days": p["lead_time_days_if_unavailable"]}
                      for p in load("catalog/spare_parts.json")}
        self.errors, self.written = load("catalog/error_codes.json"), []

    async def ticket(self, ticket_id, customer_id):
        return next((t for t in self.tickets if t["id"] == ticket_id and t["customer_id"] == customer_id), None)

    async def asset(self, serial_no, customer_id):
        return next((a for a in self.assets if a["serial_no"] == serial_no and a["customer_id"] == customer_id), None)

    async def contract(self, customer_id, *, contract_id, serial_no):
        m = [c for c in self.amc if c["customer_id"] == customer_id and contract_id in (None, c["id"])
             and (serial_no is None or serial_no in c["serials"])]
        return max(m, key=lambda c: c["end_date"], default=None)

    async def part(self, part_no):
        return self.parts.get(part_no)

    async def safety_stop(self, error_code, serial_no, customer_id):
        a = await self.asset(serial_no, customer_id)
        return any(e["safety_stop"] for e in self.errors
                   if e["id"] == error_code or (a and e["family"] == a["family"] and e["code"] == error_code))

    async def service_centre(self, city):
        c = next((c for c in load("ops/service_centres.json") if city.lower() in map(str.lower, c["covers"])), None)
        return c and {**c, "name": c["city"], "home_warehouse": c["warehouse"]}

    async def insert_ticket(self, row):
        self.written.append(row)
        return "SR-2026-00141"

    async def insert_quote(self, row):
        self.written.append(row)

    async def insert_lead(self, row):
        self.written.append(row)
        return "lead-1"


@pytest.fixture
def repo():
    return MemRepo()


def ctx(repo, customer="CUST-0001", **kw):
    return ToolContext(repo, customer_id=customer, today=TODAY, **kw)


OWN, OWN_AMC = "MT-DHD150-23-00417", "AMC-2025-0001"      # CUST-0001
SCOPED = [("get_ticket_status", {"ticket_id": "SR-2026-00101"}, "CUST-0005"),
          ("get_asset", {"serial_no": OWN}, "CUST-0001"), ("get_warranty", {"serial_no": OWN}, "CUST-0001"),
          ("get_amc_contract", {"contract_id": OWN_AMC}, "CUST-0001"),
          ("get_amc_contract", {"serial_no": OWN}, "CUST-0001")]


@pytest.mark.req("FR-AG-03")
async def test_account_reads_for_the_owner(repo):
    t = (await call_tool("get_ticket_status", {"ticket_id": "sr-2026-00101"}, ctx(repo, "CUST-0005"))).data
    assert (t["status"], t["priority"], t["engineer"]) == ("in_progress", "P1", "Tanvi Shetty") and t["last_events"]
    a = (await call_tool("get_asset", {"serial_no": OWN}, ctx(repo))).data
    assert a["model"] == "DHD-150" and a["amc_id"] == OWN_AMC
    w = (await call_tool("get_warranty", {"serial_no": OWN}, ctx(repo))).data
    assert w["in_warranty"] is False and w["warranty_end"] == dt.date(2024, 6, 11)
    k = (await call_tool("get_amc_contract", {"serial_no": OWN}, ctx(repo))).data
    assert (k["contract_id"], k["status"], k["renewal_needs_inspection"]) == (OWN_AMC, "expired", True)
    assert "price_inr" not in k


@pytest.mark.req("FR-AG-06")
@pytest.mark.parametrize(("tool", "args", "owner"), SCOPED)
async def test_scope_owner_other_and_anonymous(repo, tool, args, owner):
    assert (await call_tool(tool, args, ctx(repo, owner))).status == "ok"
    other = await call_tool(tool, args, ctx(repo, "CUST-0009"))
    key = next(iter(args))
    ghost = await call_tool(tool, {key: {"ticket_id": "SR-2026-99999", "serial_no": "MT-DHD150-23-99999",
                                         "contract_id": "AMC-2025-9999"}[key]}, ctx(repo, "CUST-0009"))
    assert other == ghost and other.status == "not_found" and other.data == {}   # no existence oracle
    anon = await call_tool(tool, args, ctx(repo, None))
    assert anon.status == "needs_verification" and anon.data == {}


@pytest.mark.req("FR-AG-06")
async def test_customer_cannot_be_passed_as_an_argument_and_tools_are_allow_listed(repo):
    r = await call_tool("get_amc_contract", {"contract_id": OWN_AMC, "customer_id": "CUST-0001"}, ctx(repo, "CUST-0009"))
    assert r.status == "invalid"
    assert (await call_tool("run_sql", {"q": "select 1"}, ctx(repo))).status == "invalid"
    assert all("customer_id" not in t.args.model_fields for t in TOOLS.values())
    assert (await call_tool("get_asset", {"serial_no": "' OR 1=1 --"}, ctx(repo))).status == "invalid"
    assert (await call_tool("get_amc_contract", {}, ctx(repo))).status == "invalid"


def test_amc_status_boundaries():
    end = dt.date(2026, 11, 3)
    assert [amc_status(end, d) for d in (end, dt.date(2026, 11, 4), dt.date(2026, 12, 3), dt.date(2026, 12, 4))] == [
        "active", "grace", "grace", "expired"]


async def test_warranty_last_day_is_covered(repo):
    c = ctx(repo)
    c.today = dt.date(2024, 6, 11)
    assert (await call_tool("get_warranty", {"serial_no": OWN}, c)).data["in_warranty"]


async def test_public_tools_need_no_customer(repo):
    p = (await call_tool("get_part_availability", {"part_no": "mt-dhd-rotor", "city": "Pune"}, ctx(repo, None))).data
    assert p["home_warehouse"] == "WH-PUN" and p["stock"] == repo.parts["MT-DHD-ROTOR"]["stock"]
    assert (await call_tool("get_part_availability", {"part_no": "MT-XXX-NOPE"}, ctx(repo, None))).status == "not_found"
    s = await call_tool("find_service_centre", {"city": "pune"}, ctx(repo, None))
    assert s.data["warehouse"] == "WH-PUN" and "placeholder" in s.data["phone"]
    assert (await call_tool("find_service_centre", {"city": "Atlantis"}, ctx(repo, None))).status == "not_found"


@pytest.mark.parametrize(("text", "kw", "want"), [
    ("no heat", {}, "P3"), ("running at reduced output", {}, "P2"), ("production is stopped", {}, "P1"),
    ("no heat", {"safety": True}, "P1"), ("no heat", {"safety_stop": True}, "P1"),
    ("no heat", {"asked": "P1"}, "P1"), ("production stopped", {"asked": "P3"}, "P1"),
])
def test_ticket_priority(text, kw, want):
    assert ticket_priority(text, **({"safety": False, "safety_stop": False, "asked": None} | kw)) == want


@pytest.mark.req("FR-AG-06")
async def test_create_ticket_ownership_and_unverified_flag(repo):
    args = {"serial_no": OWN, "description": "Dew point high", "contact": CONTACT}
    assert (await call_tool("create_ticket", args, ctx(repo, "CUST-0009"))).status == "not_found" and repo.written == []
    ok = await call_tool("create_ticket", {**args, "error_code": "e02"}, ctx(repo))
    assert ok.data["priority"] == "P1" and repo.written[-1]["model"] == "DHD-150"      # DHD E02 is a safety stop
    un = await call_tool("create_ticket", args, ctx(repo, None))
    assert un.data["unverified"] and repo.written[-1]["customer_id"] is None
    assert repo.written[-1]["model"] is None and repo.written[-1]["history"][0]["flag"] == "unverified_customer"
    assert (await call_tool("create_ticket", {**args, "contact": {"name": "x"}}, ctx(repo))).status == "invalid"


async def test_create_quote_and_lead(repo):
    q = {"part_no": "MT-DHD-ROTOR", "qty": 2}
    assert (await call_tool("create_parts_quote", q, ctx(repo, None))).status == "needs_verification"
    assert (await call_tool("create_parts_quote", {**q, "serial_no": OWN}, ctx(repo, "CUST-0009"))).status == "not_found"
    assert (await call_tool("create_parts_quote", {**q, "qty": 0}, ctx(repo))).status == "invalid" and not repo.written
    assert (await call_tool("create_parts_quote", q, ctx(repo))).data["quote_request_id"].startswith("QR-")
    lead = {"product_interest": "chiller", "application": "PET", "throughput": "100 kg/h", "location": "Pune",
            "contact": CONTACT}
    assert (await call_tool("create_lead", lead, ctx(repo, None))).data == {"lead_id": "lead-1"}


async def test_rendering(repo):
    r = await call_tool("get_amc_contract", {"contract_id": "AMC-2025-0014"}, ctx(repo, "CUST-0008"))
    assert render(r).startswith("AMC-2025-0014: Comprehensive AMC, grace, ends 2026-11-03.")
    assert render(await call_tool("get_ticket_status", {"ticket_id": "SR-2026-00101"}, ctx(repo, None))) == acc.NEEDS_VERIFICATION


async def test_node_hands_off_when_the_database_fails(monkeypatch):
    from app.obs.tracing import TraceRecorder
    from app.orchestrator.graph import run_turn
    from app.orchestrator.nodes import tools as node
    from app.orchestrator.state import CustomerRef, Envelope

    class Down:
        def connect(self):
            raise ConnectionError("db down")
    monkeypatch.setattr(node, "get_engine", Down)
    env = Envelope(channel="chat", text="status of SR-2026-00101", customer=CustomerRef(customer_id="CUST-0005"))
    f = await run_turn(env, recorder=TraceRecorder())
    assert f["path"][-2:] == ["handoff_or_review", "respond"] and f["response"]["text"] == node.UNAVAILABLE
