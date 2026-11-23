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

## Workflow

```bash
pip install -r tools/requirements.txt      # markdown
python mock-data/generate.py               # (re)generate mock data
python tools/sync_docs.py                  # after editing JSON or docs-src/*.md
python tools/sync_docs.py --check          # CI gate
```

Edit `docs/requirements-registry.json`, `docs/build-steps.json` or `docs-src/*.md` — never the generated HTML. `trace.build_steps` and `trace.tests` in the registry are generated.

Open the HTML files directly in a browser. Diagrams render offline from `docs/assets/mermaid.min.js` (Mermaid, MIT licence).
