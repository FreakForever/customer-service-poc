"""Rule layer: entities and exact intents (BS-12, LLD-6, FR-RT-01)."""

import json
import re
import time
from pathlib import Path

import pytest

from app.rules.context import RuleContext
from app.rules.entities import extract_entities
from app.rules.guardrails import check
from app.rules.intents import exact_intents

pytestmark = pytest.mark.req("FR-RT-01")
DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data"
CTX = RuleContext.from_files(DATA)
L = CTX.linker
QS = [json.loads(x) for x in (DATA / "eval" / "questions.jsonl").open()]
LABEL = {nid: n["label"] for nid, n in L.nodes.items()}
TYPES = {"ticket", "amc", "serial", "Model", "ErrorCode"}


def predicted(text):
    e = extract_entities(text, L)
    return ({("ticket", t) for t in e.ticket_ids} | {("amc", a) for a in e.amc_ids} | {("serial", s) for s in e.serials}
            | {(x.label, x.id) for x in e.items if x.label in ("Model", "ErrorCode")})


def gold(q):
    g = set()
    for k, v in (q.get("tool_args") or {}).items():
        g.add(("ticket" if k == "ticket_id" else "amc", v))
    for s in re.findall(r"\bMT-[A-Z0-9]+-\d{2}-\d{5}\b", q["question"]):   # serials are written out verbatim
        g.add(("serial", s))
        g.add(("Model", {"DHD150": "DHD-150"}.get(s.split("-")[1], s.split("-")[1])))
    path = [x for x in q.get("gold_kg_path", []) if x in LABEL]
    g |= {(LABEL[x], x) for x in path if LABEL[x] in ("Model", "ErrorCode")}
    if path and LABEL[path[0]] != "Model" and (m := re.search(r"\bon an? ([A-Z]{2,4}-[A-Z0-9]+)\b", q["question"])):
        g.add(("Model", m.group(1)))           # 5-hop items start the gold path at the error code
    return g


def test_entity_extraction_f1_on_eval_items_with_ids():
    items = [q for q in QS if q["type"] in ("transactional", "transactional_multi_hop", "multi_hop", "adversarial")
             and gold(q)]
    tp = fp = fn = 0
    for q in items:
        p, g = predicted(q["question"]), gold(q)
        tp, fp, fn = tp + len(p & g), fp + len(p - g), fn + len(g - p)
    f1 = 2 * tp / (2 * tp + fp + fn)
    assert len(items) >= 70 and f1 >= 0.95, (tp, fp, fn)


@pytest.mark.parametrize(("text", "want"), [
    ("status of sr-2026-00112", {"ticket_ids": ["SR-2026-00112"]}),
    ("Is AMC AMC-2025-0014 active?", {"amc_ids": ["AMC-2025-0014"]}),
    ("serial MT-GR200-24-00233 is down", {"serials": ["MT-GR200-24-00233"], "model": "GR-200"}),
    ("price of MT-DHD-ROTOR-R2 and MT-CHL-PUMP?", {"part_nos": ["MT-DHD-ROTOR-R2", "MT-CHL-PUMP"]}),
    ("my dhd 150 shows dew pt high", {"model": "DHD-150", "error_codes": ["DHD-E04"]}),
    ("MT-DHD150-23-00417 is showing DEW PT HIGH", {"model": "DHD-150", "error_codes": ["DHD-E04"]}),
])
def test_entity_patterns(text, want):
    e = extract_entities(text, L)
    for k, v in want.items():
        assert getattr(e, k) == v, (k, getattr(e, k))


def names(text):
    return [i.name for i in exact_intents(text, extract_entities(text, L))]


@pytest.mark.parametrize(("text", "want"), [
    ("hi", ["greeting"]), ("Hello there!", ["greeting"]), ("namaste", ["greeting"]), ("good morning team", ["greeting"]),
    ("thanks", ["thanks"]), ("Thank you so much!", ["thanks"]), ("shukriya", ["thanks"]),
    ("talk to a human", ["human"]), ("Can I speak with an agent please?", ["human"]), ("kisi se baat karni hai", ["human"]),
    ("status of SR-2026-00112", ["service.ticket_status"]), ("SR-2026-00105?", ["service.ticket_status"]),
    ("What's the status of ticket SR-2026-00105?", ["service.ticket_status"]),
    ("Engineer still not come for SR-2026-00105, please escalate", ["service.ticket_status"]),
    ("Is AMC AMC-2025-0001 active?", ["amc.status"]), ("start over", ["reset"]),
    ("Our AMC-2025-0014 expired, we want to renew it", []),          # renewal → router (amc.renewal), not status
    ("hi, my DHD-150 shows E04", []), ("what's the warranty on a new dryer?", []),
    ("Please create a ticket like SR-2026-00105 but for my second chiller which also trips daily", []),
])
def test_exact_intents(text, want):
    assert names(text) == want


def test_chat_script_turns():
    scripts = {s["id"]: s["turns"] for s in json.loads((DATA / "channels" / "chat_scripts.json").read_text())}
    assert names(scripts["CH-001"][0]) == ["greeting"]
    assert names(scripts["CH-003"][0]) == ["service.ticket_status"]
    assert names(scripts["CH-008"][0]) == ["human"]


def test_rule_layer_p95_under_10ms():
    texts = [q["question"] for q in QS] * 3
    times = []
    for t in texts:
        start = time.perf_counter()
        e = extract_entities(t, L)
        exact_intents(t, e)
        check(t, e, companies=CTX.companies)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    assert times[int(0.95 * len(times))] < 10
