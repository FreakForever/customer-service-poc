"""Dependency health probes used by /health, /ready (and their *z aliases) and the worker."""

import asyncio

from neo4j import AsyncGraphDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings

PROBE_TIMEOUT_S = 5.0


def _err(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return f"error: {msg[:160]}"


async def check_db(settings: Settings) -> str:
    url = settings.resolved_database_url()
    if not url:
        return "not_configured"
    engine = create_async_engine(url, pool_pre_ping=False)
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return "ok"
    except BaseException as exc:  # noqa: BLE001 - report any failure as status text
        return _err(exc)
    finally:
        await engine.dispose()


async def check_neo4j(settings: Settings) -> str:
    if not (settings.neo4j_uri and settings.neo4j_password):
        return "not_configured"
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S * 2):
            await driver.verify_connectivity()
        return "ok"
    except BaseException as exc:  # noqa: BLE001
        return _err(exc)
    finally:
        await driver.close()


async def check_all(settings: Settings) -> dict[str, str]:
    db, neo = await asyncio.gather(check_db(settings), check_neo4j(settings))
    status = "ok" if db == "ok" and neo == "ok" else "degraded"
    return {"status": status, "env": settings.env, "db": db, "neo4j": neo}
