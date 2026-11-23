"""Intent taxonomy (LLD-6): config/intents/*.yaml → {intent: {description, examples}}."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

INTENTS_DIR = Path(__file__).resolve().parents[1] / "config" / "intents"


class IntentSpec(BaseModel):
    name: str
    description: str
    examples: list[str]


@lru_cache
def load_taxonomy(path: str = str(INTENTS_DIR)) -> dict[str, IntentSpec]:
    out: dict[str, IntentSpec] = {}
    for f in sorted(Path(path).glob("*.yaml")):
        for name, body in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items():
            if name in out:
                raise ValueError(f"intent {name} defined twice ({f.name})")
            out[name] = IntentSpec(name=name, **body)
    return out


# rule-layer-only intents (never predicted by kNN/LLM)
RULE_ONLY = {"greeting", "thanks", "reset"}
