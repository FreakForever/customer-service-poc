"""Debug route (dev only): run a message through the orchestrator and return its trace (BS-11, LLD-14).

`POST /v1/debug/run {text, channel?, customer_id?, meta?, persist?}` → `{trace, path, response, persisted}`.
Mounted only when DEBUG_API is true, or ENV=dev and DEBUG_API is unset. Traces are stored with mode='debug' so
analytics can exclude them.
"""

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import get_engine
from app.obs.tracing import TraceRecorder, save_trace
from app.orchestrator.graph import run_turn
from app.orchestrator.state import Channel, CustomerRef, Envelope

log = logging.getLogger("mcs.api.debug")
router = APIRouter(prefix="/v1/debug", tags=["debug"])


class RunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    channel: Channel = "chat"
    customer_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    persist: bool = True


@router.post("/run")
async def run(req: RunRequest) -> dict[str, Any]:
    env = Envelope(channel=req.channel, text=req.text, meta=req.meta,
                   customer=CustomerRef(customer_id=req.customer_id) if req.customer_id else None)
    rec = TraceRecorder(mode="debug")
    final = await run_turn(env, recorder=rec)
    persisted, error = False, None
    engine = get_engine()
    if req.persist and engine is not None:
        try:
            async with engine.begin() as conn:
                await save_trace(conn, rec)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - the run result is still useful without persistence
            error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("trace not persisted: %s", error)
    log.info("debug run", extra={"path": final.get("path"), "tier": final.get("answered_tier")})
    return {"trace": rec.as_dict(), "path": final.get("path", []), "response": final.get("response", {}),
            "persisted": persisted, "persist_error": error}
