"""Verifier, confidence, overrides and handoff summary (BS-18, LLD-11; FR-VF-01..04, FR-HO-04)."""

import json
import time

import pytest

from app.kg.linker import Entities
from app.obs.tracing import TraceRecorder
from app.orchestrator.graph import run_turn, stub_graph
from app.orchestrator.nodes import verify as node
from app.orchestrator.policies import get_policy
from app.orchestrator.state import CustomerRef, Draft, Envelope, HandoffDecision, Intent, Verification
from app.providers import ResilientLLM
from app.providers.fake import FakeProvider
from app.retrieval.evidence import Evidence
from app.verify import confidence as conf
from app.verify import consistency, coverage
from app.verify.judge import judge
from app.verify.summary import MAX_WORDS, plain, summarize


def ev(text, kind="chunk", source="hybrid", **meta):
    return Evidence(id=f"doc:{text[:9]}", kind=kind, text=text, source=source, doc_id="D", anchor="a", metadata=meta)


EV = [ev("E04 Dew point high. Replace the desiccant rotor. Parts: MT-DHD-ROTOR. Price INR 55,200. Policy from 1 April 2025."),
      ev("Heater element MT-DHD-HTR costs INR 9100. Ticket SR-2026-00105.")]
GOOD = "E04 means dew point high [E1]. Replace the rotor MT-DHD-ROTOR, INR 55200 [E1]."


# ---------- coverage (FR-VF-01) ----------

@pytest.mark.req("FR-VF-01")
@pytest.mark.parametrize(("text", "n"), [
    ("It costs INR 4,500. Call us.", 2), ("Set it to 7.5 C. [E1] Then check flow. [E2]", 2), ("No. 5 station is blocked [E1].", 1),
    ("1. Isolate power [E1].\n2. Check the fuse [E1].\n\nThanks!", 3), ("Use e.g. Glycol below 10 C [E1].", 1)])
def test_sentence_splitter(text, n):
    assert len(coverage.split_sentences(text)) == n, coverage.split_sentences(text)


@pytest.mark.req("FR-VF-01")
def test_coverage_counts_factual_sentences_only():
    c = coverage.measure("Happy to help! The warranty is 12 months [E1]. Clean the filter. You can check:\nIs that clear?")
    assert (c.factual, c.covered, c.uncovered) == (2, 1, ["Clean the filter."]) and c.score == 0.5
    assert coverage.measure("Happy to help!").score == 1.0
    assert coverage.measure("The rotor is degraded. [E1]").score == 1.0          # marker after the full stop


@pytest.mark.req("FR-VF-01")
def test_uncovered_sentences_are_dropped_but_never_safety_warnings():
    t = "Isolate power first. The rotor costs INR 99.\n1. Replace it [E1].\n2. Paint it 3 times."
    assert coverage.drop_uncovered(t) == "Isolate power first.\n1. Replace it [E1]."
    assert coverage.drop_uncovered(GOOD) == GOOD


# ---------- consistency (FR-VF-03) ----------

@pytest.mark.req("FR-VF-03")
@pytest.mark.parametrize(("text", "issue"), [
    (GOOD, None), ("Effective 2025-04-01 [E1].", None), ("Effective April 1, 2025 [E1].", None),
    ("Replace MT-DHD-BLOWER [E1].", "part MT-DHD-BLOWER"), ("It costs INR 55,000 [E1].", "price 55000"),
    ("E05 means overload [E1].", "code E05"), ("See SR-2026-00106 [E2].", "ticket SR-2026-00106"),
    ("Effective 2 April 2025 [E1].", "date 2025-04-02"), ("Contract AMC-2025-0001 covers it [E1].", "amc AMC-2025-0001"),
    ("Serial MT-DHD150-23-00417 is fine [E1].", "serial MT-DHD150-23-00417"),
    ("MT-DHD-ROTOR costs INR 9100 [E1].", "price 9100 is not stated for part MT-DHD-ROTOR"),      # swapped prices
    ("Share your number (for example SR-2026-00999).", None),
])
def test_consistency(text, issue):
    got = consistency.check(text, EV)
    assert (got == []) if issue is None else any(issue in g for g in got), got


@pytest.mark.req("FR-VF-03")
def test_values_from_the_question_and_actions_are_allowed_but_not_prices():
    assert consistency.check("Your SR-2026-00777 is noted.", [], question="what about SR-2026-00777") == []
    assert consistency.check("Created SR-2026-00141.", [], actions=[{"ticket_id": "SR-2026-00141"}]) == []
    assert consistency.check("Yes, INR 100.", EV, question="is it INR 100?")


@pytest.mark.req("FR-VF-03")
def test_citation_markers_are_not_error_codes():
    assert consistency.check("Fine [E9].", EV) == []


# ---------- confidence (FR-VF-04) ----------

@pytest.mark.req("FR-VF-04")
def test_formula_and_proxy():
    s = conf.score(router=0.8, retrieval=0.6, coverage=1.0, judge=0.9)
    assert s["confidence"] == pytest.approx(0.25 * 0.8 + 0.25 * 0.6 + 0.2 + 0.3 * 0.9, abs=1e-3)
    assert conf.score(router=1, retrieval=0.5, coverage=0.8, judge=None)["judge_or_proxy"] == pytest.approx(0.5)
    assert conf.retrieval_norm([]) == 0 and conf.retrieval_norm([ev("x", "kg_path", "T-DIAG")]) == 1
    assert conf.retrieval_norm([ev("x", rerank_score=0.0)]) == 0.5


def state(draft=None, channel="chat", intents=(), **kw):
    p = get_policy(channel)
    return {"env": Envelope(channel=channel, text=kw.pop("text", "what is E04?"), customer=kw.pop("customer", None)),
            "policy": p, "deadline": time.monotonic() + 30, "flags": set(), "entities": Entities(), "evidence": EV,
            "intents": [Intent(name=n, confidence=0.9, source="knn") for n in intents], "answered_tier": "T2",
            "draft": draft, **kw}


@pytest.mark.req("FR-VF-04")
@pytest.mark.parametrize(("channel", "intents", "kw", "action", "why"), [
    ("chat", ("human",), {}, "handoff", "person"),
    ("email", ("complaint",), {}, "draft_for_review", "complaint"),
    ("email", ("amc.status",), {}, "draft_for_review", "unknown sender"),
    ("email", ("amc.status",), {"customer": CustomerRef(customer_id="C")}, None, None),
    ("chat", ("complaint",), {}, None, None),
    ("chat", (), {"verification": Verification(passed=False, issues=["part X"])}, "handoff", "part X"),
    ("email", (), {"verification": Verification(passed=False, issues=["part X"])}, "draft_for_review", "part X"),
    ("email", (), {"draft": Draft(text="t", actions=[{"priority": "P1"}])}, "draft_for_review", "P1"),
])
def test_hard_overrides(channel, intents, kw, action, why):
    h = conf.overrides(state(channel=channel, intents=intents, **kw))
    assert (h is None) if action is None else (h.action == action and why in h.reason)


@pytest.mark.req("FR-VF-04")
async def test_thresholds_are_per_channel():
    d = Draft(text=GOOD, generated=True)
    weak = [ev("x", rerank_score=-4.0)]
    common = {"evidence": weak, "verification": Verification(passed=True, coverage=1.0, judge=0.9)}
    chat = await node.decide(state(d, intents=("faq.x",), **common))
    mail = await node.decide(state(d, channel="email", intents=("faq.x",), **common))
    assert chat["confidence"] == mail["confidence"] and 0.65 < chat["confidence"] < 0.75
    assert "handoff" not in chat and mail["handoff"].action == "draft_for_review"


# ---------- verify / decide nodes ----------

def scripted(monkeypatch, **script):
    fake = FakeProvider(script={k.replace("_", "."): v for k, v in script.items()})
    monkeypatch.setattr(node, "get_llm", lambda: ResilientLLM(fake))
    return fake


@pytest.mark.req("FR-VF-03")
async def test_injected_wrong_part_is_regenerated_then_blocked(monkeypatch):
    bad = "Replace MT-DHD-BLOWER [E1]."
    fake = scripted(monkeypatch, t2_v1=[GOOD])
    ok = await node.verify(state(Draft(text=bad, generated=True)))
    assert ok["verification"].passed and "MT-DHD-ROTOR" in ok["draft"].text and len(fake.calls) == 1
    scripted(monkeypatch, t2_v1=[bad])
    st = state(Draft(text=bad, generated=True), intents=("faq.parts",))
    st.update(await node.verify(st))
    assert not st["verification"].passed
    out = await node.decide(st)
    assert out["handoff"].action == "handoff" and "MT-DHD-BLOWER" not in out["draft"].text
    assert out["blocked_draft"].text == bad


async def test_composed_draft_with_a_mismatch_is_blocked_without_a_model_call(monkeypatch):
    fake = scripted(monkeypatch)
    v = await node.verify(state(Draft(text="Replace MT-DHD-BLOWER [E1]."), answered_tier="T3"))
    assert not v["verification"].passed and fake.calls == []


@pytest.mark.req("FR-VF-01")
async def test_low_coverage_regenerates_once_then_drops(monkeypatch):
    d = Draft(text="Replace the rotor [E1]. It takes 120 minutes. Clean the filter.", generated=True)
    fake = scripted(monkeypatch, t2_v1=["Replace the rotor [E1]. It takes 120 minutes."])
    out = await node.verify(state(d))
    assert out["draft"].text == "Replace the rotor [E1]." and out["verification"].coverage == 0.5 and len(fake.calls) == 1


@pytest.mark.req("FR-VF-02")
async def test_judge_is_sync_for_email_only_and_required_there(monkeypatch):
    j = json.dumps({"faithfulness": 0.95, "unsupported_claims": [], "answers_question": True})
    d = Draft(text=GOOD, generated=True)
    fake = scripted(monkeypatch, judge_v1=j)
    mail = state(d, channel="email", intents=("faq.x",))
    mail.update(await node.verify(mail))
    assert mail["verification"].judge == 0.95 and "handoff" not in await node.decide(mail | {"evidence": [ev("x", rerank_score=5.0)]})
    chat = await node.verify(state(d))
    assert chat["verification"].judge is None and len(fake.calls) == 1
    scripted(monkeypatch, judge_v1=[ValueError("down")] * 5)
    mail = state(d, channel="email", intents=("faq.x",))
    mail.update(await node.verify(mail))
    out = await node.decide(mail)
    assert out["handoff"].action == "draft_for_review" and out["confidence_parts"]["judge_missing"]


@pytest.mark.req("FR-VF-02")
async def test_judge_output_is_schema_checked():
    fake = FakeProvider(script={"judge.v1": ['{"faithfulness": 7}', '{"faithfulness": 0.2, "unsupported_claims": ["x"], "answers_question": true}']})
    out = await judge("q", "a", EV, ResilientLLM(fake), model="m", timeout_ms=500)
    assert out.faithfulness == 0.2 and len(fake.calls) == 2


async def test_abstention_is_low_confidence_and_tool_answers_are_certain():
    a = await node.decide(state(Draft(text="x", abstained=True), intents=("faq.x",)))
    assert a["handoff"].reason.startswith("low confidence") and "blocked_draft" not in a
    t = await node.decide(state(Draft(text="x"), answered_tier="T0"))
    assert t["confidence"] == 1.0 and "handoff" not in t


# ---------- handoff summary (FR-HO-04) ----------

HS = {"handoff": HandoffDecision(action="handoff", reason="low confidence 0.40", priority="P2"),
      "customer": CustomerRef(customer_id="CUST-0010"), "text": "GR-200 trips daily " + "word " * 200,
      "dialog": {"model": "GR-200", "serial": "MT-GR200-24-00233", "tried": ["C-OVERFEED"]}}


@pytest.mark.req("FR-HO-04")
async def test_summary_contents_length_and_fallback():
    st = state(Draft(text="Reduce feed rate"), **HS)
    text = plain(st)
    for want in ("CUST-0010", "GR-200", "MT-GR200-24-00233", "low confidence", "P2"):
        assert want in text, text
    assert len(text.split()) <= MAX_WORDS
    long = ResilientLLM(FakeProvider(script={"summary.v1": "w " * 300}))
    assert len((await summarize(st, long, model="m", timeout_ms=300)).split()) == MAX_WORDS
    down = ResilientLLM(FakeProvider().fail_next(9))
    assert await summarize(st, down, model="m", timeout_ms=300) == text


@pytest.mark.req("FR-HO-04")
async def test_summary_and_reason_never_reach_the_customer(monkeypatch):
    scripted(monkeypatch, summary_v1="internal note")
    rec = TraceRecorder()
    f = await run_turn(Envelope(channel="chat", text="x", meta={"stub": {"intents": [{"name": "human", "confidence": 0.9}]}}),
                       graph=stub_graph({n: node.NODES[n] for n in ("handoff_or_review", "respond")}), recorder=rec)
    assert f["handoff"].summary == "internal note" and f["response"]["handoff"] == {"action": "handoff", "priority": None}
    assert "internal note" not in json.dumps(f["response"])


async def test_safety_handoff_summary_needs_no_model(monkeypatch):
    fake = scripted(monkeypatch)
    f = await run_turn(Envelope(channel="chat", text="there is smoke from the HAD-50 panel"), recorder=TraceRecorder())
    assert f["handoff"].priority == "P1" and "safety" in f["handoff"].summary and fake.calls == []


@pytest.mark.req("FR-VF-04")
async def test_score_components_are_in_the_trace():
    rec = TraceRecorder()
    await run_turn(Envelope(channel="chat", text="x", meta={"stub": {"intents": [{"name": "faq.x", "confidence": 0.9}]}}),
                   graph=stub_graph({n: node.NODES[n] for n in ("verify", "decide")}), recorder=rec)
    assert rec.root["confidence"] == 1.0 and "confidence" in rec.root["detail"]["confidence"]
