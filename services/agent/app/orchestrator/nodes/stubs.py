"""Stub node implementations (BS-11): deterministic, no model calls. Later steps replace them one by one
(guardrails/rules BS-12, router BS-13, cache BS-12/14, RAG answer BS-15, agents BS-16+, verify BS-18...).

For wiring tests and `/v1/debug/run`, stubs read `env.meta["stub"]`:
  {"flags": ["safety"], "intents": [{"name": "faq.warranty", "confidence": 0.9}], "rule_intents": [...],
   "active_agent": "troubleshoot", "cache_hit": true, "abstain": true, "handoff": "low confidence",
   "sleep_ms": {"rag_answer": 500}}
"""

import asyncio
import re
from typing import Any

from app.kg.linker import Entities
from app.orchestrator.state import Draft, HandoffDecision, Intent, TurnState, Verification

Update = dict[str, Any]


def _stub(state: TurnState) -> dict[str, Any]:
    return state["env"].meta.get("stub", {}) if "env" in state else {}


async def _sleep(state: TurnState, node: str) -> None:
    if ms := _stub(state).get("sleep_ms", {}).get(node):
        await asyncio.sleep(ms / 1000)


async def normalize(state: TurnState) -> Update:
    await _sleep(state, "normalize")
    text = re.sub(r"\s+", " ", state["env"].text).strip()
    env = state["env"].model_copy(update={"text": text})
    return {"env": env, "entities": Entities(), "flags": set(), "intents": [], "evidence": [], "subqueries": [],
            "active_agent": _stub(state).get("active_agent"), "draft": None, "handoff": None}


async def guardrails(state: TurnState) -> Update:
    await _sleep(state, "guardrails")
    return {"flags": set(_stub(state).get("flags", []))}


async def safety_handoff(state: TurnState) -> Update:
    return {"answered_tier": "T4", "draft": Draft(text="For your safety, please switch off and isolate the machine. "
                                                       "I'm connecting you to our service team now."),
            "handoff": HandoffDecision(action="handoff", reason="safety", priority="P1")}


async def refuse_or_redirect(state: TurnState) -> Update:
    return {"answered_tier": "T0", "draft": Draft(text="I can help with Matsui equipment, service, parts, AMC and "
                                                       "warranty questions.")}


async def rule_layer(state: TurnState) -> Update:
    await _sleep(state, "rule_layer")
    rules = [Intent(source="rule", **i) for i in _stub(state).get("rule_intents", [])]
    return {"intents": rules}


async def classify(state: TurnState) -> Update:
    """embed_query + speculative retrieval ∥ kNN intent (BS-12/BS-13)."""
    await _sleep(state, "classify")
    if state.get("intents"):
        return {}
    return {"intents": [Intent(source="knn", **i) for i in _stub(state).get("intents", [])]}


async def llm_router(state: TurnState) -> Update:
    await _sleep(state, "llm_router")
    if state.get("intents") and max(i.confidence for i in state["intents"]) > 0:
        return {}
    return {"intents": [Intent(name="faq.general", confidence=0.5, source="stub")]}


async def decompose(state: TurnState) -> Update:
    await _sleep(state, "decompose")
    return {"subqueries": []}


async def tools(state: TurnState) -> Update:
    await _sleep(state, "tools")
    return {"answered_tier": "T0", "draft": Draft(text="[stub] tool result")}


async def cache_lookup(state: TurnState) -> Update:
    await _sleep(state, "cache_lookup")
    if _stub(state).get("cache_hit"):
        return {"cache_hit": True, "answered_tier": "T1", "draft": Draft(text="[stub] cached FAQ answer")}
    return {"cache_hit": False}


async def rag_answer(state: TurnState) -> Update:
    await _sleep(state, "rag_answer")
    abstain = bool(_stub(state).get("abstain"))
    return {"answered_tier": "T2", "retrieval_score": 0.0 if abstain else 1.0,
            "draft": Draft(text="[stub] answer from documents", abstained=abstain)}


async def planner(state: TurnState) -> Update:
    await _sleep(state, "planner")
    return {"subqueries": []}


async def agents(state: TurnState) -> Update:
    await _sleep(state, "agents")
    return {"answered_tier": "T3", "draft": Draft(text="[stub] specialist answer")}


async def verify(state: TurnState) -> Update:
    await _sleep(state, "verify")
    return {"verification": Verification(passed=True)}


async def decide(state: TurnState) -> Update:
    await _sleep(state, "decide")
    if reason := _stub(state).get("handoff"):
        return {"confidence": 0.3, "handoff": HandoffDecision(action=state["policy"].on_low_confidence, reason=reason)}
    return {"confidence": 1.0}


async def handoff_or_review(state: TurnState) -> Update:
    h = state.get("handoff") or HandoffDecision(action="handoff", reason="requested")
    tier = "T4" if h.action == "handoff" else state.get("answered_tier", "T4")
    return {"handoff": h, "answered_tier": tier}


async def degrade(state: TurnState) -> Update:
    """Budget ran out (FR-OR-06): best evidence-based answer with a caveat, else hand off."""
    if state.get("evidence"):
        return {"draft": Draft(text="[stub] partial answer — this may be incomplete; our team can confirm."),
                "answered_tier": state.get("answered_tier", "T2")}
    return {"answered_tier": "T4",
            "draft": Draft(text="This is taking longer than expected, so I'm passing it to our service team."),
            "handoff": HandoffDecision(action=state["policy"].on_low_confidence,
                                       reason=f"budget exceeded in {state.get('budget_exceeded')}")}


async def respond(state: TurnState) -> Update:
    d = state.get("draft")
    h = state.get("handoff")
    return {"response": {"text": d.text if d else "", "citations": [c.model_dump() for c in d.citations] if d else [],
                         "answered_tier": state.get("answered_tier"), "handoff": h.model_dump() if h else None}}


NODES = {f.__name__: f for f in (normalize, guardrails, safety_handoff, refuse_or_redirect, rule_layer, classify,
                                 llm_router, decompose, tools, cache_lookup, rag_answer, planner, agents, verify,
                                 decide, handoff_or_review, degrade, respond)}
