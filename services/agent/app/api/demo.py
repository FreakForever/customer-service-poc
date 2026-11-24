"""Demo controls (FR-UI-08), admin only and only where demo personas are on. `POST /demo/reset` arrives in BS-33."""

import uuid

from fastapi import APIRouter, HTTPException

from app.api.chat import EngineDep
from app.api.staff import AdminDep
from app.channels.email.pipeline import ingest
from app.channels.email.samples import SAMPLES, sample_mails
from app.config.settings import get_settings

router = APIRouter(prefix="/v1/demo", tags=["demo"])


@router.post("/send-sample-emails", status_code=202)
async def send_sample_emails(_: AdminDep, engine: EngineDep) -> dict[str, int]:
    s = get_settings()
    if not s.personas_enabled or s.mail_adapter != "demo":
        raise HTTPException(403, "sample e-mails are only for the demo mailbox")
    if not SAMPLES.exists():
        raise HTTPException(503, "sample e-mails are not available in this image")
    run = uuid.uuid4().hex[:8]
    return {"count": sum([(await ingest(engine, m))[1] for m in sample_mails(f"-{run}")])}
