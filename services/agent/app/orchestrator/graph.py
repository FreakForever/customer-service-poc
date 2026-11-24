"""Orchestrator state graph (LLD-5; FR-OR-01, FR-OR-02, FR-OR-06).

Routing is plain code over the turn state; models are called only inside nodes (llm_router, rag_answer, planner,
agents, and the judge inside verify). Every node runs under `with_budget`: the node's timeout from the channel
policy, capped by what is left of the turn's total budget; on timeout the turn goes to `degrade`.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from app.obs.tracing import TraceRecorder, span_set, traced
from app.orchestrator.nodes import agents as agent_nodes
from app.orchestrator.nodes import cache as cache_nodes
from app.orchestrator.nodes import rag as rag_nodes
from app.orchestrator.nodes import router as router_nodes
from app.orchestrator.nodes import rules as rule_nodes
from app.orchestrator.nodes import stubs
from app.orchestrator.nodes import tools as tool_nodes
from app.orchestrator.policies import ChannelPolicy, get_policy
from app.orchestrator.state import Envelope, TurnState

NodeFn = Callable[[TurnState], Awaitable[dict[str, Any]]]

LLM_NODES = {"llm_router", "rag_answer", "planner", "agents"}      # FR-OR-02: never on the T0/T1 path
POLICY_KEY = {"classify": "retrieval", "llm_router": "router_llm", "cache_lookup": "retrieval", "tools": "tool",
              "rag_answer": "generate", "agents": "generate", "planner": "router_llm", "verify": "judge",
              "decompose": "router_llm"}
UNBUDGETED = {"degrade", "respond", "safety_handoff", "refuse_or_redirect", "handoff_or_review"}

TOOL_INTENTS = {"service.ticket_status", "amc.status", "warranty.status", "asset.info"}
# ticket and lead flows answer at T0 but run in the agents node: they need more than the tool budget (D-035)
AGENT_INTENTS = {"troubleshoot.error_code", "troubleshoot.symptom", "parts.availability", "parts.compatibility",
                 "service.create_ticket", "sales.quote", "complaint"}
HANDOFF_INTENTS = {"human"}
REDIRECT_INTENTS = {"oos", "smalltalk", "greeting", "thanks", "reset"}


# ---------- routing (code, not model output) ----------

def _top(state: TurnState) -> tuple[str | None, float]:
    intents = state.get("intents") or []
    if not intents:
        return None, 0.0
    best = max(intents, key=lambda i: i.confidence)
    return best.name, best.confidence


def route_guardrails(state: TurnState) -> str:
    flags = state.get("flags") or set()
    if "safety" in flags:
        return "safety_handoff"
    if flags & {"injection", "oos", "exfiltration"}:
        return "refuse_or_redirect"
    return "rule_layer"


def route_rules(state: TurnState) -> str:
    rule_intents = [i for i in state.get("intents") or [] if i.source == "rule"]
    if state.get("active_agent") and not rule_intents:      # sticky agent, no new top-level intent
        return "agents"
    return "classify"


def _to_dispatch(state: TurnState) -> str:
    return "decompose" if state["env"].channel == "email" else dispatch(state)


def route_classify(state: TurnState) -> str:
    _, conf = _top(state)
    rule = any(i.source == "rule" for i in state.get("intents") or [])
    if rule or conf >= state["policy"].thresholds.get("intent_knn", 0.8):
        return _to_dispatch(state)
    return "llm_router"


def dispatch(state: TurnState) -> str:
    """Intent table (LLD-6)."""
    names = {i.name for i in state.get("intents") or []}
    top, _ = _top(state)
    if len(state.get("subqueries") or []) > 1 or len({n.split(".")[0] for n in names}) > 1:
        return "planner"                                       # multi-intent
    if top is None:
        return "rag_answer"
    if top == "safety":
        return "safety_handoff"
    if top in HANDOFF_INTENTS:
        return "handoff_or_review"
    if top in REDIRECT_INTENTS:
        return "refuse_or_redirect"
    if top in TOOL_INTENTS:
        return "tools"
    if top.startswith("parts.") and state.get("entities") is not None and state["entities"].ids("SparePart"):
        return "tools"                                         # part number given → T0
    if top.startswith("faq."):
        return "cache_lookup"
    if top in AGENT_INTENTS:
        return "planner"
    return "rag_answer"                                        # amc.renewal and other knowledge questions


def route_cache(state: TurnState) -> str:
    return "verify" if state.get("cache_hit") else "rag_answer"


def route_rag(state: TurnState) -> str:
    d = state.get("draft")
    if (d is not None and d.abstained) or state.get("retrieval_score", 1.0) < 0.2:
        return "planner"
    return "verify"


def route_agents(state: TurnState) -> str:
    return "classify" if state.get("handback") else "verify"     # sticky agent handed the turn back


def route_decide(state: TurnState) -> str:
    return "handoff_or_review" if state.get("handoff") else "respond"


def _budget_guard(route: Callable[[TurnState], str] | str) -> Callable[[TurnState], str]:
    def guarded(state: TurnState) -> str:
        if state.get("budget_exceeded"):
            return "degrade"
        return route(state) if callable(route) else route
    guarded.__name__ = getattr(route, "__name__", f"to_{route}")
    return guarded


# ---------- budgets + tracing per node ----------

def with_budget(name: str, fn: NodeFn) -> NodeFn:
    @traced(name)
    async def run(state: TurnState) -> dict[str, Any]:
        path = [*state.get("path", []), name]
        timeout_s: float | None = None
        if name not in UNBUDGETED:
            policy: ChannelPolicy = state["policy"]
            remaining = state.get("deadline", float("inf")) - time.monotonic()
            node_s = policy.timeout_ms(POLICY_KEY.get(name, name)) / 1000
            timeout_s = max(0.0, min(node_s, remaining))
        try:
            async with asyncio.timeout(timeout_s):
                out = await fn(state)
        except TimeoutError:
            span_set(status="timeout", timeout_s=timeout_s)
            return {"budget_exceeded": name, "path": path}
        return {**out, "path": path}
    run.__name__ = name
    return run


# ---------- graph ----------

REAL_NODES: dict[str, NodeFn] = {**rule_nodes.NODES, **router_nodes.NODES, **cache_nodes.NODES,
                                **rag_nodes.NODES, **tool_nodes.NODES,
                                **agent_nodes.NODES}  # grows per step


def build_graph(overrides: dict[str, NodeFn] | None = None):
    impl = {**stubs.NODES, **REAL_NODES, **(overrides or {})}
    g = StateGraph(TurnState)
    for name, fn in impl.items():
        g.add_node(name, cast(Any, with_budget(name, fn)))
    g.add_edge(START, "normalize")
    g.add_conditional_edges("normalize", _budget_guard("guardrails"), ["guardrails", "degrade"])
    g.add_conditional_edges("guardrails", _budget_guard(route_guardrails),
                            ["safety_handoff", "refuse_or_redirect", "rule_layer", "degrade"])
    g.add_conditional_edges("rule_layer", _budget_guard(route_rules), ["agents", "classify", "degrade"])
    dispatch_targets = ["planner", "rag_answer", "handoff_or_review", "refuse_or_redirect", "tools", "cache_lookup",
                        "safety_handoff"]
    g.add_conditional_edges("classify", _budget_guard(route_classify),
                            ["llm_router", "decompose", *dispatch_targets, "degrade"])
    g.add_conditional_edges("llm_router", _budget_guard(_to_dispatch), ["decompose", *dispatch_targets, "degrade"])
    g.add_conditional_edges("decompose", _budget_guard(dispatch), [*dispatch_targets, "degrade"])
    g.add_conditional_edges("cache_lookup", _budget_guard(route_cache), ["verify", "rag_answer", "degrade"])
    g.add_conditional_edges("rag_answer", _budget_guard(route_rag), ["planner", "verify", "degrade"])
    g.add_conditional_edges("planner", _budget_guard("agents"), ["agents", "degrade"])
    g.add_conditional_edges("tools", _budget_guard("verify"), ["verify", "degrade"])
    g.add_conditional_edges("agents", _budget_guard(route_agents), ["verify", "classify", "degrade"])
    g.add_conditional_edges("verify", _budget_guard("decide"), ["decide", "degrade"])
    g.add_conditional_edges("decide", _budget_guard(route_decide), ["handoff_or_review", "respond", "degrade"])
    g.add_conditional_edges("degrade", route_decide, ["handoff_or_review", "respond"])
    for src in ("handoff_or_review", "safety_handoff", "refuse_or_redirect"):
        g.add_edge(src, "respond")
    g.add_edge("respond", END)
    return g.compile()


_default_graph = None


def default_graph():
    global _default_graph
    if _default_graph is None:
        _default_graph = build_graph()
    return _default_graph


def stub_graph(overrides: dict[str, NodeFn] | None = None):
    """Every node a stub (wiring tests, debug runs that script the path via env.meta.stub)."""
    return build_graph({**stubs.NODES, **(overrides or {})})


def mermaid(graph=None) -> str:
    return (graph or default_graph()).get_graph().draw_mermaid()


def session_of(final: TurnState) -> dict[str, Any]:
    """What a conversation keeps between turns (conversations.active_agent / .state)."""
    return {"active_agent": final.get("active_agent"), "dialog": final.get("dialog") or {}}


async def run_turn(env: Envelope, *, graph=None, recorder: TraceRecorder | None = None,
                   session: dict[str, Any] | None = None) -> TurnState:
    """Run one inbound message through the graph. The caller owns the recorder (and persists it)."""
    policy = get_policy(env.channel)
    rec = recorder or TraceRecorder(conversation_id=env.conversation_id, channel=env.channel)
    rec.conversation_id, rec.channel = env.conversation_id, env.channel
    state: TurnState = {"env": env, "policy": policy, "trace_id": rec.trace_id or uuid.uuid4(), "path": [],
                        "active_agent": (session or {}).get("active_agent"), "dialog": (session or {}).get("dialog") or {},
                        "deadline": time.monotonic() + policy.budget_ms["total"] / 1000}
    with rec:
        final: TurnState = await (graph or default_graph()).ainvoke(state)
    intents = final.get("intents") or []
    h = final.get("handoff")
    rec.root.update({"answered_tier": final.get("answered_tier"), "intents": [i.name for i in intents],
                     "confidence": final.get("confidence"), "handoff": h is not None and h.action == "handoff",
                     "detail": {"path": final.get("path", []), "flags": sorted(final.get("flags") or []),
                                "budget_exceeded": final.get("budget_exceeded"),
                                "handoff": h.model_dump() if h else None}})
    return final
