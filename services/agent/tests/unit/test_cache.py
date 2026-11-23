"""FAQ cache logic without a database (BS-14, LLD-7; FR-CA-01, FR-CA-02, FR-CA-04)."""

import pytest

from app.cache.normalize import cache_key, normalize
from app.cache.store import CacheHit, CacheWriteRejected, semantic_allowed, write_answer
from app.kg.linker import Entities
from app.obs.tracing import TraceRecorder
from app.orchestrator.nodes import cache as cache_node
from app.orchestrator.policies import get_policy
from app.orchestrator.state import Envelope, Intent


@pytest.mark.req("FR-CA-01")
@pytest.mark.parametrize(("a", "b"), [
    ("What is the warranty period?", "what is the warranty period"),
    ("Hi team, pls tell me wht is the warranty period??", "what is the warranty period"),
    ("Hello! Kindly share   AMC plans", "share amc plans"),
    ("How much does an annual maintenance contract cost?", "how much does an amc cost"),
    ("Thank you. Is shipping free for spare parts?", "is shipping free for spare parts"),
])
def test_normalizer_and_key(a, b):
    assert normalize(a) == normalize(b) and cache_key(a) == cache_key(b)


def test_normalizer_keeps_meaningful_words():
    assert normalize("Is MT-DHD-ROTOR in stock?") == "is mt dhd rotor in stock"
    assert normalize("u r late") == "you are late"
    assert cache_key("warranty") != cache_key("warranty claim")


@pytest.mark.req("FR-CA-04")
@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"customer_scoped": True}, "customer-scoped"),
    ({"volatile": True}, "real-time"),
    ({"answer": "MT-DHD-ROTOR is in stock at Pune (3 units left)."}, "real-time"),
    ({"answer": "Your ticket SR-2026-00105 is scheduled for Monday.", "origin": "promoted"}, "account id"),
    ({"question": "status of AMC-2025-0014"}, "account id"),
    ({"source_docs": []}, "source document"),
])
async def test_cache_writer_rejects_scoped_volatile_and_unsourced(kwargs, reason):
    args = {"question": "What is the warranty period?", "answer": "12 months from commissioning.",
            "source_docs": ["DOC-POL-WARRANTY-V2"]} | kwargs
    with pytest.raises(CacheWriteRejected, match=reason):
        await write_answer(None, args.pop("question"), args.pop("answer"), args.pop("source_docs"), **args)  # type: ignore[arg-type]


@pytest.mark.req("FR-CA-02")
def test_semantic_cache_only_for_faq_intents_without_account_ids():
    assert semantic_allowed(has_account_ids=False, intent="faq.warranty")
    assert semantic_allowed(has_account_ids=False, intent=None)
    assert not semantic_allowed(has_account_ids=True, intent="faq.warranty")
    assert not semantic_allowed(has_account_ids=False, intent="amc.status")


def state(text, intent="faq.warranty", **ents):
    return {"env": Envelope(channel="chat", text=text), "policy": get_policy("chat"), "flags": set(),
            "entities": Entities(**ents), "intents": [Intent(name=intent, confidence=0.9, source="knn")],
            "query_embedding": [0.1] * 768}


class FakeEngine:
    def begin(self):
        class Ctx:
            async def __aenter__(self):
                return "conn"

            async def __aexit__(self, *a):
                return False
        return Ctx()


async def test_node_returns_t1_draft_with_citation(monkeypatch):
    monkeypatch.setattr(cache_node, "get_engine", lambda: FakeEngine())

    async def exact(conn, text):
        return None

    async def semantic(conn, qvec, tau, **kw):
        assert tau == 0.90 and kw["intent"] == "faq.warranty"
        return CacheHit("semantic", "Standard warranty is 12 months.", ["DOC-POL-WARRANTY-V2"], faq_id="FAQ-001",
                        similarity=0.95)
    monkeypatch.setattr(cache_node, "exact_lookup", exact)
    monkeypatch.setattr(cache_node, "semantic_lookup", semantic)
    with TraceRecorder() as rec:
        out = await cache_node.cache_lookup(state("how long is the warranty"))
    assert out["cache_hit"] and out["answered_tier"] == "T1"
    assert out["draft"].text == "Standard warranty is 12 months. [1]"
    assert out["draft"].citations[0].doc_id == "DOC-POL-WARRANTY-V2"
    assert rec is not None


@pytest.mark.req("FR-CA-04")
async def test_node_skips_cache_for_account_ids_and_survives_db_errors(monkeypatch):
    monkeypatch.setattr(cache_node, "get_engine", lambda: FakeEngine())

    async def never(*a, **k):
        raise AssertionError("cache must not be queried")
    monkeypatch.setattr(cache_node, "exact_lookup", never)
    out = await cache_node.cache_lookup(state("is my AMC-2025-0014 covering this", serials=["MT-DHD150-23-00417"]))
    assert out == {"cache_hit": False}

    async def broken(*a, **k):
        raise ConnectionError("db down")
    monkeypatch.setattr(cache_node, "exact_lookup", broken)
    assert (await cache_node.cache_lookup(state("how long is the warranty")))["cache_hit"] is False
