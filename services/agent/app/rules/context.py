"""Dictionaries the rule layer needs, loaded once per process (LLD-6): the KG alias linker and customer company
names (for exfiltration checks). Loaded from Neo4j/Postgres at startup; tests build one from mock-data files."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.kg.linker import Linker
from app.rules.guardrails import InjectionClassifier

log = logging.getLogger("mcs.rules")


@dataclass
class RuleContext:
    linker: Linker
    companies: set[str] = field(default_factory=set)
    customer_company: dict[str, str] = field(default_factory=dict)     # customer_id → company
    injection_classifier: InjectionClassifier | None = None
    version: int | None = None                                          # kg_state.version it was loaded at; None = fixed (tests, files)

    @classmethod
    def from_files(cls, data_dir: Path) -> "RuleContext":
        nodes = [json.loads(x) for x in (data_dir / "kg" / "nodes.jsonl").open(encoding="utf-8")]
        edges = [json.loads(x) for x in (data_dir / "kg" / "edges.jsonl").open(encoding="utf-8")]
        customers = json.loads((data_dir / "ops" / "customers.json").read_text(encoding="utf-8"))
        return cls(Linker(nodes, edges), {c["company"] for c in customers},
                   {c["id"]: c["company"] for c in customers})


_ctx: RuleContext | None = None
CHECK_S = 5.0
_checked = 0.0
_task: "asyncio.Task[None] | None" = None


def set_rule_context(ctx: RuleContext | None) -> None:
    global _ctx
    _ctx = ctx


async def get_rule_context() -> RuleContext:
    """Load once: KG nodes via Neo4j, company names via Postgres. Degrades to empty dictionaries (regex ids and
    lexicons still work) when a store is unreachable."""
    global _ctx, _checked, _task
    if _ctx is not None:
        if _ctx.version is not None and time.monotonic() - _checked > CHECK_S and (_task is None or _task.done()):
            _checked = time.monotonic()
            _task = asyncio.create_task(_refresh(_ctx))            # this turn isn't delayed; the next ones use the new graph
        return _ctx
    _ctx = await _load()
    _checked = time.monotonic()
    return _ctx


async def bump_kg_version(conn: AsyncConnection) -> None:
    """The graph changed: every process reloads its dictionaries within a few seconds (no restart). Same transaction as the change."""
    await conn.execute(text("UPDATE kg_state SET version = version + 1"))


async def refresh_rule_context() -> None:
    """Reload now in this process, unless the context was fixed by a test."""
    if _ctx is not None and _ctx.version is not None:
        await _refresh(_ctx)


async def _version() -> int:
    from app.db import get_engine

    if (engine := get_engine()) is None:
        return 0
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version FROM kg_state"))).scalar() or 0


async def _refresh(old: RuleContext) -> None:
    global _ctx
    try:
        if await _version() == old.version:
            return
        new = await _load()
        if new.linker.nodes and _ctx is old:                       # never swap a good dictionary for an empty one
            _ctx = new
            log.info("rule context reloaded at graph version %s", new.version)
    except Exception as exc:  # noqa: BLE001 - keep the current dictionary, try again later
        log.warning("rule context refresh failed: %s", type(exc).__name__)


async def _load() -> RuleContext:
    version = 0
    try:
        version = await _version()                                 # read first: a change during loading triggers another reload
    except Exception as exc:  # noqa: BLE001
        log.warning("rule context: version unavailable (%s)", type(exc).__name__)

    from app.config.settings import get_settings
    from app.db import get_engine
    from app.kg.client import get_read_driver
    from app.kg.linker import load_graph

    nodes: list[dict] = []
    edges: list[dict] = []
    try:
        nodes, edges = await load_graph(get_read_driver(), get_settings().neo4j_database)
    except Exception as exc:  # noqa: BLE001
        log.warning("rule context: KG unavailable (%s); entity dictionary is empty", type(exc).__name__)
    companies: dict[str, str] = {}
    if (engine := get_engine()) is not None:
        try:
            async with engine.connect() as conn:
                companies = dict((await conn.execute(text("SELECT id, company FROM customers"))).all())
        except Exception as exc:  # noqa: BLE001
            log.warning("rule context: customers unavailable (%s)", type(exc).__name__)
    return RuleContext(Linker(nodes, edges), set(companies.values()), companies, version=version)
