# Matsui customer service POC — email + chat (hybrid rule + AI multi-agent)

Design pack and mock data to start the build. All data is **synthetic** (see `mock-data/README.md`).

## What's here

| Path | What it is |
|---|---|
| `docs/Requirements.html` | Vision, scope, personas, **interactive requirement registry** (filters, deep links) and traceability matrix |
| `docs/HighLevelDesign.html` | Functional, technical and deployment architecture, design decisions, open decisions, risks |
| `docs/LLD.html` | Implementation contract (repo layout, config, DB schema, KG schema + Cypher, orchestrator graph, router, cache, RAG, GraphRAG, agents/tools, verifier, channels, upload, APIs, UI, eval, tests, deployment) and **build steps with a progress column** |
| `docs/requirements-registry.json` | Source of truth for requirements (unique, permanent, traceable IDs) |
| `docs/build-steps.json` | Source of truth for the build plan and progress (BS-01 … BS-33) |
| `docs/BUILD_LOOP.md` | Prompt to drive the build with Claude Code `/loop` or `/goal` |
| `docs-src/*.md`, `docs-src/theme.css` | Prose sources for the three HTML docs |
| `tools/sync_docs.py` | Validates traceability, fills generated trace fields, regenerates HTML (`--check` for CI) |
| `mock-data/` | Deterministic generator + generated data for RAG, KG-RAG, tools, eval, channels, runtime uploads |
| `DECISIONS.md` | Decision log (mirrors HLD §6–7) |

## Prerequisites (no Docker needed)

`gcloud` CLI with a GCP project, Python 3.12 + `uv`, Node 20 + `pnpm`, Cloud SQL Auth Proxy. All databases and services run on Google Cloud (Cloud SQL, Neo4j AuraDB, Cloud Storage, Vertex AI); images are built by Cloud Build and run on Cloud Run. See LLD §18.

## Quick start (dev, GCP)

```bash
gcloud auth login && gcloud auth application-default login
gcloud config set project <your-project>
gcloud components install cloud-sql-proxy          # Makefile also finds it next to gcloud

# 1. Create Neo4j AuraDB Free at console.neo4j.io and keep the downloaded credentials .txt
# 2. Bootstrap GCP (idempotent; first run creates Cloud SQL, ~10 min). BUDGET_AMOUNT is optional.
NEO4J_CREDS_FILE=~/Downloads/Neo4j-xxxx.txt BUDGET_AMOUNT=50 make bootstrap-gcp
# 3. Local env from Secret Manager (git-ignored .env)
make env
# 4. Run natively: Cloud SQL proxy :5432, API :8000, worker, web :3000
make dev
curl localhost:8000/health        # {"status":"ok","db":"ok","neo4j":"ok",...}
# 5. Build on Cloud Build and deploy to Cloud Run dev
make deploy-dev
```

Code lives in `services/agent` (FastAPI + worker, `uv`) and `apps/web` (Next.js, `pnpm`); layout per LLD §1.

## Driving the build with Claude Code

| Command | What it does |
|---|---|
| `/build-step` | Runs the next eligible build step through `docs/BUILD_LOOP.md` (implement, verify, update JSON, `make docs`, commit) |
| `/build-step bs-02` | Runs a specific step. Refuses if it is already done or its dependencies aren't done |
| `/build-status` | Read-only progress summary and the next eligible step |
| `/loop /build-step` | Keeps running steps back to back (Esc to stop) |

Command files live in `.claude/commands/`.

## Workflow

```bash
pip install -r tools/requirements.txt      # markdown
python mock-data/generate.py               # (re)generate mock data
python tools/sync_docs.py                  # after editing JSON or docs-src/*.md
python tools/sync_docs.py --check          # CI gate
```

Edit `docs/requirements-registry.json`, `docs/build-steps.json` or `docs-src/*.md` — never the generated HTML. `trace.build_steps` and `trace.tests` in the registry are generated.

Open the HTML files directly in a browser. Diagrams render offline from `docs/assets/mermaid.min.js` (Mermaid, MIT licence).
