## 1. Purpose and vision {#REQ-1}

Matsui Technologies India Limited sells auxiliary equipment for plastic processing: mold temperature controllers, dehumidifying and hot air dryers, chillers, mold dehumidifiers, conveying systems and recycling granulators. Its customers are B2B plants (automotive, packaging, medical, appliances) whose questions fall into four groups:

| Query group | Example | Nature | Target tier |
|---|---|---|---|
| Static FAQ | "What is the warranty period?" | Repeated, public, stable | T1 cache |
| Transactional | "Status of SR-2026-00105?", "Is AMC-2025-0014 active?" | Structured lookup / form | T0 rules + tools |
| Troubleshooting | "DHD-150 shows DEW PT HIGH, filter already cleaned" | Multi-hop reasoning over product knowledge | T3 KG-RAG agents |
| Sales / parts | "Need blade set for GR-400 in Ludhiana — price and ETA?" | Lookup + light reasoning | T0/T3 |

**Vision:** a Matsui-branded, Zendesk-style help experience where customers get correct, cited answers by **email or chat** in seconds, routine requests never touch a large model, and anything uncertain or unsafe reaches a human with full context. Voice comes later and reuses the same core.

## 2. Stakeholders and personas {#REQ-2}

| Persona | Goals | Touchpoints |
|---|---|---|
| Plant maintenance head (customer) | Fix a stopped machine fast; know what's covered | Chat widget, email, help centre |
| Purchase / production manager (customer) | Quotes, spare parts, AMC renewal | Email, contact form |
| Matsui service agent | Handle escalations, review email drafts, take over chats | Agent console |
| Knowledge admin | Keep manuals, bulletins, FAQs and error codes current | Knowledge admin |
| Matsui leadership (demo audience) | See latency, deflection, accuracy and safety | Analytics, live demo |

## 3. Scope {#REQ-3}

**In scope (this POC):** chat and email channels; hybrid rule + AI orchestration; FAQ caching; RAG and KG-RAG over synthetic Matsui knowledge; verification and handoff; agent console; knowledge admin with runtime upload; analytics; local Docker stack and Google Cloud deployment.

**Out of scope (this POC):** voice channel (architecture is voice-ready, see BR-05); payments; real ERP/CRM integration (mocked by Postgres seed data); parsing attachments with AI; Japanese UI; production SLAs.

**Assumptions:** all data is synthetic until Matsui provides real content; Google Cloud is the target platform (same as the document-portal POC); one developer builds with Claude Code using the build steps in the LLD.

## 4. Requirement registry {#REQ-4}

The table below renders `docs/requirements-registry.json`. IDs are permanent and traceable: each requirement links to HLD/LLD sections, to the build steps that implement it (generated from `docs/build-steps.json`) and to its test case id. Use the filters to slice by type, area, priority or status. Deep links work: `Requirements.html#FR-KG-03`.

<div id="registry-app"></div>

## 5. Traceability matrix {#REQ-5}

Each dot means "build step implements requirement". Business requirements trace through their children. `python tools/sync_docs.py --check` fails CI if any functional, non-functional or data requirement has no implementing step.

<div id="trace-matrix"></div>

## 6. Glossary {#REQ-6}

| Term | Meaning |
|---|---|
| T0–T4 | Answer tiers: T0 rules/tools, T1 FAQ cache, T2 single-call RAG, T3 agentic multi-hop, T4 human |
| KG-RAG / GraphRAG | Retrieval that queries the knowledge graph (paths between models, error codes, causes, parts) and combines them with document chunks |
| Grounding / groundedness | Degree to which every claim in an answer is supported by retrieved evidence |
| Handoff | Transfer of a conversation to a human agent (chat) or to the review queue (email) |
| AMC | Annual maintenance contract (Basic, Comprehensive, Platinum in the demo data) |
| SSE | Server-sent events, used to stream chat tokens |
