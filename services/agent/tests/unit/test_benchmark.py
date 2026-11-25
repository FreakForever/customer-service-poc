"""Benchmark scoring (BS-29; FR-KG-06, NFR-QUAL-01..03) and audit sampling (FR-VF-05). No I/O."""

import importlib.util
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.orchestrator.policies import get_policy
from app.orchestrator.state import Citation, Draft
from app.retrieval.evidence import Evidence
from app.verify.audit import wanted

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("bench_metrics", ROOT / "eval" / "metrics.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
QS = [json.loads(x) for x in (ROOT / "mock-data" / "data" / "eval" / "questions.jsonl").open()]


def row(**kw):
    return {"mode": "hybrid", "id": "Q", "type": "multi_hop", "phase": "", "text": "x", "path": [], "priority": None, "abstained": False, "error": None,
            "correctness": 1.0, "groundedness": None, "latency_ms": 100, "first_token_ms": 100, "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.001,
            "tier_ok": None, "intent_ok": None, "citations_ok": None, **kw}


def q(t, **kw):
    return {"type": t, "gold_answer": "Gold text.", "expected_tier": "T3", **kw}


@pytest.mark.req("DR-02")
def test_the_question_set_is_complete():
    types = {x["type"] for x in QS}
    assert {"faq", "faq_paraphrase", "single_hop", "multi_hop", "aggregation", "version_trap", "transactional", "safety", "out_of_scope", "adversarial",
            "requires_upload"} <= types
    assert len({x["id"] for x in QS}) == len(QS) and all(x["gold_answer"] and x["expected_tier"] for x in QS)


@pytest.mark.parametrize(("item", "r", "want"), [
    (q("faq"), row(text="Gold text. [1]"), 1.0), (q("faq"), row(text="Something else"), None), (q("multi_hop"), row(abstained=True), 0.0),
    (q("requires_upload"), row(abstained=True, phase="before"), 1.0), (q("requires_upload"), row(abstained=True, phase="after"), 0.0),
    (q("requires_upload"), row(phase="before"), None), (q("multi_hop"), row(), None),
    (q("transactional", gold_answer="Status: awaiting_parts; priority P2."), row(text="SR-1: Status: awaiting parts. Priority P2"), 1.0),
    (q("transactional", gold_answer="Status: scheduled; priority P1."), row(text="Status: scheduled, priority P2"), 0.0),
    (q("adversarial"), row(path=["refuse_or_redirect"]), 1.0), (q("adversarial"), row(path=["rag_answer"], abstained=True), 0.0),
    (q("adversarial"), row(mode="rag", abstained=True), None), (q("safety"), row(path=["safety_handoff"], priority="P1"), 1.0),
    (q("safety"), row(path=["safety_handoff"], priority="P2"), 0.0)])
def test_correctness_decided_without_a_model(item, r, want):
    assert m.decide(item, r) == want


def test_tier_and_citations():
    assert m.tier_ok(q("safety", expected_tier="T0"), "T4") and not m.tier_ok(q("faq", expected_tier="T1"), "T4")
    v2, v1 = "DOC-V2", "DOC-V1"
    trap = q("version_trap", gold_docs=[v2], must_not_cite=[v1])
    assert (m.citations_ok(trap, [v2]), m.citations_ok(trap, [v2, v1]), m.citations_ok(trap, []), m.citations_ok(q("faq"), [])) == (True, False, False, None)


@pytest.mark.req("FR-KG-06")
def test_summary_leaves_out_provider_failures_and_second_asks():
    rows = [row(id="A", correctness=1.0, groundedness=0.8, latency_ms=100), row(id="B", correctness=0.5, groundedness=1.0, latency_ms=300, cost_usd=None),
            row(id="C", error="ProviderError", correctness=None), row(id="D", type="requires_upload", phase="after", correctness=0.0),
            row(id="E", type="faq", correctness=0.0, abstained=True, latency_ms=200)]
    s = m.summarize(rows)
    assert (s["n"], s["provider_failed"], s["correctness"], s["hard_correctness"], s["groundedness"]) == (3, ["C"], 0.5, 0.75, 0.9)
    assert (s["p50_ms"], s["p95_ms"], s["tokens"], s["cost_usd"], s["abstained"]) == (200, 300, 45, 0.002, 1)
    assert s["by_type"]["requires_upload (after)"]["correctness"] == 0 and s["by_type"]["multi_hop"]["n"] == 2


@pytest.mark.req("NFR-QUAL-01")
def test_fabrication():
    b = {"type": "requires_upload", "phase": "before"}
    rows = [row(id="ok", abstained=True, **b), row(id="grounded", groundedness=0.9, **b), row(id="invented", groundedness=0.3, **b), row(id="unjudged", **b),
            row(id="later", type="requires_upload", phase="after", groundedness=0.1)]
    assert m.fabricated(rows) == ["invented", "unjudged"]


@pytest.mark.req("NFR-QUAL-02")
@pytest.mark.req("NFR-QUAL-03")
def test_gates_report_either_way_and_never_pass_unmeasured():
    modes = {"hybrid": {"groundedness": 0.9, "intent_accuracy": 0.89, "tier_match": None, "hard_correctness": 0.9}, "rag": {"hard_correctness": 0.75}}
    got = {g["what"][:20]: g["met"] for g in m.gates(modes, [])}
    assert list(got.values()) == [True, None, False, None, True]
    assert all(g["met"] is None for g in m.gates({}, []))


@pytest.mark.req("FR-VF-05")
def test_audit_sample():
    ev = [Evidence(id="e", kind="chunk", text="t", source="s")]
    good = {"answered_tier": "T2", "draft": Draft(text="a", citations=[Citation(marker="[E1]", evidence_id="e")]), "evidence": ev}
    policy = get_policy("chat")
    ids = [uuid.uuid5(uuid.NAMESPACE_URL, str(i)) for i in range(4000)]
    assert 0.17 < sum(wanted(good, policy, i) for i in ids) / len(ids) < 0.23          # 20 %
    every = SimpleNamespace(verification=policy.verification.model_copy(update={"judge_sample_rate": 1.0}))
    assert wanted(good, every, ids[0]) and not wanted(good, get_policy("email"), ids[0])                     # e-mail is judged before sending
    for bad in ({"answered_tier": "T1"}, {"evidence": []}, {"draft": None}, {"draft": Draft(text="a")}, {"blocked_draft": Draft(text="x")},
                {"draft": good["draft"].model_copy(update={"abstained": True})}):
        assert not wanted({**good, **bad}, every, ids[0])


@pytest.mark.req("FR-KG-06")
def test_report_renders_and_escapes():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    rows = [row(mode=x, id="A", question="<script>alert(1)</script>", gold="g", judge="", correctness=0.5) for x in ("hybrid", "rag")]
    modes = {x: m.summarize([r for r in rows if r["mode"] == x]) for x in ("hybrid", "rag")}
    html = Environment(loader=FileSystemLoader(ROOT / "eval"), autoescape=select_autoescape(["html", "j2"])).get_template("report.html.j2").render(
        meta={"date": "2026-01-01", "questions": 1, "limit": 0, "model": "m", "judge": "m", "embedder": "e", "commit": "c"}, status="complete", modes=modes,
        gates=m.gates(modes, rows), types=["multi_hop"], wrong=rows, tiers=[("T3", "T2", 1)], misrouted=1, intents=[("a|b", "c", 1)])
    assert "<script>" not in html and "c (wrong)" in html and "50.0% of 1" in html and "not measured" in html
