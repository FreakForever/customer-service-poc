"""Retention purge (NFR-SEC-04, LLD-13): a daily job the worker schedules for itself, so it needs no external scheduler."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.queue import Job, enqueue

EVERY_H = 24


async def schedule_purge(engine: AsyncEngine) -> bool:
    """Queue one purge unless one was queued in the last day (several workers may call this at once)."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('purge'))"))
        recent = (await conn.execute(text("SELECT 1 FROM jobs WHERE kind = 'purge' AND created_at > now() - make_interval(hours => :h) LIMIT 1"),
                                     {"h": EVERY_H})).first()
        if recent:
            return False
        await enqueue(conn, "purge", {})
        return True


def make_purge_handler(engine: AsyncEngine, retention_days: int):
    async def handle(_: Job) -> dict[str, Any]:
        async with engine.begin() as conn:
            row = (await conn.execute(text("SELECT * FROM purge_expired(:d)"), {"d": retention_days})).mappings().one()
        return dict(row)
    return handle
