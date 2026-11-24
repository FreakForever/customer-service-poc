"""decompose node (BS-21, FR-EM-02): an email with several FAQ-type questions becomes one sub-query per question. Mixed
intents (fault + AMC…) are split by the planner from the router's intents instead."""

from typing import Any

from app.obs.tracing import span_set
from app.orchestrator.state import SubQuery, TurnState
from app.rules.questions import questions


async def decompose(state: TurnState) -> dict[str, Any]:
    intents = state.get("intents") or []
    if any(not (i.name.startswith("faq.") or i.name in ("smalltalk", "other")) for i in intents):
        return {"subqueries": []}
    qs = questions(state["env"].meta.get("body") or state["env"].text)[:6]
    span_set(questions=len(qs))
    return {"subqueries": [SubQuery(text=q, intent="faq.question") for q in qs] if len(qs) > 1 else []}


NODES = {"decompose": decompose}
