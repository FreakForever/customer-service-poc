"""Chat turn → SSE events (LLD-12, FR-CH-01). The turn runs as its own task, so it is finished and stored even if the
customer disconnects. Text is sent only after verification; a status event covers the wait."""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.channels import chat_store as store
from app.config.settings import get_settings
from app.obs.tracing import TraceRecorder, save_trace
from app.orchestrator.graph import run_turn, session_of
from app.orchestrator.nodes.degrade import HANDOFF_TEXT
from app.orchestrator.policies import get_policy
from app.orchestrator.state import CustomerRef, Envelope, TurnState

log = logging.getLogger("mcs.chat")
Event = tuple[str, dict[str, Any] | list[dict[str, Any]]]
STATUS_TEXT = "Checking that for you…"
QUEUED = "Connecting you to our service team…"
WITH_HUMAN = "Our service team has your message."
QUICK_REPLY = {"offer_ticket": ("Raise a service request", "yes please"), "offer_quote": ("Request a quote", "please send a quote")}
TICKET = {"type": "quick_reply", "label": "Raise a service request", "payload": "I want to raise a service request"}
STATUS = {"type": "quick_reply", "label": "Check a ticket", "payload": "Check my ticket status"}
HUMAN = {"type": "quick_reply", "label": "Talk to a person", "payload": "talk to a human"}
_tasks: set[asyncio.Task[Any]] = set()
graph: Any = None                       # tests may set a graph; None = the default graph


def encode(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def citations_of(final: TurnState) -> list[dict[str, Any]]:
    d = final.get("draft")
    titles = {e.citation: e.title for e in final.get("evidence") or []}
    texts = {e.citation: e.text for e in final.get("evidence") or []}
    return [{"marker": c.marker.strip("[]"), "title": titles.get(c.evidence_id) or ("Matsui knowledge base" if c.doc_id else "Matsui product data"),
             "url": f"/a/{c.doc_id}" + (f"#{c.anchor}" if c.anchor else "") if c.doc_id else None,
             "fact": None if c.doc_id else (texts.get(c.evidence_id) or "")[:300] or None}
            for c in (d.citations if d else [])]


def quick_replies(out: dict[str, Any], related: list[str]) -> list[dict[str, Any]]:
    """FR-CH-04: what the answer offers first, then related questions, then the general routes."""
    if out["handoff"]:
        return []
    quick = [{"type": "quick_reply", "label": QUICK_REPLY[a["type"]][0], "payload": QUICK_REPLY[a["type"]][1]}
             for a in out["actions"] if a.get("type") in QUICK_REPLY]
    if out["form"]:
        return quick                                           # a request is being filled in: no distractions
    quick += [{"type": "quick_reply", "label": q, "payload": q} for q in related]
    if not out["citations"]:
        quick += [q for q in (TICKET, STATUS) if q["label"] not in {x["label"] for x in quick}]
    return [*quick, HUMAN]


async def _turn(engine: AsyncEngine, sid: uuid.UUID, conv: dict[str, Any], message: str) -> dict[str, Any]:
    s = get_settings()
    rec = TraceRecorder(conversation_id=sid, channel="chat")
    env = Envelope(channel="chat", conversation_id=sid, text=message,
                   customer=CustomerRef(customer_id=conv["customer_id"], trust="session") if conv["customer_id"] else None)
    session = {"active_agent": conv["active_agent"], "dialog": (conv["state"] or {}).get("dialog") or {}}
    final: TurnState | None = None
    try:
        final = await run_turn(env, recorder=rec, session=session, graph=graph)
    except Exception:
        log.exception("chat turn failed")
    r: dict[str, Any] = final["response"] if final else {"text": HANDOFF_TEXT, "handoff": {"action": "handoff", "priority": None}, "answered_tier": "T4"}
    d = final.get("draft") if final else None
    cites = citations_of(final) if final else []
    actions = [a for a in (d.actions if d else [])]
    handoff = r["handoff"] if r["handoff"] and r["handoff"]["action"] == "handoff" else None
    out: dict[str, Any] = {"text": r["text"], "citations": cites, "actions": actions, "form": d.form if d else None,
                           "handoff": handoff, "tier": r["answered_tier"], "trace_id": rec.trace_id}
    async with engine.begin() as conn:
        h = final.get("handoff") if final else None
        if h is not None and h.summary:
            await store.add_message(conn, sid, "system", h.summary, trace_id=rec.trace_id)      # for the agent console
        out["message_id"] = await store.add_message(conn, sid, "assistant", r["text"], citations=cites, actions=actions, trace_id=rec.trace_id)
        await store.end_turn(conn, sid, session=session_of(final) if final else None, status="handoff" if handoff else None,
                             priority=handoff["priority"] if handoff else None, retention_days=s.retention_days)
        out["related"] = [] if handoff or out["form"] else await store.related_questions(conn, cites, message)
        if handoff:
            out["position"] = await store.queue_position(conn, sid)
    try:
        async with engine.begin() as conn:
            await save_trace(conn, rec)
    except Exception:
        log.exception("trace not saved")
    out["latency_ms"] = rec.latency_ms
    return out


async def stream(engine: AsyncEngine, sid: uuid.UUID, conv: dict[str, Any], message: str) -> AsyncIterator[Event]:
    """Caller has taken the turn lease and stored the customer's message."""
    t0 = time.perf_counter()
    if conv["status"] == "human":
        async with engine.begin() as conn:
            await store.end_turn(conn, sid, session=None, status=None, priority=None, retention_days=get_settings().retention_days)
        yield "handoff", {"status": "with_agent", "position": 0, "message": WITH_HUMAN}
        yield "done", {"trace_id": None, "tier": "T4", "latency_ms": int((time.perf_counter() - t0) * 1000), "message_id": None}
        return
    task = asyncio.create_task(_turn(engine, sid, conv, message))
    _tasks.add(task)                        # keep a reference: the task must outlive a dropped connection
    task.add_done_callback(_tasks.discard)
    wait_s = get_policy("chat").budget_ms.get("status_event", 500) / 1000 * 0.8
    done, _ = await asyncio.wait({task}, timeout=wait_s)
    if not done:
        yield "status", {"text": STATUS_TEXT, "tier": None}
    try:
        out = await asyncio.shield(task)
    except Exception:
        log.exception("chat turn could not be stored")
        yield "token", {"t": HANDOFF_TEXT}
        yield "done", {"trace_id": None, "tier": "T4", "latency_ms": int((time.perf_counter() - t0) * 1000), "message_id": None,
                       "error": "not_saved"}
        return
    for piece in re.findall(r"\S+\s*|\s+", out["text"]):
        yield "token", {"t": piece}
    if out["citations"]:
        yield "citations", out["citations"]
    if quick := quick_replies(out, out["related"]):
        yield "actions", quick
    if out["form"]:
        yield "form", out["form"]
    if out["handoff"]:
        yield "handoff", {"status": "queued", "position": out["position"], "message": QUEUED}
    yield "done", {"trace_id": out["trace_id"], "tier": out["tier"], "latency_ms": out["latency_ms"], "message_id": out["message_id"]}
