"""Trace persistence into Postgres (BS-11, NFR-OBS-01) on mcs_test."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings
from app.db import warm_pool
from app.obs.tracing import TraceRecorder, save_trace
from app.orchestrator.graph import run_turn
from app.orchestrator.state import Envelope

pytestmark = [pytest.mark.network, pytest.mark.req("NFR-OBS-01")]
AGENT = Path(__file__).resolve().parents[2]


async def test_trace_and_node_spans_are_stored():
    url = os.environ.get("DATABASE_URL_TEST") or Settings().database_url_test.get_secret_value()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    await asyncio.to_thread(subprocess.run, ["uv", "run", "alembic", "-x", f"url={url}", "upgrade", "head"],
                            cwd=AGENT, check=True, capture_output=True, text=True)
    await warm_pool()          # as the API does at startup
    rec = TraceRecorder(mode="debug")
    final = await run_turn(Envelope(channel="chat", text="Is AMC AMC-2025-0014 active?"), recorder=rec)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await save_trace(conn, rec)
        async with engine.connect() as conn:
            t = (await conn.execute(text("SELECT channel, answered_tier, intents, mode, detail, latency_ms "
                                         "FROM traces WHERE id = :i"), {"i": rec.trace_id})).one()
            spans = (await conn.execute(text("SELECT node, duration_ms, attrs FROM spans WHERE trace_id = :i "
                                             "ORDER BY id"), {"i": rec.trace_id})).all()
            assert (t.channel, t.answered_tier, t.intents, t.mode) == ("chat", "T0", ["amc.status"], "debug")
            assert t.detail["path"] == final["path"] and t.latency_ms >= 0
            assert [s.node for s in spans] == final["path"] and all(s.attrs["status"] == "ok" for s in spans)
            await conn.execute(text("DELETE FROM traces WHERE id = :i"), {"i": rec.trace_id})
            await conn.commit()
    finally:
        await engine.dispose()
