"""Estimated model cost = tokens x list price (NFR-COST-01, LLD-16). Embeddings are not included (well under 1% of a turn)."""

from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[1] / "config" / "prices.yaml"


@lru_cache
def prices() -> dict[str, dict[str, float]]:
    return yaml.safe_load(PATH.read_text()) or {}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """None when the model has no listed price."""
    p = prices().get(model)
    return None if p is None else (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000
