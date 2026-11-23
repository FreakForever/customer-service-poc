# Built remotely by Cloud Build (infra/gcp/cloudbuild.deploy.yaml) — never locally (D-015).
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/srv/.venv/bin:$PATH"
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
# Same image for API and worker; the worker service overrides the command with `python -m app.worker`.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
