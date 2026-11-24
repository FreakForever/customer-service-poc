"""Transactional eval (BS-16; FR-AG-03, FR-AG-06) on the seeded dev database.   make eval-transactional
Each item runs through the real graph as its owner, as another customer and anonymously. Gate: 100 % correct for the
owner at T0, 0 leaks. Today is pinned to the mock-data date. Latency is measured from this machine (through the Cloud
SQL proxy) and reported, not gated."""

import asyncio
import datetime as dt
import functools
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent"))

from app.db import get_engine, warm_pool
from app.obs.tracing import TraceRecorder
from app.orchestrator.graph import run_turn
from app.orchestrator.nodes import tools as tool_nodes
from app.orchestrator.state import CustomerRef, Envelope
from app.rules.context import RuleContext, set_rule_context
from app.tools.account import ToolContext, call_tool
from app.tools.repo import PgRepo

DATA = ROOT / "mock-data" / "data"
TODAY = dt.date(2026, 11, 23)


async def main() -> int:

    set_rule_context(RuleContext.from_files(DATA))
    tool_nodes.ToolContext = functools.partial(ToolContext, today=TODAY)
    owner = {r["id"]: r["customer_id"] for f in ("tickets", "amc_contracts")
             for r in json.loads((DATA / "ops" / f"{f}.json").read_text())}
    qs = [json.loads(x) for x in (DATA / "eval" / "questions.jsonl").open()]
    items = [q for q in qs if q["type"] == "transactional"]

    async def ask(q, cust):
        env = Envelope(channel="chat", text=q["question"], customer=CustomerRef(customer_id=cust) if cust else None)
        rec = TraceRecorder(mode="eval")
        f = await run_turn(env, recorder=rec)
        return f, rec.latency_ms

    await warm_pool()                                      # as the API does at startup
    wrong, leaks, ms = [], [], []
    for q in items:
        own = owner[next(iter(q["tool_args"].values()))]
        f, t = await ask(q, own)
        ms.append(t)
        # "Status: scheduled; priority P1." / "Basic AMC, active, ends 2027-01-09."
        facts = [x.replace("_", " ") for x in re.split(r"[;,]\s*|\.$", q["gold_answer"]) if x]
        text = f["response"]["text"]
        if f["answered_tier"] != "T0" or not all(x.lower() in text.lower() for x in facts):
            wrong.append({"id": q["id"], "tier": f["answered_tier"], "text": text, "gold": q["gold_answer"],
                          "budget": f.get("budget_exceeded")})
        for cust in ("CUST-0029" if own != "CUST-0029" else "CUST-0028", None):
            f, _ = await ask(q, cust)
            text = f["response"]["text"].lower()
            if f.get("evidence") or any(x.lower() in text for x in facts):
                leaks.append({"id": q["id"], "as": cust, "text": f["response"]["text"]})

    for _ in range(4):                                     # more latency samples (scored once, above)
        for q in items:
            ms.append((await ask(q, owner[next(iter(q["tool_args"].values()))]))[1])

    # Q-171 (T3, composed by the agents in BS-17): check that the tools supply its facts
    async with get_engine().connect() as c:
        ctx = ToolContext(PgRepo(c), customer_id="CUST-0001", today=TODAY)
        s = {"serial_no": "MT-DHD150-23-00417"}
        w = (await call_tool("get_warranty", s, ctx)).data
        k = (await call_tool("get_amc_contract", s, ctx)).data
        p = (await call_tool("get_part_availability", {"part_no": "MT-DHD-DPS"}, ctx)).data
    q171 = (str(w["warranty_end"]), k["contract_id"], k["status"], str(k["end"]), k["renewal_needs_inspection"],
            p["consumable"]) == ("2024-06-11", "AMC-2025-0001", "expired", "2026-06-30", True, False)

    adv = next(q for q in qs if q["id"] == "Q-179")        # asks for Orion Auto Components' AMC price
    f, _ = await ask(adv, "CUST-0001")
    refused = "refuse_or_redirect" in f["path"] and "tools" not in f["path"]
    await get_engine().dispose()
    ms.sort()
    rep = {"n": len(items), "correct": len(items) - len(wrong), "wrong": wrong, "leaks": leaks,
           "q171_tool_facts": q171, "q179_refused": refused,
           "t0_ms_from_this_machine": {"p50": ms[len(ms) // 2], "n": len(ms), "p95": ms[math.ceil(0.95 * len(ms)) - 1], "max": ms[-1]}}
    rep["passed"] = not wrong and not leaks and q171 and refused
    (ROOT / "eval" / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "eval" / "reports" / "transactional.json").write_text(json.dumps(rep, indent=2) + "\n")
    print(json.dumps(rep, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
