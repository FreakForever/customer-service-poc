"""Dictionaries the rule layer needs, loaded once per process (LLD-6): the KG alias linker and customer company
names (for exfiltration checks). Loaded from Neo4j/Postgres at startup; tests build one from mock-data files."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.kg.linker import Linker
from app.rules.guardrails import InjectionClassifier

log = logging.getLogger("mcs.rules")


@dataclass
class RuleContext:
    linker: Linker
    companies: set[str] = field(default_factory=set)
    customer_company: dict[str, str] = field(default_factory=dict)     # customer_id → company
    injection_classifier: InjectionClassifier | None = None

    @classmethod
    def from_files(cls, data_dir: Path) -> "RuleContext":
        nodes = [json.loads(x) for x in (data_dir / "kg" / "nodes.jsonl").open(encoding="utf-8")]
        edges = [json.loads(x) for x in (data_dir / "kg" / "edges.jsonl").open(encoding="utf-8")]
        customers = json.loads((data_dir / "ops" / "customers.json").read_text(encoding="utf-8"))
        return cls(Linker(nodes, edges), {c["company"] for c in customers},
                   {c["id"]: c["company"] for c in customers})


_ctx: RuleContext | None = None


def set_rule_context(ctx: RuleContext | None) -> None:
    global _ctx
    _ctx = ctx


async def get_rule_context() -> RuleContext:
    """Load once: KG nodes via Neo4j, company names via Postgres. Degrades to empty dictionaries (regex ids and
    lexicons still work) when a store is unreachable."""
    global _ctx
    if _ctx is not None:
        return _ctx
    from sqlalchemy import text

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
    _ctx = RuleContext(Linker(nodes, edges), set(companies.values()), companies)
    return _ctx
