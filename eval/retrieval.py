"""Retrieval eval (BS-08; FR-RG-01, FR-RG-03, FR-RG-04, FR-RG-06) against the seeded dev database.

    make eval-retrieval        # = cd services/agent && uv run python ../../eval/retrieval.py

Configurations: {family/model filters from the question, no filters} × {cross-encoder rerank, no rerank}.
Metrics: doc-level Recall@5 and MRR@20 on single_hop (gold_docs), section-level hit@5/MRR (the gold error-code
chunk), FAQ + paraphrase doc-level Recall@5/MRR, the version_trap item, and filter leakage (FR-RG-03).
Gate (exit 1 otherwise): filtered + rerank single_hop Recall@5 >= 0.9, version_trap never cites v1,
no other-family manual chunks under a model filter. Report: eval/reports/retrieval.json.
"""

import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import get_settings
from app.providers import get_embedder
from app.retrieval.hybrid import Filters, retrieve
from app.retrieval.rerank import NoopReranker, get_reranker

QUESTIONS = ROOT / "mock-data" / "data" / "eval" / "questions.jsonl"
REPORT = ROOT / "eval" / "reports" / "retrieval.json"
GATE_RECALL = 0.9


class CachedEmbedder:
    """Embed each distinct query once across configurations."""

    def __init__(self, inner):
        self.inner, self.dim, self.cache = inner, inner.dim, {}

    async def embed(self, texts, *, kind):
        missing = [t for t in texts if (t, kind) not in self.cache]
        if missing:
            for t, v in zip(missing, await self.inner.embed(missing, kind=kind), strict=True):
                self.cache[(t, kind)] = v
        return [self.cache[(t, kind)] for t in texts]


def rr(ranks):
    return 1.0 / ranks[0] if ranks else 0.0


def rank_of(hits, pred):
    return [i for i, h in enumerate(hits, 1) if pred(h)]


async def main() -> int:
    s = get_settings()
    engine = create_async_engine(s.resolved_database_url())
    embedder = CachedEmbedder(get_embedder())
    rerankers = {"rerank": get_reranker(), "no_rerank": NoopReranker()}
    qs = [json.loads(line) for line in QUESTIONS.open(encoding="utf-8")]
    async with engine.connect() as conn:
        model_family = dict((await conn.execute(text("SELECT id, family FROM models"))).all())
    model_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, model_family), key=len, reverse=True)) + r")\b")

    def filters_for(q):
        m = model_re.search(q)
        return Filters(family=model_family[m.group(1)], model=m.group(1)) if m else Filters()

    report: dict = {"embed_model": s.embed_model, "reranker": rerankers["rerank"].name, "configs": {}}
    rerank_ms: list[int] = []
    single = [q for q in qs if q["type"] == "single_hop"]
    faqs = [q for q in qs if q["type"] in ("faq", "faq_paraphrase")]
    async with engine.connect() as conn:
        for fname in ("filtered", "unfiltered"):
            for rname, rer in rerankers.items():
                doc_hit5, doc_rr, sec_hit5, sec_rr, leaks = [], [], [], [], 0
                for q in single:
                    f = filters_for(q["question"]) if fname == "filtered" else Filters()
                    res = await retrieve(conn, embedder, q["question"], f, reranker=rer, k_ctx=20,
                                         rerank_timeout_ms=5000)
                    if rname == "rerank" and fname == "filtered":
                        rerank_ms.append(res.timings_ms.get("rerank", 0))
                    hits, gold = res.hits, set(q["gold_docs"])
                    d = rank_of(hits, lambda h, g=gold: h.doc_id in g)
                    doc_hit5.append(bool(d and d[0] <= 5))
                    doc_rr.append(rr(d))
                    code = re.search(r"error (\w+)", q["question"]).group(1)
                    fam = filters_for(q["question"]).family
                    gold_id = f"{fam}-{code}"
                    sec = rank_of(hits, lambda h, g=gold_id: g in h.metadata.get("error_codes", []))
                    sec_hit5.append(bool(sec and sec[0] <= 5))
                    sec_rr.append(rr(sec))
                    if fname == "filtered":
                        leaks += sum(1 for h in hits if h.doc_type == "manual" and h.family != f.family)
                faq_hit5, faq_rr = [], []
                for q in faqs:
                    res = await retrieve(conn, embedder, q["question"], Filters(), reranker=rer, k_ctx=20,
                                         rerank_timeout_ms=5000)
                    d = rank_of(res.hits, lambda h, g=set(q["gold_docs"]): h.doc_id in g)
                    faq_hit5.append(bool(d and d[0] <= 5))
                    faq_rr.append(rr(d))
                report["configs"][f"{fname}/{rname}"] = {
                    "single_hop_n": len(single),
                    "single_hop_recall@5": round(statistics.mean(doc_hit5), 3),
                    "single_hop_mrr": round(statistics.mean(doc_rr), 3),
                    "section_hit@5": round(statistics.mean(sec_hit5), 3),
                    "section_mrr": round(statistics.mean(sec_rr), 3),
                    "faq_n": len(faqs),
                    "faq_recall@5": round(statistics.mean(faq_hit5), 3),
                    "faq_mrr": round(statistics.mean(faq_rr), 3),
                    "other_family_manual_chunks": leaks if fname == "filtered" else None,
                }
        trap = next(q for q in qs if q["type"] == "version_trap")
        res = await retrieve(conn, embedder, trap["question"], Filters(), reranker=rerankers["rerank"],
                             rerank_timeout_ms=5000)
        cited = [h.doc_id for h in res.hits]
        report["version_trap"] = {"question": trap["question"], "top_docs": cited,
                                  "include_superseded": res.include_superseded,
                                  "ok": "DOC-POL-WARRANTY-V2" in cited and "DOC-POL-WARRANTY-V1" not in cited}
        past = await retrieve(conn, embedder, "What was the old warranty policy before 2025?", Filters(),
                              reranker=rerankers["rerank"], rerank_timeout_ms=5000)
        report["past_policy_cue"] = {"include_superseded": past.include_superseded,
                                     "v1_retrieved": any(h.doc_id == "DOC-POL-WARRANTY-V1" for h in past.hits)}
    await engine.dispose()
    rerank_ms.sort()
    report["rerank_latency_ms"] = {"p50": rerank_ms[len(rerank_ms) // 2],
                                   "p95": rerank_ms[max(0, int(0.95 * len(rerank_ms)) - 1)], "pairs": 20}
    main_cfg = report["configs"]["filtered/rerank"]
    report["gate"] = {
        "recall@5>=0.9": main_cfg["single_hop_recall@5"] >= GATE_RECALL,
        "version_trap": report["version_trap"]["ok"],
        "no_family_leak": main_cfg["other_family_manual_chunks"] == 0,
    }
    report["passed"] = all(report["gate"].values())
    report["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
