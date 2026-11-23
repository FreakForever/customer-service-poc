"""Orchestrator wiring, budgets and tracing (BS-11; FR-OR-01, FR-OR-02, NFR-OBS-01)."""

import ast
import asyncio
from pathlib import Path

import pytest

from app.obs.tracing import TraceRecorder, span_set
from app.orchestrator import graph as g
from app.orchestrator.graph import LLM_NODES, build_graph, mermaid, run_turn
from app.orchestrator.nodes.stubs import NODES
from app.orchestrator.policies import get_policy
from app.orchestrator.state import Envelope

APP = Path(__file__).resolve().parents[2] / "app"


async def turn(text="hello", channel="chat", graph=None, **stub):
    rec = TraceRecorder()
    final = await run_turn(Envelope(channel=channel, text=text, meta={"stub": stub}), graph=graph, recorder=rec)
    return final, rec


def intent(name, conf=0.95):
    return [{"name": name, "confidence": conf}]


@pytest.mark.req("FR-OR-02")
@pytest.mark.parametrize(("stub", "path", "tier"), [
    ({}, ["normalize", "guardrails", "rule_layer", "classify", "llm_router", "cache_lookup", "rag_answer",
          "verify", "decide", "respond"], "T2"),
    ({"intents": intent("faq.warranty"), "cache_hit": True},
     ["normalize", "guardrails", "rule_layer", "classify", "cache_lookup", "verify", "decide", "respond"], "T1"),
    ({"intents": intent("service.ticket_status")},
     ["normalize", "guardrails", "rule_layer", "classify", "tools", "verify", "decide", "respond"], "T0"),
    ({"rule_intents": intent("amc.status", 1.0)},
     ["normalize", "guardrails", "rule_layer", "classify", "tools", "verify", "decide", "respond"], "T0"),
    ({"flags": ["safety"]}, ["normalize", "guardrails", "safety_handoff", "respond"], "T4"),
    ({"flags": ["injection"]}, ["normalize", "guardrails", "refuse_or_redirect", "respond"], "T0"),
    ({"active_agent": "troubleshoot"},
     ["normalize", "guardrails", "rule_layer", "agents", "verify", "decide", "respond"], "T3"),
    ({"intents": intent("troubleshoot.error_code")},
     ["normalize", "guardrails", "rule_layer", "classify", "planner", "agents", "verify", "decide", "respond"], "T3"),
    ({"intents": [{"name": "faq.warranty", "confidence": 0.9}, {"name": "amc.status", "confidence": 0.85}]},
     ["normalize", "guardrails", "rule_layer", "classify", "planner", "agents", "verify", "decide", "respond"], "T3"),
    ({"intents": intent("faq.amc"), "abstain": True},
     ["normalize", "guardrails", "rule_layer", "classify", "cache_lookup", "rag_answer", "planner", "agents",
      "verify", "decide", "respond"], "T3"),
    ({"intents": intent("human")}, ["normalize", "guardrails", "rule_layer", "classify", "handoff_or_review",
                                    "respond"], "T4"),
    ({"intents": intent("faq.service"), "cache_hit": True, "handoff": "low confidence"},
     ["normalize", "guardrails", "rule_layer", "classify", "cache_lookup", "verify", "decide", "handoff_or_review",
      "respond"], "T4"),
])
async def test_routes(stub, path, tier):
    final, _ = await turn(**stub)
    assert final["path"] == path and final["answered_tier"] == tier and final["response"]["answered_tier"] == tier


async def test_email_runs_decompose_before_dispatch_and_drafts_on_low_confidence():
    final, _ = await turn(channel="email", intents=intent("faq.warranty"), cache_hit=True, handoff="low")
    assert final["path"][:6] == ["normalize", "guardrails", "rule_layer", "classify", "decompose", "cache_lookup"]
    assert final["handoff"].action == "draft_for_review" and final["answered_tier"] == "T1"


@pytest.mark.req("FR-OR-02")
async def test_no_llm_node_on_t0_t1_paths():
    def forbidden(name):
        async def boom(state):
            raise AssertionError(f"LLM node {name} ran on a fast path")
        return boom
    strict = build_graph({n: forbidden(n) for n in LLM_NODES})
    for stub in ({"intents": intent("service.ticket_status")}, {"rule_intents": intent("amc.status", 1.0)},
                 {"intents": intent("faq.warranty"), "cache_hit": True}, {"flags": ["safety"]}):
        final, _ = await turn(graph=strict, **stub)
        assert not set(final["path"]) & LLM_NODES and final["answered_tier"] in ("T0", "T1", "T4")


async def test_node_timeout_degrades_to_handoff():
    final, rec = await turn(intents=intent("faq.amc"), sleep_ms={"cache_lookup": 1500})   # retrieval budget 400 ms
    assert final["budget_exceeded"] == "cache_lookup" and final["path"][-3:] == ["degrade", "handoff_or_review", "respond"]
    assert final["handoff"].reason == "budget exceeded in cache_lookup" and final["answered_tier"] == "T4"
    assert next(s for s in rec.spans if s.node == "cache_lookup").status == "timeout"


async def test_total_budget_caps_the_sum_of_nodes(monkeypatch):
    tight = get_policy("chat").model_copy(update={"budget_ms": {"total": 300}, "node_timeouts_ms": {}})
    monkeypatch.setattr(g, "get_policy", lambda channel: tight)
    final, _ = await turn(sleep_ms={"normalize": 150, "guardrails": 100, "rule_layer": 200})
    assert final["budget_exceeded"] == "rule_layer" and "degrade" in final["path"]


@pytest.mark.req("NFR-OBS-01")
async def test_one_span_per_visited_node_with_details():
    async def rag_with_model(state):
        span_set(model="gemini-2.5-flash", tokens_in=120, tokens_out=40, cache_hit=False, k=6)
        return await NODES["rag_answer"](state)
    final, rec = await turn(graph=build_graph({"rag_answer": rag_with_model}))
    assert [s.node for s in rec.spans] == final["path"]
    rag = next(s for s in rec.spans if s.node == "rag_answer")
    assert (rag.model, rag.tokens_in, rag.tokens_out, rag.attrs["k"]) == ("gemini-2.5-flash", 120, 40, 6)
    d = rec.as_dict()
    assert d["answered_tier"] == "T2" and d["tokens_in"] == 120 and d["detail"]["path"] == final["path"]
    assert all(s["duration_ms"] >= 0 for s in d["spans"])


async def test_concurrent_turns_keep_separate_traces():
    results = await asyncio.gather(*(turn(intents=intent("service.ticket_status")) for _ in range(5)),
                                   *(turn(flags=["safety"]) for _ in range(5)))
    for final, rec in results:
        assert [s.node for s in rec.spans] == final["path"]
    assert len({rec.trace_id for _, rec in results}) == 10


@pytest.mark.req("FR-OR-01")
def test_orchestrator_imports_no_channel_adapters():
    for f in (APP / "orchestrator").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for m in mods:
                assert not m.startswith(("app.channels", "app.api")), f"{f.name} imports {m}"


@pytest.mark.req("FR-OR-02")
def test_graph_renders_to_mermaid_and_committed_diagram_is_current():
    diagram = mermaid()
    for name in NODES:
        assert f"\t{name}(" in diagram or f"\t{name}([" in diagram, name
    assert (APP / "orchestrator" / "graph.mmd").read_text() == diagram, "run `make graph`"
