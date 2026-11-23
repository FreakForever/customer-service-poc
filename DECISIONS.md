# Decision log — Matsui CS POC

Format: `D-nnn | date | status | decision | why | alternatives | affects (REQ / HLD / LLD)`. Open items are `OD-nnn` until closed; when closed, add a `D-` entry and mark the OD `closed → D-nnn`. Keep HighLevelDesign §6–7 (docs-src/hld.md) in sync.

## Decisions

| ID | Date | Status | Decision | Why | Alternatives | Affects |
|---|---|---|---|---|---|---|
| D-001 | 2026-11-23 | accepted | Code-based orchestrator (LangGraph state graph); LLM supervisor only in T3 planner and email | ms-level deterministic routing; supervisor adds 1–2 sequential LLM calls per turn | LLM supervisor everywhere; single ReAct agent | FR-OR-02, HLD-3.2, LLD-5 |
| D-002 | 2026-11-23 | accepted | Tiered cascade T0–T4 with early exit + speculative retrieval | Big models only when needed; voice-ready fast path | One RAG path | FR-OR-03, HLD-3.3 |
| D-003 | 2026-11-23 | accepted | Python/FastAPI agent service; Next.js/TS web | AI ecosystem (LangGraph, rerankers, parsers, Neo4j GraphRAG) | Node end to end like the portal POC | HLD-4.1, LLD-1 |
| D-004 | 2026-11-23 | accepted | Postgres + pgvector for relational, vectors, FTS, cache, traces | One store, transactional, enough at demo scale | Vector DB + Redis | HLD-4.2, LLD-3 |
| D-005 | 2026-11-23 | accepted | Neo4j 5 for the KG | Cypher, full-text, drivers, tooling | Postgres CTEs, Apache AGE | FR-KG-01, LLD-4 |
| D-006 | 2026-11-23 | accepted | GraphRAG: templates first, guarded Text2Cypher fallback; KG = product knowledge only | Fast + safe; customer data behind scoped tools | Text2Cypher only | FR-KG-03/04, LLD-9 |
| D-007 | 2026-11-23 | accepted | Semantic cache only for curated public answers (τ 0.90 chat / 0.92 email) with source-doc invalidation | No stale/personal cache hits | Cache all answers | FR-CA-*, LLD-7 |
| D-008 | 2026-11-23 | accepted | Sync rule checks everywhere; LLM judge sync for email, async 20% sample for chat | Chat latency vs auditability | Judge every chat turn | FR-VF-*, LLD-11 |
| D-009 | 2026-11-23 | superseded by D-016 | Mailpit in dev behind InboundMailAdapter | Real email loop without accounts | Gmail API first | FR-EM-01, LLD-12 |
| D-010 | 2026-11-23 | accepted | SSE for chat and console live updates | Cloud Run friendly, simple | WebSockets | FR-CH-01, LLD-12 |
| D-011 | 2026-11-23 | accepted | Postgres job table + worker | Fewer moving parts | Pub/Sub, Celery | LLD-13 |
| D-012 | 2026-11-23 | accepted | Deterministic synthetic data generator + eval set | Reproducible demos/benchmarks | Hand fixtures | DR-01/02 |
| D-013 | 2026-11-23 | accepted | Provider-agnostic LLM layer; fast vs strong model per node | Swap after bake-off | Single vendor | NFR-REL-01, LLD-2 |
| D-014 | 2026-11-23 | accepted | Docs as code: JSON registry + build steps → HTML; CI trace gate | Traceability without manual sync | Hand-edited HTML | NFR-MNT-01 |
| D-015 | 2026-11-23 | accepted | GCP-first, no local Docker: Cloud SQL + AuraDB Free + GCS + Vertex for dev; native app processes; Cloud Build CI/images; Cloud Run dev + demo | No Docker on the dev machine; dev matches demo platform | Docker Compose locally | NFR-DEP-01/02, HLD-5, LLD-18, BS-01/06/32 |
| D-019 | 2026-11-23 | accepted | Provider layer details: `FALLBACK_PROVIDER`/`MODEL_FALLBACK`, `EMBED_PROVIDER`, `VERTEX_LOCATION`, `ANTHROPIC_VIA` settings; `Msg.prompt_id` for scripting; non-transient errors skip the retry; stream retry only before the first chunk; SDK retries off; interim defaults `gemini-2.5-flash`/`gemini-2.5-pro`/`text-embedding-005` (768 d) pending OD-002/OD-004 | LLD-2 named a fallback but no settings for it; retrying a refusal or 400 wastes budget; a half-streamed answer can't be restarted silently; one retry layer keeps latency predictable | Logical model aliases; SDK-managed retries | NFR-REL-01, NFR-SEC-02, LLD-2, BS-02 |
| D-020 | 2026-11-23 | accepted | Schema details in LLD-3: an IMMUTABLE `heading_text(text[])` wrapper feeds the generated `kb_chunks.tsv`; partial unique index `kb_documents_one_active` (one active version per doc); migrations ensure `mcs_ro` and its grants (incl. default privileges) but never set its password; `purge_expired(retention_days int DEFAULT NULL)` returns deleted counts and falls back to `mcs.retention_days`, then 30; explicit index names plus FK/lookup indexes | Postgres rejects `array_to_string` in a generated column (only STABLE); FR-KB-05 needs exactly one active version; a password in a migration would be a committed secret; Cloud SQL users can't reliably set custom GUCs at database level, and the purge job wants counts to log | Trigger-maintained tsv; app-only version check; hardcoded `mcs_ro` password | FR-KB-05, NFR-SEC-04, NFR-SEC-02, LLD-3, BS-03 |
| D-021 | 2026-11-23 | accepted | KG templates take the model's family from `(Model)-[:BELONGS_TO]->(ProductFamily)` and filter `RESOLVED_BY.family`; new T-PARTS (parts for a cause in a family); deterministic tie-breaks (cause id, part id); `path_id` format `<template>:<ids>`; read-only access = READ transactions always, plus `NEO4J_RO_USER` where hosting has RBAC (not on AuraDB Free) | LLD-4 used `m.family`/`e.id[0..3]`, but Model nodes have no family property and family ids aren't always a 3-char id prefix; the build step lists T-PARTS (FR-KG-03 "parts for cause") but LLD-4 had no query; ties in likelihood made T-COVER non-deterministic; AuraDB Free can't create users | Add `family` to Model nodes in the generator; separate RO user now (needs paid Aura) | FR-KG-01, FR-KG-03, LLD-4, BS-05, OD-003 |
| D-018 | 2026-11-23 | accepted | Health endpoints are `/health` and `/ready`; `/healthz`, `/readyz` kept as local aliases | Cloud Run's Google Front End reserves paths ending in `z` and returns its own 404 for `/healthz` (seen on the first BS-01 deploy) | Keep `/healthz` and probe a different path on Cloud Run | NFR-DEP-01, LLD-14, LLD-18, BS-01 |
| D-017 | 2026-11-23 | accepted | BS-01 deploy details: dedicated `mcs-build@` SA for Cloud Build and `mcs-web@` for web; images tagged `_TAG` (git SHA, `-dirty`); secrets named `mcs-<env>-*`; `mcs_ro` created as a plain SQL role (not via gcloud, which would make it cloudsqlsuperuser); budget alert opt-in via `BUDGET_AMOUNT`; `NEO4J_DATABASE` added (AuraDB Free names user/db after the instance id) | Least privilege instead of the default compute SA; `$SHORT_SHA` is empty for manual submits; truly read-only role | Default Cloud Build/compute SA; Terraform | NFR-DEP-01, LLD-2, LLD-18, BS-01 |
| D-016 | 2026-11-23 | accepted | Email adapters: built-in demo mailbox (Postgres) + Gmail API with Pub/Sub push; Mailpit dropped | Needs no Docker; Cloud Run is HTTP-only so an SMTP sink can't run there | Mailpit, SendGrid inbound parse | FR-EM-01, LLD-12, BS-21 |

## Open decisions

| ID | Question | Default until decided | Decide by |
|---|---|---|---|
| OD-001 | Matsui brand palette/logo/photography | Placeholder tokens navy #0A2540 / blue #0057A8 | Before BS-22 |
| OD-002 | LLM provider and models | Vertex AI, configurable | BS-30 |
| OD-003 | Neo4j hosting for demo env | AuraDB Free in dev; demo TBD (read-only user needs paid tier or self-managed) | Before BS-32 |
| OD-004 | Embedding model / dimension | Vertex 768 d | Before BS-07 |
| OD-005 | Reranker | Local cross-encoder | BS-08 |
| OD-006 | Chat customer identity | Demo persona + optional email OTP | Before BS-20 |
| OD-007 | Real mailbox for demo | Demo mailbox; Gmail adapter ready | Before demo |
| OD-008 | Reply language for Hinglish | English; mirror if Hinglish-only | BS-13 |
| OD-009 | Japanese support | EN only | After POC review |
| OD-010 | Shared staff auth with portal POC | Separate | Before BS-32 |
