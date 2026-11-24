"""degrade node (BS-19, FR-OR-06): the turn ran out of budget or a node failed. Quote the best evidence already found,
with a caveat and without a model, or hand off."""

import re

from app.obs.tracing import span_set
from app.orchestrator.state import Citation, Draft, HandoffDecision, TurnState
from app.verify.confidence import retrieval_norm

HANDOFF_TEXT = "This is taking longer than expected, so I'm passing it to our service team."
CAVEAT = ("This is what I could confirm in time and may be incomplete. Our service team can check the details if you'd "
          "like.")
MIN_RETRIEVAL = 0.8        # exact evidence, or a strong cross-encoder match; a merely similar chunk isn't an answer


def excerpt(text: str, words: int = 60) -> str:
    body = re.sub(r"\s+", " ", text.split("\n\n", 1)[-1]).strip()
    w = body.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


async def degrade(state: TurnState) -> dict:
    cause = state.get("budget_exceeded") or "unknown"
    policy, email = state["policy"], state["env"].channel == "email"
    evidence = state.get("evidence") or []
    why = f"{'error' if state.get('node_error') else 'budget exceeded'} in {cause}"
    span_set(cause=cause, error=state.get("node_error"))
    if evidence and retrieval_norm(evidence) >= MIN_RETRIEVAL:
        best = sorted(evidence, key=lambda e: e.kind == "chunk")[:2]         # graph and tool facts before chunks
        cites = [Citation(marker=f"[E{i}]", evidence_id=e.citation, doc_id=e.doc_id, anchor=e.anchor)
                 for i, e in enumerate(best, 1)]
        text = "\n".join(f"{excerpt(e.text)} [E{i}]" for i, e in enumerate(best, 1)) + f"\n{CAVEAT}"
        out: dict = {"draft": Draft(text=text, citations=cites), "answered_tier": state.get("answered_tier") or "T2",
                     "evidence": best, "confidence": 0.0}
        if email:                                  # never sent unattended
            out["handoff"] = HandoffDecision(action="draft_for_review", reason=why)
        return out
    return {"answered_tier": "T4", "draft": Draft(text=HANDOFF_TEXT), "confidence": 0.0,
            "handoff": HandoffDecision(action=policy.on_low_confidence, reason=why)}


NODES = {"degrade": degrade}
