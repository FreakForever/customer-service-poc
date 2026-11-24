"""tools node (BS-16/17, LLD-10): T0 account and part look-ups; ticket and lead flows go to their agents."""

import time
from typing import Any

from app.db import get_engine
from app.obs.tracing import span_set
from app.orchestrator.state import Draft, HandoffDecision, TurnState
from app.retrieval.evidence import Evidence
from app.tools.account import ToolContext, call_tool
from app.tools.render import render
from app.tools.repo import PgRepo

Update = dict[str, Any]
ASK = {"service.ticket_status": "Please share your ticket number (for example SR-2026-00123).",
       "amc.status": "Please share your AMC number (AMC-YYYY-NNNN) or the machine's serial number.",
       "warranty.status": "Please share the machine's serial number.",
       "asset.info": "Please share the machine's serial number."}

UNAVAILABLE = "I can't reach your account records right now, so I'm passing this to our service team."


def plan(intent: str, state: TurnState) -> list[tuple[str, dict[str, Any]]] | None:
    """Tool calls for an intent; [] = an id is missing; None = not handled here."""
    e = state["entities"]
    if intent == "service.ticket_status":
        return [("get_ticket_status", {"ticket_id": t}) for t in e.ticket_ids[:3]]
    if intent == "amc.status":
        return [("get_amc_contract", {"contract_id": a}) for a in e.amc_ids[:3]] or \
            [("get_amc_contract", {"serial_no": s}) for s in e.serials[:3]]
    if intent in ("warranty.status", "asset.info"):
        name = "get_warranty" if intent == "warranty.status" else "get_asset"
        return [(name, {"serial_no": s}) for s in e.serials[:3]]
    if intent.startswith("parts."):
        return [("get_part_availability", {"part_no": p}) for p in e.ids("SparePart")[:3]]
    return None


def _unavailable(reason: str, state: TurnState) -> Update:
    span_set(status="error", error=reason)
    return {"answered_tier": "T4", "draft": Draft(text=UNAVAILABLE),
            "handoff": HandoffDecision(action=state["policy"].on_low_confidence, reason=f"account lookup failed: {reason}")}


async def tools(state: TurnState) -> Update:
    top = max(state["intents"], key=lambda i: i.confidence).name
    calls = plan(top, state)
    if calls is None:                                  # dispatch only sends look-up intents here
        return _unavailable(f"no tool for {top}", state)
    if not calls:
        return {"answered_tier": "T0", "draft": Draft(text=ASK.get(top, "Could you share a few more details?"))}
    try:
        text, ev = await lookups(state, calls)
    except Exception as exc:  # noqa: BLE001 - store down: say so and hand off, never fail the turn
        return _unavailable(type(exc).__name__, state)
    return {"answered_tier": "T0", "draft": Draft(text=text), "evidence": ev}


async def lookups(state: TurnState, calls: list[tuple[str, dict[str, Any]]]) -> tuple[str, list[Evidence]]:
    engine = get_engine()
    if engine is None:
        raise ConnectionError("no database")
    env = state["env"]
    t0 = time.perf_counter()
    async with engine.connect() as conn:
        ctx = ToolContext(PgRepo(conn), customer_id=env.customer.customer_id if env.customer else None,
                          channel=env.channel)
        results = [await call_tool(n, a, ctx) for n, a in calls]
    span_set(tools=[(r.tool, r.status) for r in results], tool_ms=round((time.perf_counter() - t0) * 1000, 1))
    ev = [Evidence(id=f"tool:{r.tool}:{next(iter(a.values()))}", kind="tool", source=r.tool, text=render(r), score=1.0,
                   metadata={"status": r.status})
          for r, (_, a) in zip(results, calls, strict=True) if r.status == "ok"]
    return "\n".join(dict.fromkeys(render(r) for r in results)), ev


NODES = {"tools": tools}
