"""Email routes (LLD-14): contact form / demo inbound, Gmail push, demo mailbox view (staff)."""

import asyncio
import base64
import json
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.chat import EngineDep
from app.api.staff import AgentDep
from app.channels.email.adapter import InboundMail
from app.channels.email.demo_mailbox import listing
from app.channels.email.pipeline import ingest
from app.config.settings import get_settings
from app.jobs.queue import enqueue

router = APIRouter(prefix="/v1", tags=["email"])
console_router = APIRouter(prefix="/v1", tags=["email"])
PER_MINUTE = 30
_hits: dict[str, deque[float]] = defaultdict(deque)
_ADDR = re.compile(r"^[^@\s<>,;:\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}$")


def limit(ip: str) -> None:
    """Per process and address; a shared limit belongs in the load balancer (BS-32)."""
    now, q = time.monotonic(), _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= PER_MINUTE:
        raise HTTPException(429, "too many messages, please try again in a minute")
    q.append(now)
    if len(_hits) > 10_000:
        _hits.clear()


class InboundIn(BaseModel):
    sender: str = Field(alias="from", max_length=254)
    name: str = Field(default="", max_length=100)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    serial_no: str | None = Field(default=None, max_length=40)
    in_reply_to: str | None = Field(default=None, max_length=300)


@router.post("/email/inbound", status_code=202)
async def inbound(body: InboundIn, request: Request, engine: EngineDep) -> dict[str, str]:
    """The address is typed by the caller, so it identifies a customer only in the demo mailbox."""
    limit(request.client.host if request.client else "?")
    if not _ADDR.match(body.sender):
        raise HTTPException(422, "invalid e-mail address")
    s = get_settings()
    text = body.body + (f"\nSerial number: {body.serial_no}" if body.serial_no else "")
    cid, _ = await ingest(engine, InboundMail(
        message_id=f"<{uuid.uuid4()}@form.mcs>", from_addr=body.sender.lower(), from_name=re.sub(r"[\r\n]+", " ", body.name),
        subject=re.sub(r"[\r\n]+", " ", body.subject), text=text, in_reply_to=body.in_reply_to,
        authenticated=s.mail_adapter == "demo" and s.personas_enabled))
    return {"conversation_id": str(cid)}


async def verify_push(token: str) -> None:
    s = get_settings()
    if not s.gmail_push_audience or not s.gmail_push_service_account:
        raise HTTPException(404)
    try:
        from google.auth.transport.requests import Request as GRequest
        from google.oauth2 import id_token

        claims = await asyncio.to_thread(id_token.verify_oauth2_token, token, GRequest(), s.gmail_push_audience)
    except Exception as exc:
        raise HTTPException(401, "invalid push token") from exc
    if claims.get("email") != s.gmail_push_service_account or not claims.get("email_verified"):
        raise HTTPException(403, "unexpected push identity")


@router.post("/email/gmail/push", status_code=204)
async def gmail_push(request: Request, engine: EngineDep, authorization: Annotated[str, Header()] = "") -> Response:
    await verify_push(authorization.removeprefix("Bearer ").strip())
    try:
        data = json.loads(base64.b64decode((await request.json())["message"]["data"]))
        history, addr = int(data["historyId"]), str(data["emailAddress"]).lower()
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, "malformed push") from exc
    if addr == get_settings().gmail_delegated_user.lower():
        async with engine.begin() as conn:
            await enqueue(conn, "gmail_sync", {"history_id": str(history)})
    return Response(status_code=204)


@console_router.get("/console/mailbox")
async def mailbox(_: AgentDep, engine: EngineDep, box: Literal["inbox", "outbox"] = "inbox") -> dict[str, Any]:
    async with engine.connect() as conn:
        return {"items": await listing(conn, box)}
