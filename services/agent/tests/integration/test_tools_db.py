"""PgRepo scoping and writes on mcs_test (BS-16; FR-AG-03, FR-AG-06). Writes are rolled back."""

import asyncio
import datetime as dt
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.tools.account import ToolContext, call_tool
from app.tools.repo import PgRepo

pytestmark = [pytest.mark.network, pytest.mark.asyncio(loop_scope="module"), pytest.mark.req("FR-AG-06")]
AGENT = Path(__file__).resolve().parents[2]
OWN = "MT-DHD150-23-00417"
CONTACT = {"name": "Rohit", "email": "r@demo.example"}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    for args in (("downgrade", "base"), ("upgrade", "head")):
        await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT,
                                check=True, capture_output=True, text=True)
    spec = importlib.util.spec_from_file_location("seed_script", AGENT.parents[1] / "scripts" / "seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sync = create_engine(url)
    with sync.begin() as c:
        mod.seed(c)
    sync.dispose()
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def conn(engine):
    async with engine.connect() as c:
        yield c
        await c.rollback()


def ctx(conn, customer):
    return ToolContext(PgRepo(conn), customer_id=customer, today=dt.date(2026, 11, 23))


@pytest.mark.parametrize(("tool", "args", "owner"), [
    ("get_ticket_status", {"ticket_id": "SR-2026-00101"}, "CUST-0005"), ("get_asset", {"serial_no": OWN}, "CUST-0001"),
    ("get_warranty", {"serial_no": OWN}, "CUST-0001"), ("get_amc_contract", {"serial_no": OWN}, "CUST-0001"),
    ("get_amc_contract", {"contract_id": "AMC-2025-0001"}, "CUST-0001")])
async def test_reads_are_scoped_in_sql(conn, tool, args, owner):
    assert (await call_tool(tool, args, ctx(conn, owner))).status == "ok"
    for other in ("CUST-0009", "", "' OR '1'='1"):
        r = await call_tool(tool, args, ctx(conn, other))
        assert r.status != "ok" and r.data == {}


async def test_unverified_ticket_belongs_to_no_customer(conn):
    r = await call_tool("create_ticket", {"serial_no": OWN, "description": "x", "contact": CONTACT}, ctx(conn, None))
    assert r.data["ticket_id"].startswith("SR-") and r.data["unverified"]
    again = await call_tool("get_ticket_status", {"ticket_id": r.data["ticket_id"]}, ctx(conn, "CUST-0001"))
    assert again.status == "not_found"


async def test_writes(conn):
    c = ctx(conn, "CUST-0001")
    t = await call_tool("create_ticket", {"serial_no": OWN, "description": "hot", "error_code": "E02",
                                          "contact": CONTACT}, c)
    assert t.data["priority"] == "P1"                      # DHD-E02 safety_stop, resolved via the asset's family
    assert (await call_tool("get_ticket_status", {"ticket_id": t.data["ticket_id"]}, c)).data["status"] == "new"
    q = await call_tool("create_parts_quote", {"part_no": "MT-DHD-ROTOR", "qty": 1, "serial_no": OWN}, c)
    row = (await conn.execute(text("SELECT customer_id, status FROM parts_orders WHERE id = :i"),
                              {"i": q.data["quote_request_id"]})).one()
    assert tuple(row) == ("CUST-0001", "quote_requested")
    lead = await call_tool("create_lead", {"product_interest": "chiller", "application": "PET", "throughput": "1",
                                           "location": "Pune", "contact": CONTACT}, c)
    assert (await conn.execute(text("SELECT count(*) FROM leads WHERE id = CAST(:i AS uuid)"),
                               {"i": lead.data["lead_id"]})).scalar() == 1


async def test_part_stock(conn):
    r = await call_tool("get_part_availability", {"part_no": "MT-DHD-ROTOR"}, ctx(conn, None))
    assert r.data["stock"]["WH-PUN"] == 1 and r.data["lead_time_days"] == 23
