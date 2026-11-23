"""FAQ cache eval (BS-14; FR-CA-02) against the seeded dev database with live embeddings.

    make eval-cache     # = cd services/agent && uv run python ../../eval/cache.py
Official metrics (gate): faq_paraphrase items served from the cache (exact, then semantic at tau 0.90) with the
right FAQ >= 0.85; false-hit rate on non-FAQ eval items < 0.03 (account-id guard on, no intent gate — stricter
than production, where only faq.* intents reach the cache). Because the paraphrases are themselves seeded
variants, a leave-one-out score (semantic only, the identical variant excluded) and a threshold sweep are
reported too. Report: eval/reports/cache.json.
"""

import asyncio
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from sqlalchemy.ext.asyncio import create_async_engine

from app.cache.store import exact_lookup, semantic_lookup
from app.config.settings import get_settings
from app.providers import get_embedder
from app.rules.context import RuleContext
from app.rules.entities import extract_entities

DATA = ROOT / "mock-data" / "data"
REPORT = ROOT / "eval" / "reports" / "cache.json"
TAU = 0.90
SWEEP = (0.80, 0.85, 0.88, 0.90, 0.92, 0.94)


async def main() -> int:
    s = get_settings()
    engine = create_async_engine(s.resolved_database_url())
    emb = get_embedder()
    linker = RuleContext.from_files(DATA).linker
    faq_of = {}
    for f in json.loads((DATA / "kb" / "faqs.json").read_text()):
        for t in [f["question"], *f["paraphrases"]]:
            faq_of[t.lower()] = f["id"]
    qs = [json.loads(x) for x in (DATA / "eval" / "questions.jsonl").open()]
    para = [q for q in qs if q["type"] == "faq_paraphrase"]
    canon = [q for q in qs if q["type"] == "faq"]
    non_faq = [q for q in qs if q["type"] not in ("faq", "faq_paraphrase", "version_trap")]
    texts = [q["question"] for q in para + canon + non_faq]
    vecs = dict(zip(texts, await emb.embed(texts, kind="query"), strict=True))

    async def official(q):
        ents = extract_entities(q["question"], linker)
        async with engine.begin() as c:
            hit = await exact_lookup(c, q["question"]) if not ents.has_account_ids else None
            if hit is None:
                hit = await semantic_lookup(c, vecs[q["question"]], TAU, has_account_ids=ents.has_account_ids)
        return hit

    rows = {"paraphrase": [], "canonical": [], "non_faq": []}
    for name, items in (("paraphrase", para), ("canonical", canon)):
        for q in items:
            hit = await official(q)
            rows[name].append({"id": q["id"], "hit": bool(hit), "layer": hit.layer if hit else None,
                               "correct": bool(hit and hit.faq_id == faq_of[q["question"].lower()])})
    for q in non_faq:
        hit = await official(q)
        rows["non_faq"].append({"id": q["id"], "type": q["type"], "hit": bool(hit),
                                "faq": hit.faq_id if hit else None, "sim": hit.similarity if hit else None,
                                "question": q["question"]})

    loo = {t: [] for t in SWEEP}
    fh = {t: [] for t in SWEEP}
    async with engine.connect() as c:
        for q in para:
            for t in SWEEP:
                hit = await semantic_lookup(c, vecs[q["question"]], t, exclude_texts={q["question"].lower()})
                loo[t].append(bool(hit and hit.faq_id == faq_of[q["question"].lower()]))
        for q in non_faq:
            ents = extract_entities(q["question"], linker)
            for t in SWEEP:
                hit = await semantic_lookup(c, vecs[q["question"]], t, has_account_ids=ents.has_account_ids)
                fh[t].append(bool(hit))
    await engine.dispose()

    para_rate = statistics.mean(r["correct"] for r in rows["paraphrase"])
    false_rate = statistics.mean(r["hit"] for r in rows["non_faq"])
    report = {
        "tau": TAU, "embed_model": s.embed_model,
        "paraphrase": {"n": len(para), "served_correct": round(para_rate, 3),
                       "by_layer": {lay: sum(1 for r in rows["paraphrase"] if r["layer"] == lay) for lay in ("exact", "semantic")}},
        "canonical": {"n": len(canon), "served_correct": round(statistics.mean(r["correct"] for r in rows["canonical"]), 3)},
        "non_faq": {"n": len(non_faq), "false_hit_rate": round(false_rate, 3),
                    "false_hits": [r for r in rows["non_faq"] if r["hit"]]},
        "leave_one_out_semantic": {str(t): round(statistics.mean(v), 3) for t, v in loo.items()},
        "false_hit_sweep": {str(t): round(statistics.mean(v), 3) for t, v in fh.items()},
    }
    report["gate"] = {"paraphrase>=0.85": para_rate >= 0.85, "false_hit<0.03": false_rate < 0.03}
    report["passed"] = all(report["gate"].values())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
