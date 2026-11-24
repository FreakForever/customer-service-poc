"""Mail adapter interface (LLD-12, D-016, FR-EM-01)."""

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass
class MailAttachment:
    filename: str
    mime: str
    data: bytes


@dataclass
class InboundMail:
    message_id: str
    from_addr: str
    subject: str
    text: str
    from_name: str = ""
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    attachments: list[MailAttachment] = field(default_factory=list)
    thread_id: str | None = None          # provider thread (Gmail)
    authenticated: bool = True            # sender address verified (DKIM/DMARC); False = never identified as a customer
    auto: bool = False                    # auto-reply, bounce or list mail: stored, never answered


@dataclass
class OutboundMail:
    message_id: str
    to: str
    subject: str
    text: str
    html: str
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    thread_id: str | None = None


class MailAdapter(Protocol):
    name: str
    transactional: bool                    # True: send() commits with the caller's transaction

    async def send(self, mail: OutboundMail, conn: AsyncConnection) -> None: ...
