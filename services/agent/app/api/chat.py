"""Chat routes (LLD-14; FR-CH-01, FR-CH-02, FR-CH-06). Identity: demo persona chosen at session start (OD-006 → D-038),
only where DEMO_PERSONAS is on."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import session_token
from app.channels import chat
from app.channels import chat_store as store
from app.config.settings import get_settings
from app.db import get_engine

router = APIRouter(prefix="/v1", tags=["chat"])
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
POLL_S, HEARTBEAT_S, STREAM_MAX_S = 1.0, 15.0, 600.0


def engine_dep() -> AsyncEngine:
    e = get_engine()
    if e is None:
        raise HTTPException(503, "chat is unavailable")
    return e


def bearer(authorization: str = Header(default="")) -> str:
    return authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""


TokenDep = Annotated[str, Depends(bearer)]


def require_session(sid: uuid.UUID, token: TokenDep) -> uuid.UUID:
    if not session_token.valid_for(token, sid):
        raise HTTPException(401, "invalid or expired session token")
    return sid


EngineDep = Annotated[AsyncEngine, Depends(engine_dep)]
SessionDep = Annotated[uuid.UUID, Depends(require_session)]


class NewSession(BaseModel):
    persona_customer_id: str | None = Field(default=None, max_length=20)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class FormIn(BaseModel):
    fields: dict[str, str] = Field(max_length=10)


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: Literal[1, -1]
    reason: str | None = Field(default=None, max_length=1000)
    session_id: uuid.UUID


@router.post("/chat/sessions", status_code=201)
async def new_session(body: NewSession, engine: EngineDep) -> dict[str, str]:
    s = get_settings()
    async with engine.begin() as conn:
        if body.persona_customer_id:
            if not s.personas_enabled:
                raise HTTPException(403, "demo personas are disabled")
            if not await store.customer_exists(conn, body.persona_customer_id):
                raise HTTPException(404, "unknown persona")
        sid = await store.create(conn, body.persona_customer_id, s.retention_days)
    return {"session_id": str(sid), "token": session_token.issue(sid, s.retention_days * 86400)}


async def _load(engine: AsyncEngine, sid: uuid.UUID) -> dict[str, Any]:
    async with engine.connect() as conn:
        conv = await store.load(conn, sid)
    if conv is None:
        raise HTTPException(404, "session not found or expired")
    return conv


@router.get("/chat/sessions/{sid}")
async def resume(sid: SessionDep, engine: EngineDep) -> dict[str, Any]:
    conv = await _load(engine, sid)
    async with engine.connect() as conn:
        msgs = await store.transcript(conn, sid)
    return {"messages": msgs, "status": conv["status"], "active_agent": conv["active_agent"]}


async def _reply(engine: AsyncEngine, sid: uuid.UUID, text: str) -> StreamingResponse:
    if not text.strip():
        raise HTTPException(422, "empty message")
    conv = await _load(engine, sid)
    if conv["status"] == "closed":
        raise HTTPException(409, "this conversation is closed")
    async with engine.begin() as conn:
        if not await store.begin_turn(conn, sid):
            raise HTTPException(409, "the previous message is still being answered")
        await store.add_message(conn, sid, "customer", text)
        conv = await store.load(conn, sid) or conv             # state as of the lease

    async def body() -> AsyncIterator[str]:
        async for event, data in chat.stream(engine, sid, conv, text):
            yield chat.encode(event, data)
    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/sessions/{sid}/messages")
async def send(body: MessageIn, sid: SessionDep,
               engine: EngineDep) -> StreamingResponse:
    return await _reply(engine, sid, body.text)


@router.post("/chat/sessions/{sid}/forms/{form_id}")
async def submit_form(form_id: str, body: FormIn, sid: SessionDep,
                      engine: EngineDep) -> StreamingResponse:
    if form_id != "service_request":
        raise HTTPException(404, "unknown form")
    labels = {"description": "Issue", "serial_no": "Serial", "contact": "Contact"}
    if unknown := set(body.fields) - set(labels):
        raise HTTPException(422, f"unknown fields: {', '.join(sorted(unknown))}")
    text = ". ".join(f"{labels[k]} {body.fields[k].strip()[:2000]}" for k in labels if body.fields.get(k, "").strip())
    return await _reply(engine, sid, text)


@router.get("/chat/sessions/{sid}/events")
async def events(request: Request, sid: SessionDep,
                 engine: EngineDep) -> StreamingResponse:
    """Human replies after a handoff. Ends after STREAM_MAX_S; the client reconnects."""
    await _load(engine, sid)
    async with engine.connect() as conn:
        seen = {m["id"] for m in await store.human_messages_after(conn, sid, set())}

    async def body() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        end, beat = loop.time() + STREAM_MAX_S, loop.time()
        yield ": connected\n\n"
        while loop.time() < end and not await request.is_disconnected():
            async with engine.connect() as conn:
                fresh = await store.human_messages_after(conn, sid, seen)
            for m in fresh:
                seen.add(m["id"])
                yield chat.encode("agent_message", {"from": "Matsui service team", "text": m["content"], "message_id": m["id"]})
            if loop.time() - beat > HEARTBEAT_S:
                beat = loop.time()
                yield ": keep-alive\n\n"
            await asyncio.sleep(POLL_S)
    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/feedback", status_code=204)
async def feedback(body: FeedbackIn, token: TokenDep, engine: EngineDep) -> Response:
    require_session(body.session_id, token)
    async with engine.begin() as conn:
        if not await store.add_feedback(conn, body.message_id, body.session_id, body.rating, body.reason):
            raise HTTPException(404, "no such answer in this session")
    return Response(status_code=204)
