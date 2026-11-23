"""Seed loader (BS-04, LLD-13 "Seed") against `mcs_test`: counts match SUMMARY.json, re-runs are idempotent,
uploaded rows survive, fixtures the demo relies on are present."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config.settings import Settings

pytestmark = pytest.mark.network
AGENT_DIR = Path(__file__).resolve().parents[2]
ROOT = AGENT_DIR.parents[1]
DATA = ROOT / "mock-data" / "data"
SUMMARY = json.loads((DATA / "SUMMARY.json").read_text())["counts"]


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seed_mod = _seed_module()


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set (run `make env` and `make proxy`)")
    for args in (("downgrade", "base"), ("upgrade", "head")):  # start from an empty schema
        subprocess.run(["uv", "run", "alembic", "-x", f"url={url}", *args], cwd=AGENT_DIR, check=True,
                       capture_output=True, text=True)
    eng = create_engine(url)
    with eng.begin() as conn:
        seed_mod.seed(conn)
    yield eng
    eng.dispose()


def count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


EXPECTED = {
    "customers": SUMMARY["customers"], "assets": SUMMARY["assets"], "amc_contracts": SUMMARY["amc_contracts"],
    "tickets": SUMMARY["tickets"], "models": SUMMARY["models"], "error_codes": SUMMARY["error_codes"],
    "spare_parts": SUMMARY["spare_parts"], "faq_entries": SUMMARY["faqs"],
    "parts_orders": len(json.loads((DATA / "ops" / "parts_orders.json").read_text())),
}
N_VARIANTS = len({seed_mod.normalize(t) for f in json.loads((DATA / "kb" / "faqs.json").read_text())
                  for t in [f["question"], *f["paraphrases"]]})


@pytest.mark.req("FR-KB-06")
def test_row_counts_match_summary(engine):
    with engine.connect() as conn:
        assert {t: count(conn, t) for t in EXPECTED} == EXPECTED
        assert count(conn, "faq_variants") == N_VARIANTS


@pytest.mark.req("FR-KB-06")
def test_reseed_is_idempotent_and_keeps_uploaded_rows(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO error_codes (id, family, code, title, source) "
                          "VALUES ('DHD-E99', 'DHD', 'E99', 'Uploaded code', 'csv:job-1')"))
        conn.execute(text("UPDATE spare_parts SET source = 'csv:job-1', price_inr = 1 WHERE part_no = 'MT-MTC-HTR'"))
        conn.execute(text("UPDATE customers SET company = 'edited' WHERE id = 'CUST-0001'"))
        seed_mod.seed(conn)
        seed_mod.seed(conn)
        assert count(conn, "error_codes") == EXPECTED["error_codes"] + 1
        assert count(conn, "faq_variants") == N_VARIANTS
        assert conn.execute(text("SELECT price_inr FROM spare_parts WHERE part_no = 'MT-MTC-HTR'")).scalar() == 1
        assert conn.execute(text("SELECT company FROM customers WHERE id = 'CUST-0001'")).scalar() == "Aarav Polymers"
        # restore the pristine seed for the other tests
        conn.execute(text("DELETE FROM error_codes WHERE id = 'DHD-E99'"))
        conn.execute(text("UPDATE spare_parts SET source = 'seed' WHERE part_no = 'MT-MTC-HTR'"))
        seed_mod.seed(conn)


def test_demo_fixtures_and_field_mapping(engine):
    with engine.connect() as conn:
        a = conn.execute(text("SELECT customer_id, warranty_end FROM assets WHERE serial_no = 'MT-DHD150-23-00417'")).one()
        assert a.customer_id == "CUST-0001" and str(a.warranty_end) == "2024-06-11"
        amc = conn.execute(text("SELECT status, end_date, serials FROM amc_contracts WHERE id = 'AMC-2025-0014'")).one()
        assert amc.status == "grace" and str(amc.end_date) == "2026-11-03" and len(amc.serials) == 2
        t = conn.execute(text("SELECT priority, history FROM tickets WHERE id = 'SR-2026-00105'")).one()
        assert t.priority == "P1" and isinstance(t.history, list)
        ec = conn.execute(text("SELECT causes, safety_stop FROM error_codes WHERE id = 'MTC-E01'")).one()
        assert ec.safety_stop and ec.causes[0]["cause_id"] == "C-LOW-WATER"
        sp = conn.execute(text("SELECT stock, lead_time_days FROM spare_parts WHERE part_no = 'MT-MTC-HTR'")).one()
        assert sp.stock["WH-AMD"] == 6 and sp.lead_time_days == 3
        contacts = conn.execute(text("SELECT contacts FROM customers WHERE id = 'CUST-0001'")).scalar()
        assert contacts[0]["email"].endswith(".example")   # DR-03: placeholder contact details
        v = conn.execute(text("SELECT faq_id, embedding FROM faq_variants WHERE normalized = 'how long is the warranty'")).one()
        assert v.faq_id == "FAQ-001" and v.embedding is None   # embeddings come from seed --kb (BS-07)


def test_ticket_sequence_continues_after_seeded_tickets(engine):
    with engine.begin() as conn:
        assert conn.execute(text("SELECT nextval('ticket_seq')")).scalar() == 141
        conn.execute(text("SELECT setval('ticket_seq', 150)"))
        seed_mod.seed(conn)   # never moves backwards
        assert conn.execute(text("SELECT nextval('ticket_seq')")).scalar() == 151
        conn.execute(text("SELECT setval('ticket_seq', 140)"))


def test_every_seed_row_is_synthetic():
    assert json.loads((DATA / "SUMMARY.json").read_text())["synthetic"] is True
    for c in json.loads((DATA / "ops" / "customers.json").read_text()):
        assert c["demo_label"].startswith("synthetic")
        assert all(ct["email"].endswith(".example") for ct in c["contacts"])
