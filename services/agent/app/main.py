"""FastAPI app factory (LLD-1, LLD-14). Routers are added by later build steps."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.obs.health import check_all
from app.obs.logging import configure_logging, request_id_var


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Warm the rule dictionaries and the kNN intent index in the background so the first turns don't pay for
    them inside their node budgets (BS-13)."""
    async def warm() -> None:
        try:
            from app.db import warm_pool
            from app.providers import get_embedder
            from app.retrieval.rerank import get_reranker
            from app.router.knn import get_knn
            from app.rules.context import get_rule_context

            await warm_pool()
            await get_rule_context()
            await get_knn().build(get_embedder())
            await asyncio.to_thread(get_reranker)
            logging.getLogger("mcs.api").info("router warm")
        except Exception as exc:  # noqa: BLE001 - lazily retried on first use
            logging.getLogger("mcs.api").warning("router warm-up failed: %s", type(exc).__name__)

    task = asyncio.create_task(warm()) if get_settings().warm_on_startup else None
    yield
    if task is not None:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Matsui CS agent API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        """request_id for JSON logs: X-Request-ID, else the Cloud Run trace header, else a new id (NFR-OBS-02)."""
        rid = (request.headers.get("x-request-id")
               or request.headers.get("x-cloud-trace-context", "").split("/")[0] or uuid.uuid4().hex)
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    from app.api.chat import router as chat_router

    app.include_router(chat_router)
    from app.api.demo import router as demo_router
    from app.api.email import console_router
    from app.api.email import router as email_router

    app.include_router(email_router)
    app.include_router(console_router)
    from app.api.console import router as agent_console_router

    app.include_router(agent_console_router)
    app.include_router(demo_router)
    from app.api.kb import router as kb_router

    app.include_router(kb_router)
    if settings.debug_api if settings.debug_api is not None else settings.env == "dev":
        from app.api.debug import router as debug_router

        app.include_router(debug_router)

    # Canonical paths are /health and /ready: Cloud Run's front end reserves paths ending in "z" and answers
    # /healthz with its own 404 before the request reaches the container (D-018). The *z aliases are kept
    # for local use and probes that expect them.
    @app.get("/health")
    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Liveness plus dependency status; always 200 so Cloud Run keeps the instance."""
        return await check_all(get_settings())

    @app.get("/ready")
    @app.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        """Readiness: 503 unless Postgres and Neo4j are reachable."""
        body = await check_all(get_settings())
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    return app


app = create_app()
