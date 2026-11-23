"""Neo4j access for the agent (LLD-4). All agent queries run in READ transactions.

READ access mode is always enforced (LLD-2); a separate read-only user (NEO4J_RO_USER) is used when the hosting
supports RBAC. AuraDB Free has no user management (OD-003), so dev falls back to the main user in READ mode.
"""

from functools import lru_cache
from typing import Any

from neo4j import READ_ACCESS, AsyncDriver, AsyncGraphDatabase, unit_of_work

from app.config.settings import Settings, get_settings


def read_credentials(s: Settings) -> tuple[str, str]:
    if s.neo4j_ro_user and s.neo4j_ro_password.get_secret_value():
        return s.neo4j_ro_user, s.neo4j_ro_password.get_secret_value()
    return s.neo4j_user, s.neo4j_password.get_secret_value()


def make_read_driver(s: Settings) -> AsyncDriver:
    if not s.neo4j_uri:
        raise RuntimeError("NEO4J_URI is not configured")
    return AsyncGraphDatabase.driver(s.neo4j_uri, auth=read_credentials(s))


@lru_cache
def get_read_driver() -> AsyncDriver:
    return make_read_driver(get_settings())


async def read_query(driver: AsyncDriver, cypher: str, params: dict[str, Any], *, database: str | None = None,
                     timeout_s: float = 5.0) -> tuple[list[dict[str, Any]], int]:
    """Run `cypher` in a READ transaction. Returns (rows, server_ms): rows as plain dicts, server_ms = time the
    server took to produce and stream the result (excludes network round trips)."""

    @unit_of_work(timeout=timeout_s)
    async def work(tx):
        result = await tx.run(cypher, params)
        rows = [r.data() async for r in result]
        summary = await result.consume()
        server_ms = (summary.result_available_after or 0) + (summary.result_consumed_after or 0)
        return rows, server_ms

    async with driver.session(database=database or None, default_access_mode=READ_ACCESS) as session:
        return await session.execute_read(work)
