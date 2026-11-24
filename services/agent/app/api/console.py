"""Agent console routes (LLD-14; FR-HO-01..03, FR-CA-05). All need the agent role."""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.chat import EngineDep
from app.api.staff import AgentDep, current_staff
from app.channels import console
from app.channels import console_store as store
from app.channels.email.pipeline import get_adapter
from app.providers import get_embedder

router = APIRouter(prefix="/v1/console", tags=["console"], dependencies=[Depends(current_staff)])
Q = Annotated[str, Query(max_length=100)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    approve_draft_id: uuid.UUID | None = None


class PromoteIn(BaseModel):
    question: str = Field(min_length=5, max_length=300)
    answer: str = Field(min_length=5, max_length=4000)
    source_doc: str = Field(min_length=1, max_length=100)


class TicketPatch(BaseModel):
    status: Literal["new", "assigned", "scheduled", "in_progress", "awaiting_parts", "resolved", "closed"] | None = None
    assigned_engineer: str | None = Field(default=None, max_length=100)
    scheduled_visit: dt.date | None = None


@router.get("/conversations")
async def conversations(staff: AgentDep, engine: EngineDep, view: Literal["review", "live", "p1", "mine", "all"] = "all", q: Q = "",
                        limit: Limit = 30, offset: Offset = 0) -> dict[str, Any]:
    async with engine.connect() as conn:
        items = await store.conversations(conn, view, staff.email, q.strip(), limit + 1, offset)
        counts = await store.counts(conn, staff.email)
    return {"items": items[:limit], "next": offset + limit if len(items) > limit else None, "counts": counts}


@router.get("/conversations/{cid}")
async def conversation(cid: uuid.UUID, _: AgentDep, engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        out = await store.detail(conn, cid)
    if out is None:
        raise HTTPException(404, "no such conversation")
    return out


async def _exists(engine: Any, cid: uuid.UUID) -> str:
    async with engine.connect() as conn:
        got = await store.rows(conn, "SELECT channel FROM conversations WHERE id = :i", i=cid)
    if not got:
        raise HTTPException(404, "no such conversation")
    return str(got[0]["channel"])


@router.post("/conversations/{cid}/takeover", status_code=204)
async def takeover(cid: uuid.UUID, staff: AgentDep, engine: EngineDep) -> Response:
    await _exists(engine, cid)
    async with engine.begin() as conn:
        if not await store.takeover(conn, cid, staff.email, staff.name or "Service team"):
            raise HTTPException(409, "another agent owns this conversation, or it is closed")
    return Response(status_code=204)


async def _give_back(cid: uuid.UUID, staff: Any, engine: Any, status: str) -> Response:
    await _exists(engine, cid)
    async with engine.begin() as conn:
        if not await store.give_back(conn, cid, staff.email, status=status, force=staff.role == "admin"):
            raise HTTPException(409, "another agent owns this conversation, or it is closed")
    return Response(status_code=204)


@router.post("/conversations/{cid}/release", status_code=204)
async def release(cid: uuid.UUID, staff: AgentDep, engine: EngineDep) -> Response:
    return await _give_back(cid, staff, engine, "bot")


@router.post("/conversations/{cid}/close", status_code=204)
async def close(cid: uuid.UUID, staff: AgentDep, engine: EngineDep) -> Response:
    return await _give_back(cid, staff, engine, "closed")


@router.post("/conversations/{cid}/messages", status_code=201)
async def reply(cid: uuid.UUID, body: ReplyIn, staff: AgentDep, engine: EngineDep) -> dict[str, str]:
    if not body.text.strip():
        raise HTTPException(422, "empty message")
    channel = await _exists(engine, cid)
    try:
        if channel == "email":
            mid = await console.send_email(engine, get_adapter(), cid, staff, body.text, body.approve_draft_id)
        else:
            async with engine.begin() as conn:
                mid = await console.send_chat(conn, cid, staff, body.text)
    except console.Refused as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"message_id": str(mid)}


@router.get("/traces/{tid}")
async def trace(tid: uuid.UUID, _: AgentDep, engine: EngineDep) -> dict[str, Any]:
    async with engine.connect() as conn:
        out = await store.trace(conn, tid)
    if out is None:
        raise HTTPException(404, "no such trace")
    return out


@router.post("/messages/{mid}/promote-faq", status_code=201)
async def promote_faq(mid: uuid.UUID, body: PromoteIn, staff: AgentDep, engine: EngineDep) -> dict[str, str]:
    try:
        return {"faq_id": await console.promote(engine, get_embedder(), mid, body.question, body.answer, body.source_doc, staff.email)}
    except console.Refused as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.get("/tickets")
async def tickets(staff: AgentDep, engine: EngineDep, status: Literal["", "open", "new", "assigned", "scheduled", "in_progress", "awaiting_parts",
                  "resolved", "closed"] = "", priority: Literal["", "P1", "P2", "P3"] = "", mine: bool = False, q: Q = "", limit: Limit = 50,
                  offset: Offset = 0) -> dict[str, Any]:
    async with engine.connect() as conn:
        items = await store.tickets(conn, status=status, priority=priority, mine=staff.name if mine else "", q=q.strip(), limit=limit + 1, offset=offset)
        engineers = [r["e"] for r in await store.rows(conn, "SELECT DISTINCT assigned_engineer AS e FROM tickets WHERE assigned_engineer IS NOT NULL ORDER BY 1")]
    return {"items": items[:limit], "next": offset + limit if len(items) > limit else None, "engineers": engineers}


@router.patch("/tickets/{tid}")
async def patch_ticket(tid: str, body: TicketPatch, staff: AgentDep, engine: EngineDep) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if not changes or ("status" in changes and changes["status"] is None):
        raise HTTPException(422, "nothing to change")
    async with engine.begin() as conn:
        out = await store.update_ticket(conn, tid, changes, staff.name or staff.email)
    if out is None:
        raise HTTPException(404, "no such ticket")
    return out
