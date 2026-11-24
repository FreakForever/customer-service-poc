"""RFC 822 → InboundMail, quote/signature stripping, question splitting (LLD-12)."""

import email
import email.policy
import re
from email.message import EmailMessage
from email.utils import parseaddr

from app.channels.email.adapter import InboundMail, MailAttachment

_QUOTE_START = re.compile(r"^\s*(>|On .{5,120}wrote:\s*$|-{2,}\s*Original Message|_{10,}\s*$|From:\s.+@|Sent from my )", re.IGNORECASE)
_SIGN_OFF = re.compile(r"^\s*(--\s*|(best |kind |warm )?regards,?|thanks( and regards| & regards|,)?|thank you,?|sincerely,?|yours .{0,15},?)\s*$",
                       re.IGNORECASE)


def strip_body(body: str) -> str:
    lines: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line):
            break
        lines.append(line)
    for i, line in enumerate(lines):
        # a sign-off ends the message only when little follows it
        if _SIGN_OFF.match(line) and len(" ".join(lines[i + 1:]).split()) <= 15:
            lines = lines[:i]
            break
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as h
    return re.sub(r"[ \t]+", " ", h.unescape(t)).strip()


def is_auto(msg: EmailMessage) -> bool:
    addr = parseaddr(str(msg.get("From", "")))[1].lower()
    return (str(msg.get("Auto-Submitted", "no")).lower() not in ("no", "") or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "list")
            or "List-Id" in msg or "X-Autoreply" in msg or str(msg.get("Return-Path", "x")).strip() == "<>"
            or bool(re.match(r"(mailer-daemon|postmaster|no-?reply|do-?not-?reply)@", addr)))


def authenticated(msg: EmailMessage, from_addr: str) -> bool:
    """DMARC pass, or DKIM pass for the From domain, as stamped by the receiving server (Gmail). Only the topmost
    Authentication-Results counts: anything below it could have been written by the sender."""
    results = msg.get_all("Authentication-Results") or []
    if not results or not str(results[0]).strip().lower().startswith("mx.google.com"):
        return False
    r, domain = str(results[0]).lower(), from_addr.rsplit("@", 1)[-1].lower()
    return bool(re.search(r"\bdmarc=pass\b", r)) or bool(re.search(rf"\bdkim=pass\b[^;]*header\.(d|i)=@?{re.escape(domain)}(\s|;|$)", r))


def from_rfc822(raw: bytes, *, thread_id: str | None = None) -> InboundMail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    name, addr = parseaddr(str(msg.get("From", "")))
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part else ""
    if part is not None and part.get_content_subtype() == "html":
        body = html_to_text(body)
    atts = []
    for a in msg.iter_attachments():
        data = a.get_payload(decode=True)
        if isinstance(data, bytes):
            atts.append(MailAttachment(a.get_filename() or "attachment", a.get_content_type(), data))
    return InboundMail(
        message_id=str(msg.get("Message-ID", "")).strip(), from_addr=addr.lower(), from_name=name, subject=str(msg.get("Subject", "")),
        text=body, in_reply_to=(str(msg["In-Reply-To"]).strip() if msg["In-Reply-To"] else None),
        references=str(msg.get("References", "")).split(), attachments=atts, thread_id=thread_id,
        authenticated=authenticated(msg, addr.lower()), auto=is_auto(msg))
