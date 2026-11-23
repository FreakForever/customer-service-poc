"""Shared async SQLAlchemy engine for the API process (lazy, one per process)."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine | None:
    url = get_settings().resolved_database_url()
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5) if url else None
