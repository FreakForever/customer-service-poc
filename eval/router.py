"""Router eval (BS-13; FR-RT-02, FR-RT-03, FR-CH-07): rules + guardrails + kNN + LLM fallback through the real
graph front, on the eval questions, the sample emails and the chat-script turns.

    make eval-router     # = cd services/agent && uv run python ../../eval/router.py
Gold intents are derived from each item's type (eval items carry no intent labels); a prediction is correct when
the routed label is in the item's gold set. Gate: accuracy >= 0.90, EM-001 yields two intents, CH-007 and every
Hinglish FAQ paraphrase routed correctly. Report: eval/reports/router.json.
"""

import asyncio
import json

import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from app.obs.tracing import TraceRecorder  # noqa: E402
from app.orchestrator.graph import run_turn  # noqa: E402
from app.orchestrator.state import Envelope  # noqa: E402
from app.rules.context import RuleContext, set_rule_context  # noqa: E402
from app.router.language import detect_language  # noqa: E402

DATA = ROOT / "mock-data" / "data"
REPORT = ROOT / "eval" / "reports" / "router.json"
TROUBLE = {"troubleshoot.error_code", "troubleshoot.symptom"}
FAQ_ALT = {"FAQ-007": {"amc.renewal"}, "FAQ-011": {"complaint"}, "FAQ-028": {"service.ticket_status"},
           "FAQ-033": {"sales.quote"}, "FAQ-023": {"safety"}, "FAQ-008": {"amc.status"}}


def faq_index():
    idx = {}
    for f in json.loads((DATA / "kb" / "faqs.json").read_text()):
        for t in [f["question"], *f["paraphrases"]]:
            idx[t.lower()] = f
    return idx


def gold_for(q, faqs):
    t, text = q["type"], q["question"]
    if t in ("faq", "faq_paraphrase"):
        f = faqs[text.lower()]
        return {f"faq.{f['category']}"} | FAQ_ALT.get(f["id"], set())
    if t in ("single_hop", "aggregation", "comparison"):
        return TROUBLE
    if t == "multi_hop":
        if "covered" in text:
            return TROUBLE | {"parts.availability", "amc.status", "faq.amc"}
        if "spare part" in text:
            return TROUBLE | {"parts.availability"}
        return TROUBLE
    if t == "version_trap":
        return {"faq.warranty"}
    if t == "transactional":
        return {"service.ticket_status"} if q["tool"] == "get_ticket_status" else {"amc.status"}
    if t == "transactional_multi_hop":
        return {"warranty.status", "amc.status", "asset.info"}
    if t == "safety":
        return {"safety"}
    if t == "out_of_scope":
        return {"oos"}
    if t == "adversarial":
        return {"refuse"}
    if t == "requires_upload":
        return {"Q-180": TROUBLE, "Q-181": {"parts.availability", "parts.compatibility"}, "Q-182": TROUBLE,
                "Q-183": {"faq.product"}}[q["id"]]
    raise KeyError(t)


EMAIL_GOLD = {"EM-004": "faq.*", "EM-007": {"refuse"}}
CHAT_GOLD = {
    ("CH-001", 0): {"greeting"}, ("CH-001", 1): {"faq.warranty"}, ("CH-002", 0): TROUBLE,
    ("CH-002", 2): {"parts.availability"} | TROUBLE, ("CH-003", 0): {"service.ticket_status"},
    ("CH-004", 0): {"complaint", "service.create_ticket"}, ("CH-004", 1): TROUBLE | {"complaint"},
    ("CH-005", 0): TROUBLE, ("CH-006", 0): {"oos"}, ("CH-007", 0): {"faq.process"}, ("CH-008", 0): {"human"},
    ("CH-009", 0): TROUBLE | {"parts.compatibility", "parts.availability"}, ("CH-010", 0): {"refuse"},
}


def label(final):
    flags = final.get("flags") or set()
    if "safety" in flags:
        return "safety", "guardrail"
    if flags & {"injection", "exfiltration"}:
        return "refuse", "guardrail"
    if "oos" in flags:
        return "oos", "guardrail"
    intents = final.get("intents") or []
    if not intents:
        return "none", "none"
    top = max(intents, key=lambda i: i.confidence)
    return top.name, top.source


def ok(pred, gold):
    return pred.startswith("faq.") if gold == "faq.*" else pred in gold


async def run(text, channel="chat"):
    rec = TraceRecorder(mode="eval")
    final = await run_turn(Envelope(channel=channel, text=text), recorder=rec)
    spans = {s.node: s for s in rec.spans}
    return final, spans


async def main() -> int:
    set_rule_context(RuleContext.from_files(DATA))
    faqs = faq_index()
    items = []
    for q in map(json.loads, (DATA / "eval" / "questions.jsonl").open()):
        items.append({"id": q["id"], "kind": q["type"], "text": q["question"], "gold": gold_for(q, faqs), "channel": "chat"})
    seen = {i["text"].lower() for i in items}
    for f in json.loads((DATA / "kb" / "faqs.json").read_text()):      # FR-CH-07: every Hinglish FAQ paraphrase
        for t in f["paraphrases"]:
            if detect_language(t) == "hinglish" and t.lower() not in seen:
                items.append({"id": f"{f['id']}~{t[:24]}", "kind": "hinglish_faq", "text": t, "channel": "chat",
                              "gold": {f"faq.{f['category']}"} | FAQ_ALT.get(f["id"], set())})
    for e in json.loads((DATA / "channels" / "emails.json").read_text()):
        gold = EMAIL_GOLD.get(e["id"]) or set(e["expect"]["intents"])
        items.append({"id": e["id"], "kind": "email", "text": f"{e['subject']}\n{e['body']}", "gold": gold,
                      "channel": "email", "all": set(e["expect"]["intents"])})
    for c in json.loads((DATA / "channels" / "chat_scripts.json").read_text()):
        for i, t in enumerate(c["turns"]):
            if (c["id"], i) in CHAT_GOLD:
                items.append({"id": f"{c['id']}#{i}", "kind": "chat", "text": t, "gold": CHAT_GOLD[(c["id"], i)],
                              "channel": "chat"})
    # build the kNN index once before timing anything (the API does this at startup)
    from app.providers import get_embedder
    from app.router.knn import get_knn

    await get_knn().build(get_embedder())

    rows, sources, by_kind = [], Counter(), {}
    llm_ms, knn_ms = [], []
    for it in items:
        t0 = time.perf_counter()
        final, spans = await run(it["text"], it["channel"])
        pred, src = label(final)
        good = ok(pred, it["gold"])
        sources[src] += 1
        by_kind.setdefault(it["kind"], []).append(good)
        if "classify" in spans:
            knn_ms.append(spans["classify"].duration_ms)
        if "llm_router" in spans:
            llm_ms.append(spans["llm_router"].duration_ms)
        rows.append({"id": it["id"], "kind": it["kind"], "pred": pred, "source": src, "ok": good,
                     "intents": [i.name for i in final.get("intents") or []],
                     "gold": sorted(it["gold"]) if isinstance(it["gold"], set) else it["gold"],
                     "language": final.get("language"), "ms": int((time.perf_counter() - t0) * 1000),
                     "router_status": spans["llm_router"].status if "llm_router" in spans else None,
                     "router_fallback": spans["llm_router"].attrs.get("fallback") if "llm_router" in spans else None})
    acc = statistics.mean(r["ok"] for r in rows)
    em1 = next(r for r in rows if r["id"] == "EM-001")
    hinglish = [r for r in rows if detect_language(next(i["text"] for i in items if i["id"] == r["id"])) == "hinglish"]
    report = {
        "n": len(rows), "accuracy": round(acc, 3),
        "accuracy_by_kind": {k: round(statistics.mean(v), 3) for k, v in sorted(by_kind.items())},
        "decided_by": dict(sources),
        "fast_path_share": round(sum(v for k, v in sources.items() if k in ("rule", "knn", "guardrail")) / len(rows), 3),
        "em001_intents": em1["intents"],
        "hinglish": {"n": len(hinglish), "correct": sum(r["ok"] for r in hinglish),
                     "ids": [r["id"] for r in hinglish if not r["ok"]]},
        "latency_ms": {"classify_p50": statistics.median(knn_ms) if knn_ms else None,
                       "llm_router_p50": statistics.median(llm_ms) if llm_ms else None,
                       "llm_router_p95": sorted(llm_ms)[int(0.95 * len(llm_ms)) - 1] if llm_ms else None,
                       "llm_router_fallbacks": sum(1 for r in rows if r["router_fallback"])},
        "errors": [r for r in rows if not r["ok"]],
    }
    report["gate"] = {"accuracy>=0.90": acc >= 0.90,
                      "em001_two_intents": {"troubleshoot.error_code", "amc.status"} <= set(em1["intents"]),
                      "ch007_ok": next(r for r in rows if r["id"] == "CH-007#0")["ok"],
                      "hinglish_all_ok": all(r["ok"] for r in hinglish)}
    report["passed"] = all(report["gate"].values())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, default=list) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "errors"}, indent=2, default=list))
    for e in report["errors"]:
        print("MISS", e["id"], e["pred"], e["source"], e["gold"], e["router_fallback"] or "")
    return 0 if report["passed"] else 1


if __name__ == "__main__":

    raise SystemExit(asyncio.run(main()))
