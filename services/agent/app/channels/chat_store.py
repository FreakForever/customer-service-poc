"""Chat persistence in conversations/messages/feedback (LLD-12, FR-CH-02, FR-CH-06)."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

LEASE_S = 60            # one turn at a time per conversation; a crashed turn frees itself after this


async def create(conn: AsyncConnection, customer_id: str | None, retention_days: int) -> uuid.UUID:
    return (await conn.execute(text(
        "INSERT INTO conversations (channel, customer_id, expires_at) VALUES ('chat', :c, now() + make_interval(days => :d)) "
        "RETURNING id"), {"c": customer_id, "d": retention_days})).scalar_one()


async def customer_exists(conn: AsyncConnection, customer_id: str) -> bool:
    return bool((await conn.execute(text("SELECT 1 FROM customers WHERE id = :c"), {"c": customer_id})).scalar())


async def load(conn: AsyncConnection, sid: uuid.UUID) -> dict[str, Any] | None:
    r = (await conn.execute(text("SELECT id, customer_id, status, active_agent, state FROM conversations WHERE id = :i AND "
                                 "channel = 'chat' AND (expires_at IS NULL OR expires_at > now())"), {"i": sid})).mappings().first()
    return dict(r) if r else None


async def begin_turn(conn: AsyncConnection, sid: uuid.UUID) -> bool:
    return (await conn.execute(text(
        "UPDATE conversations SET state = jsonb_set(coalesce(state, '{}'), '{_lease}', to_jsonb(extract(epoch FROM now()) + "
        "CAST(:s AS int))) WHERE id = :i AND coalesce((state->>'_lease')::float, 0) < extract(epoch FROM now())"),
        {"i": sid, "s": LEASE_S})).rowcount == 1


async def end_turn(conn: AsyncConnection, sid: uuid.UUID, *, session: dict[str, Any] | None, status: str | None,
                   priority: str | None, retention_days: int) -> None:
    """Saves the dialog (which drops the lease). `human`/`closed` set by staff meanwhile is never overwritten."""
    await conn.execute(text(
        "UPDATE conversations SET state = CASE WHEN CAST(:st AS text) IS NULL THEN state - '_lease' ELSE CAST(:st AS jsonb) END, "
        "active_agent = CASE WHEN CAST(:st AS text) IS NULL THEN active_agent ELSE :a END, "
        "status = CASE WHEN status IN ('human', 'closed') THEN status ELSE coalesce(:s, status) END, "
        "priority = least(priority, :p), updated_at = now(), expires_at = now() + make_interval(days => :d) WHERE id = :i"),
        {"i": sid, "st": json.dumps({"dialog": session["dialog"]}) if session else None,
         "a": session["active_agent"] if session else None, "s": status, "p": priority, "d": retention_days})


async def add_message(conn: AsyncConnection, sid: uuid.UUID, role: str, content: str, *, citations: Any = None,
                      actions: Any = None, trace_id: uuid.UUID | None = None) -> uuid.UUID:
    return (await conn.execute(text(
        "INSERT INTO messages (conversation_id, role, content, citations, actions, trace_id, created_at) VALUES (:c, :r, :t, "
        "CAST(:ci AS jsonb), CAST(:a AS jsonb), :tr, clock_timestamp()) RETURNING id"),
        {"c": sid, "r": role, "t": content, "ci": json.dumps(citations or []), "a": json.dumps(actions or [], default=str),
         "tr": trace_id})).scalar_one()


async def transcript(conn: AsyncConnection, sid: uuid.UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT id, role, content, citations, created_at FROM messages WHERE conversation_id = :i "
                                   "AND delivery = 'sent' AND role <> 'system' ORDER BY created_at, id"), {"i": sid})
    return [dict(r) for r in rows.mappings()]


async def human_messages_after(conn: AsyncConnection, sid: uuid.UUID, seen: set[uuid.UUID]) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT id, content FROM messages WHERE conversation_id = :i AND role = 'agent' AND "
                                   "delivery = 'sent' ORDER BY created_at, id"), {"i": sid})
    return [dict(r) for r in rows.mappings() if r["id"] not in seen]


async def queue_position(conn: AsyncConnection, sid: uuid.UUID) -> int:
    return int((await conn.execute(text(
        "SELECT count(*) FROM conversations q, conversations me WHERE me.id = :i AND q.status = 'handoff' AND "
        "(coalesce(q.priority, 'P9'), q.updated_at) <= (coalesce(me.priority, 'P9'), me.updated_at)"), {"i": sid})).scalar_one()) or 1


async def add_feedback(conn: AsyncConnection, message_id: uuid.UUID, session_id: uuid.UUID, rating: int, reason: str | None) -> bool:
    """Only for assistant answers in the caller's own session; a second rating replaces the first."""
    m = (await conn.execute(text("SELECT trace_id FROM messages WHERE id = :m AND conversation_id = :c AND role = 'assistant'"),
                            {"m": message_id, "c": session_id})).first()
    if m is None:
        return False
    await conn.execute(text("DELETE FROM feedback WHERE message_id = :m"), {"m": message_id})
    await conn.execute(text("INSERT INTO feedback (message_id, trace_id, rating, reason) VALUES (:m, :t, :r, :x)"),
                       {"m": message_id, "t": m.trace_id, "r": rating, "x": reason})
    return True


async def related_questions(conn: AsyncConnection, citations: list[dict[str, Any]], asked: str) -> list[str]:
    """Up to two FAQ questions answered by the documents just cited, closest to the question first."""
    docs = list(dict.fromkeys(c["url"].removeprefix("/a/").split("#")[0] for c in citations if c.get("url")))[:3]
    if not docs:
        return []
    rows = await conn.execute(text(
        "SELECT question FROM faq_entries WHERE status = 'active' AND source_doc = ANY(:d) AND id NOT IN "
        "(SELECT faq_id FROM faq_variants WHERE lower(text) = lower(:q)) AND lower(question) <> lower(:q) ORDER BY "
        "ts_rank(to_tsvector('english', question), plainto_tsquery('english', :q)) DESC, id LIMIT 2"), {"d": docs, "q": asked})
    return list(rows.scalars())


async def owns_serial(conn: AsyncConnection, customer_id: str, serial: str) -> bool:
    return bool((await conn.execute(text("SELECT 1 FROM assets WHERE serial_no = :s AND customer_id = :c"),
                                    {"s": serial, "c": customer_id})).scalar())
