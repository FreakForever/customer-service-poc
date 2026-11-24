"""Data access for the account tools (LLD-10, FR-AG-06). Account reads take the customer id and filter on it in the
query itself, so another customer's row is never fetched into the process."""

import json
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

Row = dict[str, Any]


class OpsRepo(Protocol):
    async def ticket(self, ticket_id: str, customer_id: str) -> Row | None: ...
    async def asset(self, serial_no: str, customer_id: str) -> Row | None: ...
    async def contract(self, customer_id: str, *, contract_id: str | None, serial_no: str | None) -> Row | None: ...
    async def part(self, part_no: str) -> Row | None: ...
    async def safety_stop(self, error_code: str, serial_no: str, customer_id: str | None) -> bool: ...
    async def service_centre(self, city: str) -> Row | None: ...
    async def insert_ticket(self, row: Row) -> str: ...
    async def insert_quote(self, row: Row) -> None: ...
    async def insert_lead(self, row: Row) -> str: ...


class PgRepo:
    def __init__(self, conn: AsyncConnection) -> None:
        self.conn = conn

    async def _one(self, sql: str, **p: Any) -> Row | None:
        r = (await self.conn.execute(text(sql), p)).mappings().first()
        return dict(r) if r else None

    async def ticket(self, ticket_id: str, customer_id: str) -> Row | None:
        return await self._one("SELECT * FROM tickets WHERE id = :i AND customer_id = :c", i=ticket_id, c=customer_id)

    async def asset(self, serial_no: str, customer_id: str) -> Row | None:
        return await self._one("SELECT * FROM assets WHERE serial_no = :s AND customer_id = :c", s=serial_no, c=customer_id)

    async def contract(self, customer_id: str, *, contract_id: str | None, serial_no: str | None) -> Row | None:
        return await self._one(
            "SELECT * FROM amc_contracts WHERE customer_id = :c AND (CAST(:i AS text) IS NULL OR id = :i) "
            "AND (CAST(:s AS text) IS NULL OR :s = ANY(serials)) ORDER BY end_date DESC LIMIT 1",
            c=customer_id, i=contract_id, s=serial_no)

    async def part(self, part_no: str) -> Row | None:
        return await self._one("SELECT * FROM spare_parts WHERE part_no = :p", p=part_no)

    async def safety_stop(self, error_code: str, serial_no: str, customer_id: str | None) -> bool:
        r = await self._one(
            "SELECT bool_or(e.safety_stop) AS stop FROM error_codes e WHERE e.id = :e OR (e.code = :e AND e.family = "
            "(SELECT family FROM assets WHERE serial_no = :s AND customer_id = :c))",
            e=error_code, s=serial_no, c=customer_id)
        return bool(r and r["stop"])

    async def service_centre(self, city: str) -> Row | None:
        from app.config.settings import get_settings
        from app.kg.client import get_read_driver
        from app.kg.templates import run_template

        res = await run_template("T-SC", {"city": city}, driver=get_read_driver(),
                                 database=get_settings().neo4j_database)
        return res.rows[0] if res.rows else None

    async def insert_ticket(self, row: Row) -> str:
        r = await self._one(
            "INSERT INTO tickets (id, customer_id, serial_no, model, error_code, priority, status, channel, opened_at, "
            "summary, history) VALUES ('SR-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('ticket_seq')::text, 5, '0'),"
            " :customer_id, :serial_no, :model, :error_code, :priority, 'new', :channel, now(), :summary, "
            "CAST(:history AS jsonb)) RETURNING id", **{**row, "history": json.dumps(row["history"])})
        assert r is not None
        return str(r["id"])

    async def insert_quote(self, row: Row) -> None:
        await self.conn.execute(text(
            "INSERT INTO parts_orders (id, customer_id, lines, status, placed_on) VALUES (:id, :customer_id, "
            "CAST(:lines AS jsonb), 'quote_requested', current_date)"), {**row, "lines": json.dumps(row["lines"])})

    async def insert_lead(self, row: Row) -> str:
        r = await self._one(
            "INSERT INTO leads (customer_id, product_interest, application, throughput, location, contact) VALUES "
            "(:customer_id, :product_interest, :application, :throughput, :location, CAST(:contact AS jsonb)) "
            "RETURNING id", **{**row, "contact": json.dumps(row["contact"])})
        assert r is not None
        return str(r["id"])
