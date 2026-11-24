"""Put the 10 sample e-mails into the demo mailbox and answer them in-process (BS-21).   make demo-emails
Uses the configured model; replies go to the demo outbox only. Re-running adds nothing (same Message-IDs)."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from app.channels.email.pipeline import ingest, make_reply_handler
from app.channels.email.samples import sample_mails
from app.config.settings import get_settings
from app.db import get_engine
from app.jobs.queue import run_once
from app.worker import _warm


async def main() -> int:
    if get_settings().mail_adapter != "demo":
        print("MAIL_ADAPTER must be demo: the samples are never sent through a real mailbox", file=sys.stderr)
        return 2
    engine = get_engine()
    assert engine is not None
    await _warm()
    handler, results = make_reply_handler(engine), {}

    async def record(job):
        out = await handler(job)
        results[job.payload["message_id"]] = out
        return out
    new = 0
    for m in sample_mails():
        new += (await ingest(engine, m))[1]
    while await run_once(engine, {"email_reply": record}):
        pass
    print(f"new messages: {new}")
    for mid, r in sorted(results.items()):
        print(mid, json.dumps({k: r.get(k) for k in ("delivery", "status", "tier", "confidence", "reason", "latency_ms", "skipped", "review")}))
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
