"""Real nodes for the front of the graph (BS-12, LLD-6): normalize (+ rule entities), guardrails, rule_layer,
safety_handoff and refuse_or_redirect. Deterministic and model-free."""

import time
from typing import Any

from app.obs.tracing import span_set
from app.orchestrator.nodes.stubs import normalize as _normalize_stub
from app.orchestrator.state import Draft, HandoffDecision, TurnState
from app.rules import guardrails as gr
from app.rules.context import get_rule_context
from app.rules.entities import extract_entities
from app.rules.intents import exact_intents

Update = dict[str, Any]

SAFETY_TEXT = ("For your safety: switch the machine off at the main isolator now and keep people away from it. "
               "If there is fire, smoke or anyone is hurt, call emergency services first. Do not restart the machine. "
               "I'm connecting you to our service team as a priority (P1).")
REFUSE_TEXT = ("I can't help with that request. I can only discuss your own equipment, tickets and contracts, and "
               "I can't share other customers' information or internal instructions.")
REDIRECT_TEXT = ("I'm Matsui's customer-support assistant, so I can only help with Matsui equipment: troubleshooting, "
                 "service tickets, spare parts, AMC and warranty. What can I do for you?")
GREETING_TEXT = "Hello! I can help with troubleshooting, service tickets, spare parts, AMC and warranty. How can I help?"
THANKS_TEXT = "You're welcome! Anything else I can help with?"
RESET_TEXT = "Okay, let's start over. How can I help?"


async def normalize(state: TurnState) -> Update:
    out = await _normalize_stub(state)
    out.pop("active_agent")                 # comes from the conversation (run_turn session), never from the message
    ctx = await get_rule_context()
    t0 = time.perf_counter()
    out["entities"] = extract_entities(out["env"].text, ctx.linker)
    span_set(rule_ms=round((time.perf_counter() - t0) * 1000, 2))
    return out


async def guardrails(state: TurnState) -> Update:
    ctx = await get_rule_context()
    env = state["env"]
    own = ctx.customer_company.get(env.customer.customer_id) if env.customer else None
    res = gr.check(env.text, state["entities"], own_company=own, companies=ctx.companies,
                   classifier=ctx.injection_classifier)
    span_set(flags=sorted(res.flags), reasons=res.reasons)
    return {"flags": res.flags}


async def rule_layer(state: TurnState) -> Update:
    intents = exact_intents(state["env"].text, state["entities"])
    span_set(intents=[i.name for i in intents])
    out: Update = {"intents": intents}
    if any(i.name not in ("greeting", "thanks") for i in intents):
        out["active_agent"] = None                  # a new top-level request ends the sticky agent (FR-OR-05)
    return out


async def safety_handoff(state: TurnState) -> Update:
    return {"answered_tier": "T4", "draft": Draft(text=SAFETY_TEXT),
            "handoff": HandoffDecision(action="handoff", reason="safety", priority="P1")}


async def refuse_or_redirect(state: TurnState) -> Update:
    flags = state.get("flags") or set()
    top = max(state.get("intents") or [], key=lambda i: i.confidence, default=None)
    if flags & {"injection", "exfiltration"}:
        text = REFUSE_TEXT
    elif top is not None and top.name == "greeting":
        text = GREETING_TEXT
    elif top is not None and top.name == "thanks":
        text = THANKS_TEXT
    elif top is not None and top.name == "reset":
        text = RESET_TEXT
    else:
        text = REDIRECT_TEXT
    reply: Update = {"answered_tier": "T0", "draft": Draft(text=text)}
    if top is not None and top.name == "reset":
        reply["active_agent"] = None
    return reply


NODES = {f.__name__: f for f in (normalize, guardrails, rule_layer, safety_handoff, refuse_or_redirect)}
