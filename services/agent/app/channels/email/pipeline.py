"""Email pipeline (LLD-12; FR-EM-01..06): ingest (idempotent, threaded, sender matched) → job → answer → send or review."""

import asyncio
import hashlib
import json
import logging
import re
import uuid
from pathlib import PurePath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.channels.chat_store import add_message
from app.channels.email.adapter import InboundMail, MailAdapter, OutboundMail
from app.channels.email.demo_mailbox import DemoMailboxAdapter
from app.channels.email.parse import strip_body
from app.channels.email.render import render, subject_for, ticket_ref
from app.config.settings import get_settings
from app.ingest.pipeline import store_file
from app.jobs.queue import Job, enqueue
from app.obs.tracing import TraceRecorder, save_trace
from app.orchestrator.graph import run_turn, session_of
from app.orchestrator.state import CustomerRef, Envelope, TurnState

log = logging.getLogger("mcs.email")
MAX_ATTACHMENT, MAX_ATTACHMENTS = 10 * 1024 * 1024, 10
MAX_PER_HOUR = 20                    # per sender; beyond that a person looks (stops mail loops and floods)
FIXED_REPLIES = {"safety_handoff", "refuse_or_redirect"}      # fixed texts with no account data: sent at once
graph: Any = None                    # tests may set a graph


def get_adapter() -> MailAdapter:
    if get_settings().mail_adapter == "gmail":
        from app.channels.email.gmail import GmailAdapter

        return GmailAdapter()
    return DemoMailboxAdapter()


# ---------- sender and thread ----------

async def identify(conn: AsyncConnection, m: InboundMail) -> CustomerRef | None:
    """Exact contact → customer; else e-mail domain → customer (lower trust); else unknown (FR-EM-04). An address that
    several customers share, or that isn't verified, identifies nobody."""
    if not m.authenticated or "@" not in m.from_addr:
        return None
    addr = m.from_addr.lower()
    rows = (await conn.execute(text("SELECT DISTINCT id FROM customers, jsonb_array_elements(contacts) c WHERE lower(c->>'email') = :a"),
                               {"a": addr})).scalars().all()
    if len(rows) == 1:
        return CustomerRef(customer_id=rows[0], contact_email=addr, trust="contact")
    if rows:
        return None
    rows = (await conn.execute(text("SELECT id FROM customers WHERE lower(email_domain) = :d"), {"d": addr.split("@")[1]})).scalars().all()
    return CustomerRef(customer_id=rows[0], contact_email=addr, trust="domain") if len(rows) == 1 else None


async def find_thread(conn: AsyncConnection, m: InboundMail, cust: CustomerRef | None) -> uuid.UUID | None:
    """By In-Reply-To/References, else by ticket number in the subject. Only someone already in the conversation, or the
    verified customer it belongs to, can join it: ids and ticket numbers can be guessed or forwarded."""
    refs = [r for r in [m.in_reply_to, *reversed(m.references)] if r]
    if refs:
        row = (await conn.execute(text(
            "SELECT c.id, c.customer_id, c.state->>'from' AS addr FROM messages msg JOIN conversations c ON c.id = msg.conversation_id "
            "WHERE msg.external_message_id = ANY(:r) AND c.channel = 'email' LIMIT 1"), {"r": refs})).first()
        if row and m.authenticated and (row.addr == m.from_addr or (cust and cust.trust == "contact" and cust.customer_id == row.customer_id)):
            return row.id
    if cust and cust.trust == "contact" and (t := re.search(r"\bSR-\d{4}-\d{5}\b", m.subject.upper())):
        return (await conn.execute(text(
            "SELECT c.id FROM tickets t JOIN conversations c ON c.id = t.conversation_id WHERE t.id = :t AND t.customer_id = :c "
            "AND c.customer_id = :c AND c.channel = 'email'"), {"t": t.group(0), "c": cust.customer_id})).scalar()
    return None


# ---------- ingest ----------

async def _store_attachments(m: InboundMail) -> list[dict[str, Any]]:
    """Metadata + bucket object (FR-EM-06); never parsed. Oversized files are listed without content."""
    out = []
    key = hashlib.sha256(m.message_id.encode()).hexdigest()[:16]
    for n, a in enumerate(m.attachments[:MAX_ATTACHMENTS]):
        name = re.sub(r"[^\w.\- ]", "_", PurePath(a.filename.replace("\\", "/")).name)[:120] or "attachment"
        uri = None
        if len(a.data) <= MAX_ATTACHMENT:
            uri = await asyncio.to_thread(store_file, f"email-{key}", str(n), name, a.data, get_settings().gcs_bucket)
        out.append({"filename": name, "mime": a.mime[:100], "bytes": len(a.data), "uri": uri})
    return out


async def ingest(engine: AsyncEngine, m: InboundMail) -> tuple[uuid.UUID, bool]:
    """Store the message and queue the reply. Returns (conversation, is_new). Duplicate Message-IDs change nothing."""
    if not m.message_id:
        m.message_id = f"<{hashlib.sha256(f'{m.from_addr}{m.subject}{m.text}'.encode()).hexdigest()[:32]}@no-id.mcs>"
    dup_sql = text("SELECT conversation_id FROM messages WHERE external_message_id = :m")
    async with engine.connect() as conn:
        if dup := (await conn.execute(dup_sql, {"m": m.message_id})).scalar():
            return dup, False
    files = await _store_attachments(m)
    days = get_settings().retention_days
    async with engine.begin() as conn:
        # one ingest per Message-ID at a time, also across workers
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:m))"), {"m": m.message_id})
        if dup := (await conn.execute(dup_sql, {"m": m.message_id})).scalar():
            return dup, False
        cust = await identify(conn, m)
        cid = await find_thread(conn, m, cust)
        if cid is None:
            cid = (await conn.execute(text(
                "INSERT INTO conversations (channel, customer_id, external_thread_id, subject, state, expires_at) VALUES ('email', :c, :t, "
                ":s, CAST(:st AS jsonb), now() + make_interval(days => :d)) RETURNING id"),
                {"c": cust.customer_id if cust else None, "t": m.message_id, "s": m.subject[:300], "d": days,
                 "st": json.dumps({"from": m.from_addr, **({"thread": m.thread_id} if m.thread_id else {})})})).scalar_one()
        else:
            await conn.execute(text("UPDATE conversations SET updated_at = now(), expires_at = now() + make_interval(days => :d), "
                                    "status = CASE WHEN status = 'closed' THEN 'bot' ELSE status END WHERE id = :i"), {"i": cid, "d": days})
        mid = await add_message(conn, cid, "customer", m.text)
        await conn.execute(text("UPDATE messages SET external_message_id = :e WHERE id = :i"), {"e": m.message_id, "i": mid})
        for f in files:
            await conn.execute(text("INSERT INTO attachments (message_id, filename, mime, bytes, storage_uri) VALUES (:m, :f, :t, :b, :u)"),
                               {"m": mid, "f": f["filename"], "t": f["mime"], "b": f["bytes"], "u": f["uri"]})
        adapter = get_adapter()
        if isinstance(adapter, DemoMailboxAdapter):
            await adapter.record_inbound(m, conn)
        if not m.auto:
            await enqueue(conn, "email_reply", {
                "conversation_id": str(cid), "message_id": m.message_id, "from": m.from_addr, "from_name": m.from_name,
                "subject": m.subject, "references": [*m.references, m.message_id][-20:], "thread_id": m.thread_id,
                "customer": cust.model_dump() if cust else None})
    return cid, True


# ---------- reply ----------

def may_send(final: TurnState) -> bool:
    return bool(FIXED_REPLIES & set(final["path"])) or bool(final.get("auto_send"))


async def _greeting(conn: AsyncConnection, p: dict[str, Any]) -> str:
    if p["customer"] and p["customer"]["trust"] == "contact":
        name = (await conn.execute(text("SELECT c->>'name' FROM customers, jsonb_array_elements(contacts) c WHERE id = :i AND "
                                        "lower(c->>'email') = :a LIMIT 1"), {"i": p["customer"]["customer_id"], "a": p["from"]})).scalar()
        if name:
            return str(name).split()[0]
    return (p["from_name"] or "").split()[0] if (p["from_name"] or "").split() else ""


async def _review(engine: AsyncEngine, cid: uuid.UUID, note: str, status: str = "review") -> None:
    async with engine.begin() as conn:
        await add_message(conn, cid, "system", note)
        await conn.execute(text("UPDATE conversations SET status = CASE WHEN status IN ('human','handoff') THEN status ELSE :s END, "
                                "updated_at = now() WHERE id = :i"), {"i": cid, "s": status})


def make_reply_handler(engine: AsyncEngine, adapter: MailAdapter | None = None):
    async def handle(job: Job) -> dict[str, Any]:
        p, cid = job.payload, uuid.UUID(job.payload["conversation_id"])
        reply_id = f"<reply-{hashlib.sha256(p['message_id'].encode()).hexdigest()[:24]}@mcs.matsui-demo.example>"
        async with engine.connect() as conn:
            conv = (await conn.execute(text("SELECT status, active_agent, state, subject FROM conversations WHERE id = :i"), {"i": cid})).mappings().first()
            msg = (await conn.execute(text("SELECT content FROM messages WHERE external_message_id = :m"), {"m": p["message_id"]})).scalar()
            done = (await conn.execute(text("SELECT delivery FROM messages WHERE external_message_id = :m"), {"m": reply_id})).scalar()
            recent = (await conn.execute(text(
                "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.channel = 'email' AND m.role = "
                "'customer' AND c.state->>'from' = :f AND m.created_at > now() - interval '1 hour'"), {"f": p["from"]})).scalar_one()
            started = (await conn.execute(text("SELECT result->>'started' FROM jobs WHERE id = :j"), {"j": job.id})).scalar()
        if conv is None or msg is None:
            return {"skipped": "conversation expired"}
        if done is not None:
            return {"skipped": f"already answered ({done})"}
        if conv["status"] == "human":
            return {"skipped": "a person owns this conversation"}
        if recent > MAX_PER_HOUR:
            await _review(engine, cid, "More than 20 e-mails from this sender in an hour: not answered automatically.")
            return {"skipped": "rate limit"}
        if started:
            # a previous attempt may already have created a ticket or lead: don't run the agents twice
            await _review(engine, cid, "Automatic processing was interrupted; please check for a ticket or lead and reply.")
            return {"review": "interrupted"}
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE jobs SET result = '{\"started\": true}' WHERE id = :j"), {"j": job.id})

        cust = CustomerRef(**p["customer"]) if p["customer"] else None
        rec = TraceRecorder(conversation_id=cid, channel="email")
        env = Envelope(channel="email", conversation_id=cid, customer=cust, text=f"{p['subject']}\n{strip_body(msg)}"[:8000],
                       meta={"from": p["from"], "from_name": p["from_name"], "subject": p["subject"], "body": strip_body(msg)[:8000]})
        state = conv["state"] or {}
        final = await run_turn(env, recorder=rec, graph=graph,
                               session={"active_agent": conv["active_agent"], "dialog": state.get("dialog") or {}})
        draft, h = final.get("draft"), final.get("handoff")
        send = may_send(final)
        session = session_of(final)
        status = "bot" if send and h is None else "handoff" if h is not None and h.action == "handoff" else "review"
        actions = draft.actions if draft else []
        out_adapter = adapter or get_adapter()
        async with engine.begin() as conn:
            allowed: set[str] = set()
            if cust:
                allowed = set((await conn.execute(text("SELECT id FROM tickets WHERE customer_id = :c"), {"c": cust.customer_id})).scalars())
            ticket = ticket_ref(actions, p["subject"], allowed)
            body_txt, html = render(name=await _greeting(conn, p), text=final["response"]["text"], citations=draft.citations if draft else [],
                                    titles={e.citation: e.title or "" for e in final.get("evidence") or []}, ticket=ticket)
            mail = OutboundMail(message_id=reply_id, to=p["from"], subject=subject_for(p["subject"], ticket), text=body_txt, html=html,
                                in_reply_to=p["message_id"], references=p["references"], thread_id=p["thread_id"])
            if h is not None and h.summary:
                await add_message(conn, cid, "system", h.summary, trace_id=rec.trace_id)
            rid = await add_message(conn, cid, "assistant", body_txt, citations=final["response"]["citations"], actions=actions, trace_id=rec.trace_id)
            delivery = ("sent" if out_adapter.transactional else "sending") if send else "draft"
            await conn.execute(text("UPDATE messages SET external_message_id = :e, delivery = :d WHERE id = :i"), {"e": reply_id, "d": delivery, "i": rid})
            await conn.execute(text(
                "UPDATE conversations SET status = CASE WHEN status = 'human' THEN status ELSE :s END, active_agent = :a, state = state || "
                "CAST(:st AS jsonb), priority = least(priority, :p), updated_at = now() WHERE id = :i"),
                {"i": cid, "s": status, "a": session["active_agent"], "st": json.dumps({"dialog": session["dialog"]}),
                 "p": h.priority if h else None})
            if send and out_adapter.transactional:
                await out_adapter.send(mail, conn)
        if send and not out_adapter.transactional:
            # outside the transaction: a crash in between leaves 'sending', which is never re-sent automatically
            async with engine.begin() as conn:
                try:
                    await out_adapter.send(mail, conn)
                    ok = True
                except Exception:
                    log.exception("send failed")
                    ok = False
                await conn.execute(text("UPDATE messages SET delivery = :d WHERE id = :i"), {"d": "sent" if ok else "failed", "i": rid})
            if not ok:
                await _review(engine, cid, "The reply could not be sent.")
        rec.root.setdefault("detail", {})["sent"] = send            # analytics: did the AI answer go out by itself?
        try:
            async with engine.begin() as conn:
                await save_trace(conn, rec)
        except Exception:
            log.exception("trace not saved")
        return {"delivery": delivery, "status": status, "tier": final.get("answered_tier"), "confidence": final.get("confidence"),
                "reason": h.reason if h else None, "latency_ms": rec.latency_ms}
    return handle


# ---------- Gmail jobs ----------

def make_gmail_sync_handler(engine: AsyncEngine, adapter: Any = None):
    async def handle(job: Job) -> dict[str, Any]:
        import httpx

        from app.channels.email.gmail import GmailAdapter

        gm = adapter or GmailAdapter()
        async with engine.connect() as conn:
            since = (await conn.execute(text("SELECT value FROM mail_state WHERE key = 'gmail_history'"))).scalar() or str(job.payload["history_id"])
        try:
            ids, newest = await gm.new_messages(since)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            ids, newest = await gm.recent_ids()
        n = 0
        for i in ids:
            m = await gm.fetch(i)
            if m.from_addr and m.from_addr != get_settings().support_address.lower():       # never answer ourselves
                n += (await ingest(engine, m))[1]
        async with engine.begin() as conn:            # only after every message is stored
            await conn.execute(text(
                "INSERT INTO mail_state (key, value) VALUES ('gmail_history', :v) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = now() WHERE mail_state.value::numeric < EXCLUDED.value::numeric"), {"v": newest})
        return {"fetched": len(ids), "new": n}
    return handle


def make_gmail_watch_handler(engine: AsyncEngine, adapter: Any = None):
    async def handle(job: Job) -> dict[str, Any]:
        from app.channels.email.gmail import GmailAdapter

        async with engine.begin() as conn:
            return {"history_id": await (adapter or GmailAdapter()).watch(conn)}
    return handle
