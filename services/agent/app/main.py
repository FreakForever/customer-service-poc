"""FastAPI app factory (LLD-1, LLD-14). Routers are added by later build steps."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.obs.health import check_all


def create_app() -> FastAPI:
    app = FastAPI(title="Matsui CS agent API", version="0.1.0")

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
