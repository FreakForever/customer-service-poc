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
| D-009 | 2026-11-23 | accepted | Mailpit in dev behind InboundMailAdapter | Real email loop without accounts | Gmail API first | FR-EM-01, LLD-12 |
| D-010 | 2026-11-23 | accepted | SSE for chat and console live updates | Cloud Run friendly, simple | WebSockets | FR-CH-01, LLD-12 |
| D-011 | 2026-11-23 | accepted | Postgres job table + worker | Fewer moving parts | Pub/Sub, Celery | LLD-13 |
| D-012 | 2026-11-23 | accepted | Deterministic synthetic data generator + eval set | Reproducible demos/benchmarks | Hand fixtures | DR-01/02 |
| D-013 | 2026-11-23 | accepted | Provider-agnostic LLM layer; fast vs strong model per node | Swap after bake-off | Single vendor | NFR-REL-01, LLD-2 |
| D-014 | 2026-11-23 | accepted | Docs as code: JSON registry + build steps → HTML; CI trace gate | Traceability without manual sync | Hand-edited HTML | NFR-MNT-01 |

## Open decisions

| ID | Question | Default until decided | Decide by |
|---|---|---|---|
| OD-001 | Matsui brand palette/logo/photography | Placeholder tokens navy #0A2540 / blue #0057A8 | Before BS-22 |
| OD-002 | LLM provider and models | Vertex AI, configurable | BS-30 |
| OD-003 | Neo4j hosting on GCP | Compute Engine e2-medium | Before BS-32 |
| OD-004 | Embedding model / dimension | Vertex 768 d | Before BS-07 |
| OD-005 | Reranker | Local cross-encoder | BS-08 |
| OD-006 | Chat customer identity | Demo persona + optional email OTP | Before BS-20 |
| OD-007 | Real mailbox for demo | Mailpit only | Before demo |
| OD-008 | Reply language for Hinglish | English; mirror if Hinglish-only | BS-13 |
| OD-009 | Japanese support | EN only | After POC review |
| OD-010 | Shared staff auth with portal POC | Separate | Before BS-32 |
