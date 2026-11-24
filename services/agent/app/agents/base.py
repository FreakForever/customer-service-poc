"""Shared types for the specialist agents (LLD-10). `dialog` is the JSON-serialisable memory kept with the
conversation; agents read and update it in place."""

from dataclasses import dataclass, field
from typing import Any

from app.kg.linker import Entities, Linker
from app.orchestrator.state import Citation, Envelope, HandoffDecision
from app.providers import ResilientLLM
from app.retrieval.evidence import Evidence
from app.retrieval.graphrag import GraphRAGDeps
from app.tools.account import ToolContext


@dataclass
class AgentCtx:
    env: Envelope
    text: str
    ents: Entities
    dialog: dict[str, Any]
    deps: GraphRAGDeps
    tools: ToolContext
    llm: ResilientLLM
    model_fast: str
    model_strong: str
    timeout_ms: int
    sticky: bool = False

    @property
    def linker(self) -> Linker:
        assert self.deps.linker is not None
        return self.deps.linker


@dataclass
class AgentResult:
    text: str
    tier: str = "T3"
    active_agent: str | None = None          # None = done, hand back to the router
    evidence: list[Evidence] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    form: dict[str, Any] | None = None
    handoff: HandoffDecision | None = None
    handback: bool = False                   # this turn isn't for the agent: re-route it
    abstained: bool = False


def cite(evidence: list[Evidence], start: int = 1) -> list[Citation]:
    return [Citation(marker=f"[E{i}]", evidence_id=e.citation, doc_id=e.doc_id, anchor=e.anchor)
            for i, e in enumerate(evidence, start)]


def remember(ctx: AgentCtx) -> None:
    """Merge this turn's entities into the dialog (newest wins)."""
    d, e = ctx.dialog, ctx.ents
    if e.model and e.model != d.get("model"):
        if d.get("model"):
            d.pop("errors", None)            # another machine: its codes don't carry over
        d["model"] = e.model
    if e.family:
        d["family"] = e.family
    if e.error_codes:
        d["errors"] = e.error_codes
    if e.ids("SparePart"):
        d["parts"] = e.ids("SparePart")
    if e.serials:
        d["serial"] = e.serials[0]
    for label, key in (("Warehouse", "city"), ("City", "name")):
        if ids := e.ids(label):
            d["city"] = ctx.linker.nodes[ids[0]]["props"].get(key) or d.get("city")
    if e.ambiguous and not d.get("errors"):
        d["pending"] = e.ambiguous[0].mention
    elif d.get("pending") and d.get("model") and not d.get("errors"):
        # the model arrived a turn after an ambiguous code: "E06" ... "it's a DHD-100"
        d["errors"] = ctx.linker.link_dictionary(f"{d['model']} error {d['pending']}").error_codes
        d.pop("pending")
