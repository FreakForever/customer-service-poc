# Mock data — Matsui customer-service POC

**All data is synthetic.** Product categories follow the public range on matsuiindia.in; every model name, part number, price, error code, policy, customer and ticket is invented for the demo. Contact numbers are placeholders. Replace with real data only after Matsui provides it.

```bash
python mock-data/generate.py          # deterministic (seed 42, "today" = 2026-11-23)
python mock-data/kg/load_kg.py --wipe # load KG into Neo4j (needs NEO4J_* env)
```

## What's inside (`data/`)

| Folder | Used by | Contents |
|---|---|---|
| `catalog/` | KG seed, tools, manuals | 7 families, 23 models, 24 components, 34 causes, 44 error codes, 75 resolutions, 50 spare parts with stock per warehouse |
| `kb/docs/*.md` + `kb/documents.json` | RAG ingestion | 8 policy/guide docs (incl. a **superseded** warranty v1 for version-aware retrieval) + 7 generated product manuals with troubleshooting tables |
| `kb/faqs.json` | FAQ semantic cache (tier T1) | 35 FAQs, each with paraphrases (some Hinglish) |
| `ops/` | Transactional tools (tier T0) | 30 synthetic customers, 62 assets (serials), 18 AMC contracts, 40 service tickets, 20 parts orders, 11 service centres, 4 warehouses |
| `kg/nodes.jsonl`, `kg/edges.jsonl` | KG-RAG (tier T3) | ~340 nodes / ~1100 edges: Model→ErrorCode→Cause→Resolution→SparePart→Warehouse, AMCPlan→COVERS_PART, Document→SUPERSEDES |
| `eval/questions.jsonl` | Benchmark: RAG vs KG-RAG, tier routing, guardrails | 183 questions typed faq / paraphrase / single-hop / multi-hop (2–5 hops) / aggregation / version-trap / transactional / safety / out-of-scope / adversarial / requires-upload, with gold answers, gold docs and gold KG paths |
| `channels/` | E2E tests, demo scripts | 10 inbound emails (multi-question, angry escalation, prompt injection, safety) and 10 chat scripts |

## Fixtures the demo relies on
- `MT-DHD150-23-00417` (Aarav Polymers, Pune): out of warranty, Comprehensive AMC expired → good "is this covered?" case.
- `SR-2026-00105` (Orion Auto Components): P1 chiller HP TRIP, visit missed → escalation/handoff demo.
- `SR-2026-00112` (Indus Polymers): CCS-8 awaiting parts.
- `AMC-2025-0014` (Navkar Precision Plastics): lapsed 20 days ago → in 30-day grace window.

## Runtime upload demo (`upload-samples/`)
See `upload-samples/README.md`. Four eval questions (`type: requires_upload`) must be answered with "no information" before upload and correctly after — a direct hallucination and freshness check.

## KG schema (labels → relationships)
```
(Model)-[:BELONGS_TO]->(ProductFamily)-[:HAS_COMPONENT]->(Component)
(Model)-[:CAN_RAISE]->(ErrorCode)-[:CAUSED_BY {likelihood}]->(Cause)-[:AFFECTS]->(Component)
(Cause)-[:RESOLVED_BY {family}]->(Resolution)-[:REQUIRES_PART]->(SparePart)-[:FITS]->(Model)
(SparePart)-[:STOCKED_AT {qty}]->(Warehouse)<-[:SUPPLIED_BY]-(ServiceCentre)-[:COVERS]->(City)
(AMCPlan)-[:COVERS_PART]->(SparePart); (AMCPlan)-[:DESCRIBED_IN]->(Document)
(ErrorCode)-[:DOCUMENTED_IN]->(Document); (Document)-[:SUPERSEDES]->(Document); (ProductFamily)-[:USED_IN]->(Industry)
```
Note: `Cause -RESOLVED_BY-> Resolution` is family-specific (filter on `r.family`).
