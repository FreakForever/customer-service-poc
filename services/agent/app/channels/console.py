"""Replies by people: live chat messages, reviewed e-mail drafts, promotion to the FAQ cache (BS-25)."""

import hashlib
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api.staff import Staff
from app.cache.normalize import normalize
from app.cache.store import CacheWriteRejected, _vec, write_answer
from app.channels.chat_store import add_message
from app.channels.console_store import rows
from app.channels.email.adapter import MailAdapter, OutboundMail
from app.channels.email.render import render_manual, subject_for
from app.config.settings import get_settings
from app.providers.base import Embedder
from app.rules.pii import mask_pii

log = logging.getLogger("mcs.console")
SIGNATURE = "Matsui Customer Care\nDemo system: all data in this message is synthetic."


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


async def send_chat(conn: AsyncConnection, cid: uuid.UUID, staff: Staff, body: str) -> uuid.UUID:
    owner = (await rows(conn, "SELECT status, assigned_to FROM conversations WHERE id = :i FOR UPDATE", i=cid))[0]
    if owner["status"] != "human" or owner["assigned_to"] != staff.email:
        raise Refused(409, "take over the conversation before replying")
    mid = await add_message(conn, cid, "agent", body, actions=[{"type": "sent_by", "name": staff.name, "email": staff.email}])
    await conn.execute(text("UPDATE conversations SET updated_at = now(), expires_at = now() + make_interval(days => :d) WHERE id = :i"),
                       {"i": cid, "d": get_settings().retention_days})
    return mid


async def send_email(engine: AsyncEngine, adapter: MailAdapter, cid: uuid.UUID, staff: Staff, body: str, draft_id: uuid.UUID | None) -> uuid.UUID:
    """Approve a draft (as is or edited) or write a new reply. A draft can be sent once."""
    async with engine.begin() as conn:
        conv = (await rows(conn, "SELECT status, subject, state->>'from' AS addr, state->>'thread' AS thread, assigned_to FROM conversations "
                                 "WHERE id = :i FOR UPDATE", i=cid))[0]
        if conv["status"] == "closed":
            raise Refused(409, "this conversation is closed")
        if conv["status"] == "human" and conv["assigned_to"] != staff.email:
            raise Refused(409, "another agent owns this conversation")
        if not conv["addr"]:
            raise Refused(409, "the sender's address is unknown")
        ids = [r["m"] for r in await rows(conn, "SELECT external_message_id AS m FROM messages WHERE conversation_id = :i AND "
                                                "external_message_id IS NOT NULL AND delivery = 'sent' ORDER BY created_at", i=cid)]
        last = (await rows(conn, "SELECT external_message_id AS m FROM messages WHERE conversation_id = :i AND role = 'customer' ORDER BY "
                                 "created_at DESC LIMIT 1", i=cid))
        delivery = "sent" if adapter.transactional else "sending"
        if draft_id:
            d = await rows(conn, "SELECT content, external_message_id AS ext FROM messages WHERE id = :m AND conversation_id = :i AND "
                                 "delivery = 'draft' FOR UPDATE", m=draft_id, i=cid)
            if not d:
                raise Refused(409, "this draft was already sent or no longer exists")
            mid, ext = draft_id, d[0]["ext"] or f"<reply-{draft_id}@mcs.matsui-demo.example>"
            note = {"type": "reviewed_by", "name": staff.name, "email": staff.email, "edited": d[0]["content"].strip() != body.strip()}
            await conn.execute(text("UPDATE messages SET content = :c, delivery = :d, external_message_id = :e, actions = coalesce(actions, '[]') "
                                    "|| CAST(:n AS jsonb), created_at = clock_timestamp() WHERE id = :m"),
                               {"c": body, "d": delivery, "e": ext, "n": json.dumps([note]), "m": mid})
        else:
            body = f"{body.strip()}\n\n{SIGNATURE}"
            mid = await add_message(conn, cid, "agent", body, actions=[{"type": "sent_by", "name": staff.name, "email": staff.email}])
            ext = f"<reply-{mid}@mcs.matsui-demo.example>"
            await conn.execute(text("UPDATE messages SET external_message_id = :e, delivery = :d WHERE id = :m"), {"e": ext, "d": delivery, "m": mid})
        ticket = (await rows(conn, "SELECT id FROM tickets WHERE conversation_id = :i ORDER BY id DESC LIMIT 1", i=cid))
        mail = OutboundMail(message_id=ext, to=conv["addr"], subject=subject_for(conv["subject"] or "", ticket[0]["id"] if ticket else None),
                            text=body, html=render_manual(body), in_reply_to=last[0]["m"] if last else None, references=ids[-20:],
                            thread_id=conv["thread"])
        # a later customer message may have its own draft: stay in review until none is left
        await conn.execute(text(
            "UPDATE conversations SET status = CASE WHEN status = 'review' AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id = :i AND "
            "delivery = 'draft') THEN 'bot' ELSE status END, updated_at = now() WHERE id = :i"), {"i": cid})
        if adapter.transactional:
            await adapter.send(mail, conn)
    if not adapter.transactional:
        async with engine.begin() as conn:
            try:
                await adapter.send(mail, conn)
                ok = True
            except Exception:
                log.exception("send failed")
                ok = False
            await conn.execute(text("UPDATE messages SET delivery = :d WHERE id = :m"), {"d": "sent" if ok else "failed", "m": mid})
            if not ok:
                await conn.execute(text("UPDATE conversations SET status = 'review' WHERE id = :i AND status = 'bot'"), {"i": cid})
        if not ok:
            raise Refused(502, "the mail provider refused the message")
    return mid


async def promote(engine: AsyncEngine, embedder: Embedder, mid: uuid.UUID, question: str, answer: str, source_doc: str, by: str) -> str:
    """Reviewed answer → FAQ entry + exact and semantic cache (FR-CA-05), through the cache's write guard."""
    answer = re.sub(r"\s*\[E\d+\]", "", answer).strip()
    question = question.strip()
    if mask_pii(question) != question or mask_pii(answer) != answer:
        raise Refused(422, "remove personal details (phone, e-mail, GSTIN) before promoting")
    vec = (await embedder.embed([question], kind="query"))[0]
    fid = "FAQ-P-" + hashlib.sha1(normalize(question).encode()).hexdigest()[:10].upper()
    async with engine.begin() as conn:
        if not await rows(conn, "SELECT 1 FROM messages WHERE id = :m AND role IN ('assistant', 'agent') AND delivery = 'sent'", m=mid):
            raise Refused(404, "no such sent answer")
        if not await rows(conn, "SELECT 1 FROM kb_documents WHERE doc_id = :d AND status = 'active'", d=source_doc):
            raise Refused(422, "the source must be an active document")
        norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", question.lower())).strip()
        if not norm:
            raise Refused(422, "the question is empty")
        taken: list[dict[str, Any]] = await rows(conn, "SELECT faq_id FROM faq_variants WHERE normalized = :n AND faq_id <> :f", n=norm, f=fid)
        if taken:
            raise Refused(409, f"{taken[0]['faq_id']} already answers this question")
        try:
            await write_answer(conn, question, answer, [source_doc], faq_id=fid, origin="promoted")
        except CacheWriteRejected as exc:
            raise Refused(422, str(exc)) from exc
        await conn.execute(text("INSERT INTO faq_entries (id, category, question, answer, source_doc, origin) VALUES (:i, 'promoted', :q, :a, :d, "
                                "'promoted') ON CONFLICT (id) DO UPDATE SET answer = EXCLUDED.answer, source_doc = EXCLUDED.source_doc, "
                                "status = 'active'"), {"i": fid, "q": question, "a": answer, "d": source_doc})
        await conn.execute(text("INSERT INTO faq_variants (faq_id, text, normalized, embedding) VALUES (:i, :q, :n, CAST(:e AS vector)) "
                                "ON CONFLICT (normalized) DO UPDATE SET embedding = EXCLUDED.embedding"),
                           {"i": fid, "q": question, "n": norm, "e": _vec(vec)})
    log.info("faq promoted", extra={"faq_id": fid, "by": hashlib.sha1(by.encode()).hexdigest()[:8]})
    return fid
