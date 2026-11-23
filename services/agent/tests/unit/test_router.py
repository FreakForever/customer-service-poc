"""Intent taxonomy, kNN classifier, LLM router and their graph nodes (BS-13; FR-RT-02, FR-RT-03, FR-CH-07).
Offline: FakeProvider embeddings (hash bag-of-words) and scripted router output."""

import json
import re
from pathlib import Path

import pytest

from app.obs.tracing import TraceRecorder
from app.orchestrator.graph import run_turn
from app.orchestrator.nodes import router as router_node
from app.orchestrator.state import Envelope
from app.providers import ResilientLLM, get_embedder
from app.providers.base import ProviderUnavailable
from app.providers.fake import FakeProvider
from app.router.knn import KnnClassifier, KnnResult, set_knn
from app.router.language import detect_language
from app.router.llm_router import ALLOWED, RouterOutput, llm_route
from app.router.taxonomy import RULE_ONLY, load_taxonomy

DATA = Path(__file__).resolve().parents[4] / "mock-data" / "data"
TAX = load_taxonomy()


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


@pytest.mark.req("FR-RT-02")
def test_taxonomy_has_15_examples_per_intent_incl_hinglish():
    assert len(TAX) >= 25 and not set(TAX) & RULE_ONLY
    for name, spec in TAX.items():
        assert len(spec.examples) >= 15, name
        assert len(set(map(norm, spec.examples))) == len(spec.examples), f"duplicate example in {name}"
        if name != "smalltalk":
            assert sum(detect_language(e) != "en" for e in spec.examples) >= 2, f"{name} needs Hinglish examples"


def test_examples_never_copy_eval_texts():
    held_out = {norm(q["question"]) for q in map(json.loads, (DATA / "eval" / "questions.jsonl").open())}
    for f in json.loads((DATA / "kb" / "faqs.json").read_text()):
        held_out |= {norm(f["question"]), *map(norm, f["paraphrases"])}
    for c in json.loads((DATA / "channels" / "chat_scripts.json").read_text()):
        held_out |= set(map(norm, c["turns"]))
    leaks = [(n, e) for n, s in TAX.items() for e in s.examples if norm(e) in held_out]
    assert leaks == []


@pytest.fixture
async def knn():
    k = KnnClassifier(TAX)
    await k.build(get_embedder())
    set_knn(k)
    yield k
    set_knn(None)


@pytest.mark.req("FR-RT-02")
async def test_knn_scores_and_acceptance(knn):
    res, qvec = await knn.classify(TAX["amc.renewal"].examples[0], get_embedder())
    assert res.top[0] == "amc.renewal" and res.best_sim > 0.99 and len(qvec) == get_embedder().dim
    assert [s for _, s in res.ranked] == sorted((s for _, s in res.ranked), reverse=True)
    assert KnnResult([("a", 0.85), ("b", 0.70)], 0.9).accepted(0.80)
    assert not KnnResult([("a", 0.85), ("b", 0.80)], 0.9).accepted(0.80)        # margin < 0.10
    assert not KnnResult([("a", 0.75)], 0.9).accepted(0.80)                      # below tau
    assert not KnnResult([], 0.0).accepted(0.80)


async def test_knn_build_runs_once_and_survives_cancellation():
    calls = []

    class Counting(FakeProvider):
        async def embed(self, texts, *, kind):
            calls.append(len(texts))
            return await super().embed(texts, kind=kind)

    k = KnnClassifier(TAX)
    emb = Counting(dim=64)
    import asyncio
    waiter = asyncio.create_task(k.build(emb))
    await asyncio.sleep(0)
    waiter.cancel()                                  # a timed-out request must not cancel the shared build
    await k.build(emb)
    await k.build(emb)
    assert k.ready and sum(calls) == len(k.texts)


@pytest.mark.req("FR-CH-07")
@pytest.mark.parametrize(("text", "lang"), [
    ("pc ko hot air dryer mein dry kar sakte hai kya?", "hinglish"), ("warranty kitna hai machine pe", "hinglish"),
    ("What is the warranty period?", "en"), ("my chiller ka compressor trip ho raha hai", "hinglish"),
    ("is the rotor covered", "en"),
])
def test_language_detection(text, lang):
    assert detect_language(text) == lang


def em001():
    e = next(e for e in json.loads((DATA / "channels" / "emails.json").read_text()) if e["id"] == "EM-001")
    return f"{e['subject']}\n{e['body']}"


@pytest.mark.req("FR-RT-03")
async def test_llm_router_schema_multi_intent_and_one_retry_on_invalid():
    good = {"intents": [{"name": "troubleshoot.error_code", "confidence": 0.9, "span": "DEW PT HIGH"},
                        {"name": "amc.status", "confidence": 0.8, "span": "covered under our AMC"}],
            "entities": {"models": ["DHD-150"], "error_codes": ["E04"], "serials": ["MT-DHD150-23-00417"]},
            "language": "en"}
    bad = {"intents": [{"name": "warranty.check", "confidence": 0.9}]}           # not in the taxonomy
    fake = FakeProvider(script={"router.v1": [json.dumps(bad), json.dumps(good)]})
    out, comp = await llm_route(em001(), ResilientLLM(fake), model="fast", timeout_ms=1000)
    assert isinstance(out, RouterOutput) and comp.attempts == 2
    assert [i.name for i in out.intents] == ["troubleshoot.error_code", "amc.status"]
    assert out.entities.serials == ["MT-DHD150-23-00417"]
    prompt = fake.calls[0]
    assert prompt["schema"] == "RouterOutput"
    always_bad = FakeProvider(script={"router.v1": [json.dumps(bad)] * 3})
    with pytest.raises(ProviderUnavailable):
        await llm_route("x", ResilientLLM(always_bad), model="fast", timeout_ms=1000)
    assert "other" in ALLOWED and "greeting" not in ALLOWED


async def turn(text, channel="chat"):
    rec = TraceRecorder()
    final = await run_turn(Envelope(channel=channel, text=text), recorder=rec)
    return final, {s.node: s for s in rec.spans}


@pytest.mark.req("FR-RT-03")
async def test_graph_uses_llm_router_when_knn_is_unsure_and_plans_multi_intent(knn, monkeypatch):
    script = {"router.v1": json.dumps({"intents": [{"name": "troubleshoot.error_code", "confidence": 0.9},
                                                   {"name": "amc.status", "confidence": 0.8}], "language": "en"})}
    monkeypatch.setattr(router_node, "get_llm", lambda: ResilientLLM(FakeProvider(script=script)))
    final, spans = await turn(em001(), channel="email")
    assert "llm_router" in final["path"] and spans["llm_router"].attrs["routed"] == ["troubleshoot.error_code", "amc.status"]
    assert final["path"][final["path"].index("decompose") + 1] == "planner"        # two top-level intents
    assert {i.source for i in final["intents"]} == {"llm"} and final["language"] == "en"


async def test_confident_knn_skips_the_llm(monkeypatch):
    def boom():
        raise AssertionError("LLM router must not run")
    k1 = KnnClassifier(TAX, k=1)               # hash test embeddings: a single exact neighbour gives score 1.0
    await k1.build(get_embedder())
    set_knn(k1)
    monkeypatch.setattr(router_node, "get_llm", boom)
    final, _spans = await turn(TAX["sales.quote"].examples[3])
    assert "llm_router" not in final["path"] and final["intents"][0].name == "sales.quote"
    assert final["intents"][0].source == "knn" and "query_embedding" in final
    set_knn(None)


async def test_router_failure_falls_back_to_knn_guess(knn, monkeypatch):
    monkeypatch.setattr(router_node, "get_llm", lambda: ResilientLLM(FakeProvider().fail_next(5)))
    final, spans = await turn("something about the machine and maybe parts")
    assert "llm_router" in final["path"] and spans["llm_router"].attrs["fallback"]
    assert final["intents"] == [] or final["intents"][0].source == "knn"
    assert "degrade" not in final["path"]


@pytest.mark.req("FR-CH-07")
async def test_hinglish_is_recorded_and_rules_still_short_circuit(knn, monkeypatch):
    monkeypatch.setattr(router_node, "get_llm", lambda: ResilientLLM(FakeProvider(script={"router.v1": json.dumps(
        {"intents": [{"name": "faq.process", "confidence": 0.9}], "language": "hinglish"})})))
    final, _ = await turn("pc ko hot air dryer mein dry kar sakte hai kya?")
    assert final["language"] == "hinglish"
    final, _ = await turn("status of SR-2026-00112")
    assert final["intents"][0].source == "rule" and "query_embedding" not in final
