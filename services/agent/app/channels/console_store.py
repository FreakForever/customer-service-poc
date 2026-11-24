"""Agent console queries (BS-25; FR-HO-01..03, FR-UI-04)."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

Row = dict[str, Any]
VIEWS = {"review": "c.status = 'review'", "live": "c.channel = 'chat' AND c.status IN ('handoff', 'human')",
         "p1": "c.priority = 'P1' AND c.status <> 'closed'", "mine": "c.assigned_to = :me AND c.status <> 'closed'", "all": "true"}
TICKET_STATUSES = ("new", "assigned", "scheduled", "in_progress", "awaiting_parts", "resolved", "closed")


async def rows(conn: AsyncConnection, sql: str, **p: Any) -> list[Row]:
    return [dict(r) for r in (await conn.execute(text(sql), p)).mappings()]


async def counts(conn: AsyncConnection, me: str) -> Row:
    cols = ", ".join(f"count(*) FILTER (WHERE {w}) AS {v}" for v, w in VIEWS.items())
    out = (await rows(conn, f"SELECT {cols} FROM conversations c", me=me))[0]
    out["tickets"] = (await conn.execute(text("SELECT count(*) FROM tickets WHERE status NOT IN ('resolved', 'closed')"))).scalar_one()
    return out


def like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def conversations(conn: AsyncConnection, view: str, me: str, q: str, limit: int, offset: int) -> list[Row]:
    return await rows(conn, f"""
        SELECT c.id, c.channel, c.subject, c.status, c.priority, c.assigned_to, c.updated_at, c.customer_id, cu.company,
          c.state->>'from' AS from_addr, left(m.content, 140) AS last_message, m.role AS last_role
        FROM conversations c LEFT JOIN customers cu ON cu.id = c.customer_id
        LEFT JOIN LATERAL (SELECT content, role FROM messages m WHERE m.conversation_id = c.id AND m.role <> 'system'
          ORDER BY created_at DESC, id LIMIT 1) m ON true
        WHERE {VIEWS[view]} AND (:q = '' OR c.subject ILIKE :l OR cu.company ILIKE :l OR c.state->>'from' ILIKE :l
          OR EXISTS (SELECT 1 FROM messages x WHERE x.conversation_id = c.id AND x.content ILIKE :l)
          OR EXISTS (SELECT 1 FROM tickets t WHERE t.conversation_id = c.id AND t.id ILIKE :l))
        ORDER BY c.priority NULLS LAST, c.updated_at DESC, c.id LIMIT :n OFFSET :o""", me=me, q=q, l=like(q), n=limit, o=offset)


async def detail(conn: AsyncConnection, cid: uuid.UUID) -> Row | None:
    got = await rows(conn, "SELECT id, channel, customer_id, subject, status, priority, assigned_to, active_agent, created_at, updated_at, "
                           "state->>'from' AS from_addr, state->>'agent_name' AS agent_name FROM conversations WHERE id = :i", i=cid)
    if not got:
        return None
    conv = got[0]
    msgs = await rows(conn, """
        SELECT m.id, m.role, m.content, m.citations, m.actions, m.trace_id, m.delivery, m.created_at,
          (SELECT rating FROM feedback f WHERE f.message_id = m.id LIMIT 1) AS rating,
          coalesce((SELECT jsonb_agg(jsonb_build_object('filename', a.filename, 'mime', a.mime, 'bytes', a.bytes)) FROM attachments a
                    WHERE a.message_id = m.id), '[]') AS attachments
        FROM messages m WHERE m.conversation_id = :i ORDER BY m.created_at, m.id""", i=cid)
    c = conv["customer_id"]
    cust = await rows(conn, "SELECT id, company, industry, city, service_centre, contacts FROM customers WHERE id = :c", c=c)
    assets = await rows(conn, """
        SELECT a.serial_no, a.model, a.site_city, a.warranty_end, a.warranty_end >= current_date AS in_warranty,
          EXISTS (SELECT 1 FROM amc_contracts k WHERE a.serial_no = ANY(k.serials) AND k.customer_id = a.customer_id
            AND current_date BETWEEN k.start_date AND k.end_date) AS in_amc
        FROM assets a WHERE a.customer_id = :c ORDER BY a.serial_no""", c=c)
    amc = await rows(conn, "SELECT id, plan, start_date, end_date, status, serials, visits_used, current_date BETWEEN start_date AND end_date "
                           "AS active FROM amc_contracts WHERE customer_id = :c ORDER BY end_date DESC", c=c)
    tickets = await rows(conn, "SELECT id, status, priority, model, error_code, summary, assigned_engineer, scheduled_visit, conversation_id = :i "
                               "AS here FROM tickets WHERE (customer_id = :c AND status NOT IN ('resolved', 'closed')) OR conversation_id = :i "
                               "ORDER BY here DESC NULLS LAST, priority, id DESC", c=c, i=cid)
    notes = [m["content"] for m in msgs if m["role"] == "system"]
    return {"conversation": conv, "messages": msgs, "customer": cust[0] if cust else None, "assets": assets, "contracts": amc, "tickets": tickets,
            "summary": notes[-1] if notes else None}


async def takeover(conn: AsyncConnection, cid: uuid.UUID, email: str, name: str) -> bool:
    """One owner at a time: False when someone else already has it, or it is closed."""
    return (await conn.execute(text(
        "UPDATE conversations SET status = 'human', assigned_to = :e, state = coalesce(state, '{}') || jsonb_build_object('agent_name', "
        "CAST(:n AS text)), updated_at = now() WHERE id = :i AND status <> 'closed' AND (status <> 'human' OR assigned_to = :e)"),
        {"i": cid, "e": email, "n": name})).rowcount == 1


async def give_back(conn: AsyncConnection, cid: uuid.UUID, email: str, *, status: str, force: bool) -> bool:
    return (await conn.execute(text(
        "UPDATE conversations SET status = :s, assigned_to = NULL, state = coalesce(state, '{}') - 'agent_name', updated_at = now() "
        "WHERE id = :i AND status <> 'closed' AND (status <> 'human' OR assigned_to = :e OR :f)"),
        {"i": cid, "s": status, "e": email, "f": force})).rowcount == 1


async def trace(conn: AsyncConnection, tid: uuid.UUID) -> Row | None:
    got = await rows(conn, "SELECT id, conversation_id, channel, answered_tier, intents, confidence, groundedness, handoff, latency_ms, "
                           "first_token_ms, tokens_in, tokens_out, cost_usd, created_at, detail FROM traces WHERE id = :t", t=tid)
    if not got:
        return None
    t = got[0]
    d = t.pop("detail") or {}
    spans = await rows(conn, "SELECT node, started_at, duration_ms, model, tokens_in, tokens_out, cache_hit, attrs FROM spans WHERE trace_id = :t "
                             "ORDER BY started_at, id", t=tid)
    return {"trace": {**t, "path": d.get("path"), "flags": d.get("flags"), "confidence_parts": d.get("confidence"), "handoff_reason":
                      (d.get("handoff") or {}).get("reason"), "suggested": d.get("suggested")},
            "spans": spans, "evidence": d.get("evidence") or [], "verification": d.get("verification")}


async def tickets(conn: AsyncConnection, *, status: str, priority: str, mine: str, q: str, limit: int, offset: int) -> list[Row]:
    return await rows(conn, """
        SELECT t.id, t.customer_id, cu.company, t.serial_no, t.model, t.error_code, t.priority, t.status, t.channel, t.opened_at,
          t.assigned_engineer, t.scheduled_visit, t.summary, t.conversation_id
        FROM tickets t LEFT JOIN customers cu ON cu.id = t.customer_id
        WHERE (:s = '' OR t.status = :s OR (:s = 'open' AND t.status NOT IN ('resolved', 'closed'))) AND (:p = '' OR t.priority = :p)
          AND (:m = '' OR t.assigned_engineer = :m)
          AND (:q = '' OR t.id ILIKE :l OR cu.company ILIKE :l OR t.summary ILIKE :l OR t.serial_no ILIKE :l)
        ORDER BY (t.status IN ('resolved', 'closed')), t.priority, t.opened_at DESC NULLS LAST, t.id LIMIT :n OFFSET :o""",
        s=status, p=priority, m=mine, q=q, l=like(q), n=limit, o=offset)


async def update_ticket(conn: AsyncConnection, tid: str, changes: Row, by: str) -> Row | None:
    """Only the given fields; each change is appended to the ticket's history."""
    cols = [k for k in ("status", "assigned_engineer", "scheduled_visit") if k in changes]
    sets = "".join(f"{k} = :{k}, " for k in cols)
    event: Row = {"event": "updated", "by": by, **{k: str(changes[k]) if changes[k] is not None else None for k in cols}}
    got = await rows(conn, f"UPDATE tickets SET {sets}history = coalesce(history, '[]') || (CAST(:ev AS jsonb) || jsonb_build_object('at', "
                           "current_date)) WHERE id = :id RETURNING id, status, assigned_engineer, scheduled_visit, history",
                     id=tid, ev=json.dumps([event])[1:-1], **{k: changes[k] for k in cols})
    return got[0] if got else None
