"""Background worker (LLD-1, LLD-13): claims jobs from Postgres (ingest_document from BS-07) and logs a heartbeat.

On Cloud Run the worker runs as a service, so it also answers GET /health on $PORT.
"""

import asyncio
import json
import logging
import os
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.config.settings import get_settings
from app.jobs.queue import run_once
from app.obs.health import check_all

log = logging.getLogger("mcs.worker")
HEARTBEAT_S = int(os.environ.get("WORKER_HEARTBEAT_S", "60"))
POLL_S = float(os.environ.get("WORKER_POLL_S", "2"))


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ok = self.path in ("/", "/health", "/healthz")
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if ok:
            self.wfile.write(json.dumps({"status": "ok", "role": "worker"}).encode())

    def log_message(self, *_args: object) -> None:
        pass


def _serve_health(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Health)
    Thread(target=server.serve_forever, daemon=True).start()


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    if port := os.environ.get("PORT"):
        _serve_health(int(port))
    settings = get_settings()
    engine, handlers = _handlers(settings)
    next_beat = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_beat:
            log.info("heartbeat %s", await check_all(settings))
            next_beat = time.monotonic() + HEARTBEAT_S
        job = None
        if engine is not None:
            try:
                job = await run_once(engine, handlers)
            except Exception:
                log.exception("job loop error")
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_S)
            except TimeoutError:
                pass
    if engine is not None:
        await engine.dispose()
    log.info("worker stopped")


def _handlers(settings):
    url = settings.resolved_database_url()
    if not url:
        log.warning("no database configured: job loop disabled")
        return None, {}
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.ingest.pipeline import make_ingest_handler
    from app.providers import get_embedder

    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, {"ingest_document": make_ingest_handler(engine, get_embedder())}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())
