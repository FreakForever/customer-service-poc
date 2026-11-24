"""Live T2 eval (BS-15, FR-RG-05): real retrieval + model on the seeded dev database.   make eval-t2
Gate: every requires_upload item abstains (run before any upload); answerable items are answered with a citation to
a gold document >= 0.8. Provider failures are retried and counted separately, never scored as abstentions."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from sqlalchemy.ext.asyncio import create_async_engine

from app.agents.rag_answerer import answer
from app.config.settings import get_settings
from app.providers import get_embedder, get_llm
from app.retrieval.graphrag import chunk_evidence
from app.retrieval.hybrid import Filters, retrieve
from app.retrieval.rerank import get_reranker
from app.rules.context import RuleContext
from app.rules.entities import extract_entities

DATA = ROOT / "mock-data" / "data"
REPORT = ROOT / "eval" / "reports" / "t2.json"


async def main() -> int:
    s, linker = get_settings(), RuleContext.from_files(DATA).linker
    engine = create_async_engine(s.resolved_database_url())
    qs = [json.loads(x) for x in (DATA / "eval" / "questions.jsonl").open()]
    by = {t: [q for q in qs if q["type"] == t] for t in ("requires_upload", "faq", "single_hop", "version_trap")}
    items = by["requires_upload"] + by["faq"][::3] + by["single_hop"][::2] + by["version_trap"]
    rows = []
    for q in items:
        ents = extract_entities(q["question"], linker)
        async with engine.connect() as conn:
            res = await retrieve(conn, get_embedder(), q["question"], Filters(family=ents.family, model=ents.model),
                                 reranker=get_reranker(), rerank_timeout_ms=5000)
        ev = [chunk_evidence(h) for h in res.hits]
        row = {"id": q["id"], "type": q["type"], "question": q["question"], "error": None}
        for attempt in range(4):
            try:
                out = await answer(q["question"], ev, get_llm(), model=s.model_fast, timeout_ms=30000)
            except Exception as exc:  # noqa: BLE001 - free tier: 503/429/timeouts
                row["error"] = type(exc).__name__
                await asyncio.sleep(10 * (attempt + 1))
                continue
            cited = {c.doc_id for c in out.draft.citations}
            row.update(error=None, abstained=out.draft.abstained, text=out.draft.text, cited=sorted(cited),
                       gold_cited=bool(cited & set(q.get("gold_docs") or [])),
                       raw=out.completion.text if out.completion else "",
                       ms=out.completion.latency_ms if out.completion else 0,
                       model=out.completion.model if out.completion else "")
            break
        rows.append(row)
        await asyncio.sleep(4)
    await engine.dispose()
    failed = [r["id"] for r in rows if r["error"]]
    up = [r for r in rows if r["type"] == "requires_upload" and not r["error"]]
    an = [r for r in rows if r["type"] != "requires_upload" and not r["error"]]
    ms = sorted(r["ms"] for r in rows if not r["error"])
    rep = {"n": len(rows), "provider_failed": failed, "upload_abstained": f"{sum(r['abstained'] for r in up)}/{len(up)}",
           "answerable": len(an), "answered_gold_cited": round(sum(r["gold_cited"] for r in an) / max(1, len(an)), 3),
           "wrongly_abstained": [r["id"] for r in an if r["abstained"]],
           "latency_ms": {"p50": ms[len(ms) // 2], "max": ms[-1]} if ms else {},
           "models": sorted({r["model"] for r in rows if not r["error"]})}
    rep["passed"] = len(up) == 4 and all(r["abstained"] for r in up) and not failed and rep["answered_gold_cited"] >= 0.8
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({**rep, "rows": rows}, indent=2) + "\n")
    print(json.dumps(rep, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
