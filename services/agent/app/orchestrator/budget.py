"""Budget allocator (LLD-5, FR-OR-04, FR-OR-06): a node gets its policy timeout, capped by what is left of the turn
after reserving time to verify, hand off and respond."""

import time

from app.orchestrator.policies import ChannelPolicy
from app.orchestrator.state import TurnState

TAIL = {"verify", "decide"}             # may spend the reserve


def left_s(state: TurnState) -> float:
    return max(0.0, state.get("deadline", float("inf")) - time.monotonic())


def allocate_s(state: TurnState, key: str, *, tail: bool = False) -> float:
    policy: ChannelPolicy = state["policy"]
    reserve = 0.0 if tail else min(policy.reserve_ms, policy.budget_ms["total"] / 4) / 1000
    return max(0.0, min(policy.timeout_ms(key) / 1000, left_s(state) - reserve))
