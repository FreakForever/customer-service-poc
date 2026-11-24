"""Judge calibration (BS-18, FR-VF-02): agreement on eval/calibration/judge_50.json.   make eval-judge
Uses human_label when every item has one, else the constructed label (and says so: that doesn't satisfy FR-VF-02)."""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from app.config.settings import get_settings
from app.providers import get_llm
from app.retrieval.evidence import Evidence
from app.verify.judge import PASS, judge


async def main() -> int:
    s = get_settings()
    items = json.loads((ROOT / "eval/calibration/judge_50.json").read_text())
    human = all(i["human_label"] for i in items)
    rows, failed = [], []
    for i in items:
        ev = [Evidence(id="doc:x", kind="chunk", text=i["evidence"], source="calibration")]
        for attempt in range(4):
            try:
                j = await judge(i["question"], i["answer"], ev, get_llm(), model=s.model_strong, timeout_ms=30000)
            except Exception:  # noqa: BLE001 - free tier: 503/429
                await asyncio.sleep(10 * (attempt + 1))
                continue
            got = "supported" if j.faithfulness >= PASS else "unsupported"
            rows.append({"id": i["id"], "edit": i["edit"], "want": i["human_label"] if human else i["constructed_label"],
                         "got": got, "score": j.faithfulness, "claims": j.unsupported_claims})
            break
        else:
            failed.append(i["id"])
        await asyncio.sleep(4)
    agree = sum(r["got"] == r["want"] for r in rows) / max(1, len(rows))
    rep = {"labels": "human" if human else "constructed", "model": s.model_strong, "n": len(rows), "provider_failed": failed,
           "agreement": round(agree, 3),
           "misses_by_edit": dict(Counter(r["edit"] for r in rows if r["got"] != r["want"])),
           "false_accepts": sum(r["got"] == "supported" != r["want"] for r in rows),
           "false_rejects": sum(r["got"] == "unsupported" != r["want"] for r in rows),
           "misses": [r for r in rows if r["got"] != r["want"]]}
    rep["passed"] = agree >= 0.85 and not failed
    (ROOT / "eval/reports").mkdir(exist_ok=True)
    (ROOT / "eval/reports/judge.json").write_text(json.dumps({**rep, "rows": rows}, indent=2) + "\n")
    print(json.dumps(rep, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
