"""Async audit of chat answers (LLD-11, FR-VF-05): a sample is judged by the worker after the customer has the answer. The score goes to
`traces.groundedness`; below PASS the trace is flagged, and the analytics page lists it."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config.settings import get_settings
from app.jobs.queue import MAX_ATTEMPTS, Job, enqueue
from app.orchestrator.policies import ChannelPolicy
from app.orchestrator.state import TurnState
from app.providers.base import LLM
from app.retrieval.evidence import Evidence
from app.rules.pii import mask_deep
from app.verify.judge import PASS, judge

log = logging.getLogger("mcs.audit")
TIMEOUT_MS = 30_000
TIERS = ("T2", "T3")             # knowledge answers; T0/T1 state no new facts


def wanted(final: TurnState, policy: ChannelPolicy, trace_id: uuid.UUID) -> bool:
    """Sampled by trace id, so the choice is stable and needs no state."""
    d = final.get("draft")
    v = policy.verification
    return (v.judge == "async" and final.get("answered_tier") in TIERS and d is not None and bool(d.citations) and not d.abstained
            and final.get("blocked_draft") is None and bool(final.get("evidence")) and trace_id.int % 10_000 < v.judge_sample_rate * 10_000)


async def request(conn: AsyncConnection, final: TurnState, trace_id: uuid.UUID) -> None:
    """The trace keeps only excerpts of the evidence, so the job carries the full text (masked) until it has run."""
    payload = mask_deep({"question": final["env"].text[:4000], "answer": final["response"]["text"],
                         "evidence": [{"id": e.id, "kind": e.kind, "source": e.source, "text": e.text} for e in final.get("evidence") or []]})
    await enqueue(conn, "audit_sample", {"trace_id": str(trace_id), **payload})


def make_audit_handler(engine: AsyncEngine, llm: Callable[[], LLM]):
    async def forget(conn: AsyncConnection, job: Job) -> None:
        await conn.execute(text("UPDATE jobs SET payload = CAST(:p AS jsonb) WHERE id = :i"), {"p": json.dumps({"trace_id": job.payload["trace_id"]}), "i": job.id})

    async def handle(job: Job) -> dict[str, Any]:
        p = job.payload
        try:
            v = await judge(p["question"], p["answer"], [Evidence(**e) for e in p["evidence"]], llm(), model=get_settings().model_strong, timeout_ms=TIMEOUT_MS)
        except Exception:
            if job.attempts >= MAX_ATTEMPTS:         # giving up: don't keep the conversation text in the queue
                async with engine.begin() as conn:
                    await forget(conn, job)
            raise
        flagged = v.faithfulness < PASS
        async with engine.begin() as conn:
            n = (await conn.execute(text("UPDATE traces SET groundedness = :g, detail = coalesce(detail, '{}') || CAST(:d AS jsonb) WHERE id = :t"),
                                    {"g": v.faithfulness, "t": uuid.UUID(p["trace_id"]),
                                     "d": json.dumps({"audit": {"flagged": flagged, "unsupported_claims": mask_deep(v.unsupported_claims)[:5]}})})).rowcount
            await forget(conn, job)
        return {"trace_id": p["trace_id"], "groundedness": v.faithfulness, "flagged": flagged, **({} if n else {"skipped": "trace is gone"})}
    return handle
