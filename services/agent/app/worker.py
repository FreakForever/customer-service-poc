"""Background worker (LLD-1). BS-01: heartbeat loop only; job handlers arrive in later steps.

On Cloud Run the worker runs as a service, so it also answers GET /health on $PORT.
"""

import asyncio
import json
import logging
import os
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from app.config.settings import get_settings
from app.obs.health import check_all

log = logging.getLogger("mcs.worker")
HEARTBEAT_S = int(os.environ.get("WORKER_HEARTBEAT_S", "60"))


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
    while not stop.is_set():
        log.info("heartbeat %s", await check_all(settings))
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_S)
        except TimeoutError:
            pass
    log.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())
