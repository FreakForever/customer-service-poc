"""Channel policy profiles (LLD-2, FR-OR-04): budgets, node timeouts, tiers, verification and thresholds."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "policies.yaml"
Tier = Literal["T0", "T1", "T2", "T3", "T4"]


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: Literal["sync", "async", "off"]
    judge: Literal["sync", "async", "off"]
    judge_sample_rate: float = 1.0


class ChannelPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    budget_ms: dict[str, int]
    node_timeouts_ms: dict[str, int]
    allowed_tiers: list[Tier]
    verification: Verification
    thresholds: dict[str, float]
    on_low_confidence: Literal["handoff", "draft_for_review"]

    def timeout_ms(self, node: str) -> int:
        """Timeout for a node; falls back to the channel's total budget if the node has no entry."""
        return self.node_timeouts_ms.get(node, self.budget_ms["total"])


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _resolve(name: str, raw: dict[str, dict[str, Any]], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    if name in seen:
        raise ValueError(f"policy inheritance cycle: {' -> '.join((*seen, name))}")
    if name not in raw:
        raise KeyError(f"unknown policy profile {name!r}")
    body = dict(raw[name])
    parent = body.pop("inherits", None)
    return _deep_merge(_resolve(parent, raw, (*seen, name)), body) if parent else body


def load_policies(path: Path | str = DEFAULT_PATH) -> dict[str, ChannelPolicy]:
    raw = yaml.safe_load(Path(path).read_text())
    return {name: ChannelPolicy(name=name, **_resolve(name, raw)) for name in raw}


@lru_cache
def _cached() -> dict[str, ChannelPolicy]:
    return load_policies()


def get_policy(channel: str) -> ChannelPolicy:
    try:
        return _cached()[channel]
    except KeyError:
        raise KeyError(f"no policy for channel {channel!r}; known: {sorted(_cached())}") from None
