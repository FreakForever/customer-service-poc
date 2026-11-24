"""Demo mailbox: Postgres inbox/outbox (D-016). Nothing leaves the database."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.channels.email.adapter import InboundMail, OutboundMail
from app.config.settings import get_settings


class DemoMailboxAdapter:
    name, transactional = "demo", True

    async def record_inbound(self, m: InboundMail, conn: AsyncConnection) -> None:
        await conn.execute(text(
            "INSERT INTO mailbox_messages (box, message_id, in_reply_to, references_, from_addr, to_addr, subject, body_text, "
            "processed) VALUES ('inbox', :id, :irt, :refs, :f, :t, :s, :b, true) ON CONFLICT (message_id) DO NOTHING"),
            {"id": m.message_id, "irt": m.in_reply_to, "refs": " ".join(m.references), "f": m.from_addr,
             "t": get_settings().support_address, "s": m.subject, "b": m.text})

    async def send(self, mail: OutboundMail, conn: AsyncConnection) -> None:
        await conn.execute(text(
            "INSERT INTO mailbox_messages (box, message_id, in_reply_to, references_, from_addr, to_addr, subject, body_text, "
            "body_html, processed) VALUES ('outbox', :id, :irt, :refs, :f, :t, :s, :b, :h, true) ON CONFLICT (message_id) DO NOTHING"),
            {"id": mail.message_id, "irt": mail.in_reply_to, "refs": " ".join(mail.references), "f": get_settings().support_address,
             "t": mail.to, "s": mail.subject, "b": mail.text, "h": mail.html})


async def listing(conn: AsyncConnection, box: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT message_id, in_reply_to, from_addr, to_addr, subject, body_text, body_html, created_at "
                                   "FROM mailbox_messages WHERE box = :b ORDER BY created_at DESC LIMIT :n"), {"b": box, "n": limit})
    return [dict(r) for r in rows.mappings()]
