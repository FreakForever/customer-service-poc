"""Load mock data into Postgres idempotently (BS-04, LLD-13 "Seed", FR-KB-06).

    cd services/agent && uv run python ../../scripts/seed.py [--url postgresql+psycopg://...] [--data DIR]
    ... seed.py --kb      # KB documents + FAQ variant embeddings: added by BS-07

Loads mock-data/data/{ops,catalog,kb/faqs.json}. Every table is upserted on its primary key, so re-running
converges to the generated data without duplicates. Rows that came from runtime uploads (`source`/`origin` not
'seed') are never overwritten or deleted. All data is synthetic (DR-03).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "mock-data" / "data"
J = "jsonb"  # marker: serialise the value to JSON and cast


def _load(data: Path, rel: str) -> list[dict[str, Any]]:
    return json.loads((data / rel).read_text(encoding="utf-8"))


def normalize(q: str) -> str:
    """FAQ variant key: lower-case, alphanumerics only, single spaces (matches the exact-cache normaliser)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", q.lower())).strip()


# table -> (pk columns, {column: source key | (source key, "jsonb")}, guard)
# guard: SQL condition on the existing row that must hold for an update (protects uploaded rows).
SPECS: dict[str, tuple[str, dict[str, Any], str | None]] = {
    "customers": ("id", {"id": "id", "company": "company", "industry": "industry", "city": "city",
                         "service_centre": "service_centre", "email_domain": "email_domain",
                         "contacts": ("contacts", J), "created_at": "created_at"}, None),
    "assets": ("serial_no", {"serial_no": "serial_no", "model": "model", "family": "family",
                             "customer_id": "customer_id", "dispatched_on": "dispatched_on",
                             "commissioned_on": "commissioned_on", "warranty_end": "warranty_end",
                             "site_city": "site_city", "running_hours": "running_hours"}, None),
    "amc_contracts": ("id", {"id": "id", "customer_id": "customer_id", "plan": "plan", "start_date": "start",
                             "end_date": "end", "status": "status", "serials": "serials",
                             "visits_used": "visits_used", "price_inr": "price_inr"}, None),
    "tickets": ("id", {"id": "id", "customer_id": "customer_id", "serial_no": "serial_no", "model": "model",
                       "error_code": "error_code", "priority": "priority", "status": "status",
                       "channel": "channel", "opened_at": "opened_at", "assigned_engineer": "assigned_engineer",
                       "scheduled_visit": "scheduled_visit", "summary": "summary",
                       "history": ("history", J)}, None),
    "parts_orders": ("id", {"id": "id", "customer_id": "customer_id", "lines": ("lines", J), "status": "status",
                            "placed_on": "placed_on", "dispatch_from": "dispatch_from", "awb": "awb"}, None),
    "models": ("id", {"id": "id", "family": "family", "name": "name", "specs": ("specs", J)}, None),
    "error_codes": ("id", {"id": "id", "family": "family", "code": "code", "title": "title",
                           "display_text": "display_text", "severity": "severity", "safety_stop": "safety_stop",
                           "causes": ("causes", J)}, "error_codes.source = 'seed'"),
    "spare_parts": ("part_no", {"part_no": "part_no", "family": "family", "name": "name",
                                "price_inr": "price_inr", "consumable": "consumable",
                                "fits_models": "fits_models", "stock": ("stock", J),
                                "lead_time_days": "lead_time_days_if_unavailable"}, "spare_parts.source = 'seed'"),
    "faq_entries": ("id", {"id": "id", "category": "category", "question": "question", "answer": "answer",
                           "source_doc": "source_doc"}, "faq_entries.origin = 'seed'"),
}

SOURCES = {
    "customers": "ops/customers.json", "assets": "ops/assets.json", "amc_contracts": "ops/amc_contracts.json",
    "tickets": "ops/tickets.json", "parts_orders": "ops/parts_orders.json", "models": "catalog/models.json",
    "error_codes": "catalog/error_codes.json", "spare_parts": "catalog/spare_parts.json",
    "faq_entries": "kb/faqs.json",
}
ORDER = list(SOURCES)  # parents before children (customers before assets/contracts/tickets)


def upsert(conn: Connection, table: str, rows: list[dict[str, Any]]) -> int:
    pk, cols, guard = SPECS[table]
    names = list(cols)
    values = ", ".join(f"CAST(:{c} AS jsonb)" if isinstance(cols[c], tuple) else f":{c}" for c in names)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in names if c != pk)
    sql = (f"INSERT INTO {table} ({', '.join(names)}) VALUES ({values}) "
           f"ON CONFLICT ({pk}) DO UPDATE SET {updates}" + (f" WHERE {guard}" if guard else ""))
    params = []
    for row in rows:
        p = {}
        for col, src in cols.items():
            if isinstance(src, tuple):
                p[col] = json.dumps(row.get(src[0]), ensure_ascii=False)
            else:
                p[col] = row.get(src)
        params.append(p)
    if params:
        conn.execute(text(sql), params)
    return len(params)


def seed_faq_variants(conn: Connection, faqs: list[dict[str, Any]]) -> int:
    """Question + paraphrases per seed FAQ. Embeddings are filled by `seed.py --kb` (BS-07)."""
    wanted: dict[str, tuple[str, str]] = {}
    for f in faqs:
        for t in [f["question"], *f.get("paraphrases", [])]:
            wanted.setdefault(normalize(t), (f["id"], t))
    ids = [f["id"] for f in faqs]
    # drop variants of seed FAQs that the generator no longer produces
    conn.execute(text("DELETE FROM faq_variants v USING faq_entries e WHERE v.faq_id = e.id AND e.origin = 'seed' "
                      "AND e.id = ANY(:ids) AND NOT (v.normalized = ANY(:keep))"),
                 {"ids": ids, "keep": list(wanted)})
    # keep an existing embedding when the text is unchanged
    conn.execute(text("INSERT INTO faq_variants (faq_id, text, normalized) VALUES (:f, :t, :n) "
                      "ON CONFLICT (normalized) DO UPDATE SET faq_id = EXCLUDED.faq_id, text = EXCLUDED.text, "
                      "embedding = CASE WHEN faq_variants.text = EXCLUDED.text THEN faq_variants.embedding END"),
                 [{"f": fid, "t": t, "n": n} for n, (fid, t) in wanted.items()])
    return len(wanted)


def advance_ticket_seq(conn: Connection) -> int:
    """Never move the sequence backwards: next id is above both seeded and runtime-created tickets."""
    return conn.execute(text(r"""
        SELECT setval('ticket_seq', GREATEST(
          (SELECT coalesce(max(substring(id FROM '(\d+)$')::int), 0) FROM tickets WHERE id ~ '^SR-\d{4}-\d+$'),
          (SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END FROM ticket_seq)))
    """)).scalar_one()


def seed(conn: Connection, data: Path = DATA) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ORDER:
        counts[table] = upsert(conn, table, _load(data, SOURCES[table]))
    counts["faq_variants"] = seed_faq_variants(conn, _load(data, "kb/faqs.json"))
    counts["ticket_seq_last"] = advance_ticket_seq(conn)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="database URL (default: app settings / DATABASE_URL)")
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--kb", action="store_true", help="ingest KB documents and FAQ embeddings (BS-07)")
    args = ap.parse_args(argv)
    if args.kb:
        print("seed --kb: KB ingestion arrives with BS-07; nothing to do yet.")
        return 0
    url = args.url
    if not url:
        sys.path.insert(0, str(ROOT / "services" / "agent"))
        from app.config.settings import get_settings

        url = get_settings().resolved_database_url()
    if not url:
        print("No database URL: run `make env` (and `make proxy`) or pass --url", file=sys.stderr)
        return 2
    start = time.perf_counter()
    engine = create_engine(url)
    with engine.begin() as conn:  # one transaction: all or nothing
        counts = seed(conn, args.data)
    engine.dispose()
    print("seeded " + ", ".join(f"{k}={v}" for k, v in counts.items()) + f" in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
