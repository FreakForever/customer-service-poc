"""Shared async SQLAlchemy engine for the API process (lazy, one per process)."""

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine | None:
    url = get_settings().resolved_database_url()
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5) if url else None


async def warm_pool() -> None:
    """Open a connection before the first turn: a cold connect can exceed a node budget and degrade that turn."""
    if (engine := get_engine()) is not None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
