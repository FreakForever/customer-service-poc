"""Deterministic offline provider for tests and local runs without credentials (LLD-2, NFR-REL-01).

- Embeddings: hashed bag of words + character trigrams, L2-normalised. Same text gives the same vector in every
  process, and texts that share words land close together (good enough for cache/retrieval tests).
- Completions: scripted per prompt id (`Msg.prompt_id`). A script value may be a string, a dict or BaseModel
  (for schema calls), a callable(messages) returning one of those, an Exception instance to raise, or a list
  consumed one item per call. Unscripted text calls echo deterministically; unscripted schema calls fail loudly.
- Fault injection: `delay_ms` and `fail_next(n, exc)` drive the resilience tests.
"""

import asyncio
import hashlib
import math
import re
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.base import Completion, Msg, ProviderError, ProviderTimeout, parse_json

ScriptValue = str | dict | BaseModel | BaseException | Callable[[list[Msg]], Any] | list
_TOKEN = re.compile(r"[a-z0-9]+")


class FakeScriptMissing(LookupError):
    """A schema call had no script for its prompt id: a test bug, not a provider failure (never retried)."""


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def hash_embedding(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    words = _TOKEN.findall(text.lower())
    feats = words + [w[i:i + 3] for w in words for i in range(max(1, len(w) - 2))]
    for f in feats:
        h = _h(f)
        vec[h % dim] += 1.0 if (h >> 32) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeProvider:
    def __init__(self, *, name: str = "fake", dim: int = 768, script: dict[str, ScriptValue] | None = None,
                 delay_ms: int = 0) -> None:
        self.name, self.dim, self.delay_ms = name, dim, delay_ms
        self.script: dict[str, ScriptValue] = dict(script or {})
        self.calls: list[dict[str, Any]] = []
        self._failures: list[BaseException] = []

    # --- test controls ---
    def fail_next(self, n: int = 1, exc: BaseException | None = None) -> "FakeProvider":
        self._failures.extend([exc or ProviderTimeout("injected timeout", provider=self.name)] * n)
        return self

    async def _tick(self, op: str, **info: Any) -> None:
        self.calls.append({"op": op, **info})
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        if self._failures:
            raise self._failures.pop(0)

    # --- LLM ---
    def _resolve(self, messages: list[Msg]) -> tuple[str | None, Any]:
        pid = next((m.prompt_id for m in messages if m.prompt_id), None)
        if pid is None or pid not in self.script:
            return pid, None
        value = self.script[pid]
        if isinstance(value, list):
            if not value:
                raise FakeScriptMissing(f"script for {pid!r} exhausted")
            value = value.pop(0)
        if isinstance(value, BaseException):
            raise value
        return pid, value(messages) if callable(value) else value

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion:
        await self._tick("complete", model=model, schema=schema.__name__ if schema else None)
        pid, value = self._resolve(messages)
        parsed: BaseModel | None = None
        if schema is not None:
            if value is None:
                raise FakeScriptMissing(f"no fake script for prompt_id={pid!r} (schema {schema.__name__})")
            if isinstance(value, schema):
                parsed = value
            elif isinstance(value, dict):
                parsed = schema.model_validate(value)
            else:
                parsed = parse_json(schema, str(value), provider=self.name)
            text = parsed.model_dump_json()
        elif value is None:
            last = next((m.content for m in reversed(messages) if m.role == "user"), "")
            text = f"[fake:{pid or 'default'}] {last[:200]}"
        else:
            text = value.model_dump_json() if isinstance(value, BaseModel) else str(value)
        if not isinstance(text, str):
            raise ProviderError("fake script returned a non-string", provider=self.name, transient=False)
        words = sum(len(m.content.split()) for m in messages)
        return Completion(text=text, model=model, provider=self.name, input_tokens=words,
                          output_tokens=len(text.split()), latency_ms=self.delay_ms, stop_reason="end_turn",
                          parsed=parsed)

    async def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]:
        out = await self.complete(messages, model=model, timeout_ms=timeout_ms)
        self.calls[-1]["op"] = "stream"
        words = out.text.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "

    # --- Embedder ---
    async def embed(self, texts: list[str], *, kind: Literal["query", "document"]) -> list[list[float]]:
        await self._tick("embed", n=len(texts), kind=kind)
        return [hash_embedding(t, self.dim) for t in texts]
