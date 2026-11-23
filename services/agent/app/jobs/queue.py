"""Postgres job queue (LLD-13, FR-KB-04): FOR UPDATE SKIP LOCKED claims, max 3 attempts, exponential backoff."""

import json
import logging
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

log = logging.getLogger("mcs.jobs")
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 5  # retry after 5 s, then 10 s


@dataclass
class Job:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int


Handler = Callable[[Job], Awaitable[dict[str, Any] | None]]


async def enqueue(conn: AsyncConnection, kind: str, payload: dict[str, Any]) -> uuid.UUID:
    return (await conn.execute(text("INSERT INTO jobs (kind, payload) VALUES (:k, CAST(:p AS jsonb)) RETURNING id"),
                               {"k": kind, "p": json.dumps(payload)})).scalar_one()


async def claim(conn: AsyncConnection, kinds: list[str] | None = None) -> Job | None:
    row = (await conn.execute(text("""
        UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1, error = NULL
        WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now()
                      AND (CAST(:kinds AS text[]) IS NULL OR kind = ANY(CAST(:kinds AS text[])))
                    ORDER BY run_after, created_at FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, kind, payload, attempts"""), {"kinds": kinds})).first()
    return Job(row.id, row.kind, row.payload or {}, row.attempts) if row else None


async def complete(conn: AsyncConnection, job_id: uuid.UUID, result: dict[str, Any] | None) -> None:
    await conn.execute(text("UPDATE jobs SET status = 'done', finished_at = now(), result = CAST(:r AS jsonb) "
                            "WHERE id = :id"), {"id": job_id, "r": json.dumps(result or {})})


async def fail(conn: AsyncConnection, job: Job, error: str) -> str:
    """Requeue with backoff, or mark failed after MAX_ATTEMPTS. Returns the new status."""
    if job.attempts >= MAX_ATTEMPTS:
        await conn.execute(text("UPDATE jobs SET status = 'failed', finished_at = now(), error = :e WHERE id = :id"),
                           {"id": job.id, "e": error[:4000]})
        return "failed"
    delay = BACKOFF_BASE_S * 2 ** (job.attempts - 1)
    await conn.execute(text("UPDATE jobs SET status = 'queued', error = :e, "
                            "run_after = now() + make_interval(secs => :d) WHERE id = :id"),
                       {"id": job.id, "e": error[:4000], "d": delay})
    return "queued"


async def run_once(engine: AsyncEngine, handlers: dict[str, Handler]) -> Job | None:
    """Claim one job (own transaction), run it, record the outcome. Returns the job, or None if idle."""
    async with engine.begin() as conn:
        job = await claim(conn, list(handlers))
    if job is None:
        return None
    try:
        result = await handlers[job.kind](job)
    except Exception as exc:  # noqa: BLE001 - any handler failure is recorded on the job
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"
        async with engine.begin() as conn:
            status = await fail(conn, job, err)
        log.warning("job %s %s attempt %d failed (%s): %s", job.kind, job.id, job.attempts, status, exc)
        return job
    async with engine.begin() as conn:
        await complete(conn, job.id, result)
    log.info("job %s %s done %s", job.kind, job.id, result)
    return job


async def drain(engine: AsyncEngine, handlers: dict[str, Handler], max_jobs: int = 1000) -> int:
    """Run ready jobs until none are left (seed, tests). Jobs waiting on backoff are not awaited."""
    n = 0
    while n < max_jobs and await run_once(engine, handlers) is not None:
        n += 1
    return n
