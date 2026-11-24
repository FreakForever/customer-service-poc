"""Gmail API adapter (D-016): watch, history sync, fetch, send. Auth is a token callable (service account with
domain-wide delegation, see `delegated_token`), so it can be tested without Google."""

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from email.message import EmailMessage

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.channels.email.adapter import InboundMail, OutboundMail
from app.channels.email.parse import from_rfc822
from app.config.settings import get_settings

log = logging.getLogger("mcs.gmail")
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
TokenFn = Callable[[], Awaitable[str]]


async def delegated_token() -> str:
    def fetch() -> str:
        import google.auth
        from google.auth import iam
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        req = Request()
        creds.refresh(req)
        sa = getattr(creds, "service_account_email", None)
        if not sa or sa == "default":
            raise RuntimeError("Gmail needs a service account with domain-wide delegation")
        signer = iam.Signer(req, creds, sa)
        d = service_account.Credentials(signer, sa, "https://oauth2.googleapis.com/token", scopes=SCOPES,
                                        subject=get_settings().gmail_delegated_user)
        d.refresh(req)
        return str(d.token)
    return await asyncio.to_thread(fetch)


def to_rfc822(m: OutboundMail, sender: str) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, m.to, m.subject, m.message_id
    if m.in_reply_to:
        msg["In-Reply-To"] = m.in_reply_to
    if m.references:
        msg["References"] = " ".join(m.references)
    msg["Auto-Submitted"] = "auto-replied"
    msg.set_content(m.text)
    msg.add_alternative(m.html, subtype="html")
    return msg.as_bytes()


class GmailAdapter:
    name, transactional = "gmail", False

    def __init__(self, token: TokenFn = delegated_token, client: httpx.AsyncClient | None = None) -> None:
        self.token = token
        self.http = client or httpx.AsyncClient(timeout=20)

    async def _call(self, method: str, path: str, **kw) -> dict:
        r = await self.http.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {await self.token()}"}, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def watch(self, conn: AsyncConnection) -> str:
        out = await self._call("POST", "/watch", json={"topicName": get_settings().gmail_pubsub_topic, "labelIds": ["INBOX"],
                                                       "labelFilterBehavior": "INCLUDE"})
        # never move an existing cursor: messages between the two would be skipped
        await conn.execute(text("INSERT INTO mail_state (key, value) VALUES ('gmail_history', :v) ON CONFLICT (key) DO NOTHING"),
                           {"v": str(out["historyId"])})
        return str(out["historyId"])

    async def fetch(self, mid: str) -> InboundMail:
        m = await self._call("GET", f"/messages/{mid}", params={"format": "raw"})
        return from_rfc822(base64.urlsafe_b64decode(m["raw"] + "=="), thread_id=m.get("threadId"))

    async def new_messages(self, since: str) -> tuple[list[str], str]:
        ids: list[str] = []
        page, newest = None, since
        while True:
            params = {"startHistoryId": since, "historyTypes": "messageAdded", "labelId": "INBOX"} | ({"pageToken": page} if page else {})
            out = await self._call("GET", "/history", params=params)
            newest = str(out.get("historyId", newest))
            for h in out.get("history", []):
                ids += [a["message"]["id"] for a in h.get("messagesAdded", [])]
            page = out.get("nextPageToken")
            if not page:
                return list(dict.fromkeys(ids)), newest

    async def recent_ids(self, days: int = 2) -> tuple[list[str], str]:
        """History cursor expired (404): re-read recent inbox mail; processing is idempotent by Message-ID."""
        out = await self._call("GET", "/messages", params={"labelIds": "INBOX", "q": f"newer_than:{days}d", "maxResults": 100})
        prof = await self._call("GET", "/profile")
        return [m["id"] for m in out.get("messages", [])], str(prof["historyId"])

    async def send(self, mail: OutboundMail, conn: AsyncConnection) -> None:
        raw = base64.urlsafe_b64encode(to_rfc822(mail, get_settings().support_address)).decode()
        await self._call("POST", "/messages/send", json={"raw": raw, **({"threadId": mail.thread_id} if mail.thread_id else {})})
