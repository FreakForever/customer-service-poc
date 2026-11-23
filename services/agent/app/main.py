"""FastAPI app factory (LLD-1, LLD-14). Routers are added by later build steps."""

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.obs.health import check_all
from app.obs.logging import configure_logging, request_id_var


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Matsui CS agent API", version="0.1.0")

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
