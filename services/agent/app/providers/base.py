"""Provider interface and resilience wrappers (LLD-2, NFR-REL-01).

Every LLM/embedding call goes through ResilientLLM / ResilientEmbedder:
    attempt on primary (per-attempt timeout) -> one retry -> fallback provider/model -> ProviderUnavailable.
Adapters set their SDK's own retries to 0 so this is the only retry layer.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from app.obs.tracing import record_llm

log = logging.getLogger("mcs.providers")

Role = Literal["system", "user", "assistant"]


class Msg(BaseModel):
    role: Role
    content: str
    prompt_id: str | None = None  # template id (e.g. "router.v1"); FakeProvider scripts replies by it


class Completion(BaseModel):
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None
    parsed: BaseModel | None = None
    attempts: int = 1
    fallback_used: bool = False


class ProviderError(Exception):
    """A provider call failed. `transient` errors (timeouts, 429, 5xx, network) are worth one retry."""

    def __init__(self, message: str, *, provider: str = "", transient: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.transient = transient


class ProviderTimeout(ProviderError):
    pass


class ProviderRefusal(ProviderError):
    """The model declined (e.g. Claude stop_reason "refusal"). Not retried on the same model; fallback is tried."""

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, provider=provider, transient=False)


class ProviderUnavailable(ProviderError):
    """Primary, retry and fallback all failed."""

    def __init__(self, message: str, errors: list[BaseException]) -> None:
        super().__init__(message, transient=False)
        self.errors = errors


@runtime_checkable
class LLM(Protocol):
    name: str

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion: ...

    def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]: ...


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str], *, kind: Literal["query", "document"]) -> list[list[float]]: ...


# ---------- helpers shared by adapters ----------

def split_system(messages: list[Msg]) -> tuple[str, list[Msg]]:
    """Providers take system text separately; join all system messages and return the rest in order."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    return system, [m for m in messages if m.role != "system"]


def schema_instruction(schema: type[BaseModel]) -> str:
    return ("Reply with a single JSON object only, no prose or code fences, matching this JSON Schema:\n"
            + json.dumps(schema.model_json_schema(), separators=(",", ":")))


def parse_json(schema: type[BaseModel], text: str, *, provider: str) -> BaseModel:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        return schema.model_validate_json(raw)
    except ValidationError as exc:
        # Malformed structured output is usually a sampling hiccup: transient, so it gets the one retry.
        raise ProviderError(f"invalid {schema.__name__} JSON: {exc.errors()[0]['msg']}", provider=provider) from exc


async def with_timeout[T](coro: Awaitable[T], timeout_ms: int, *, provider: str) -> T:
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            return await coro
    except TimeoutError as exc:
        raise ProviderTimeout(f"{provider} timed out after {timeout_ms} ms", provider=provider) from exc


# ---------- resilience ----------

class ResilientLLM:
    """primary (timeout) -> 1 retry on transient errors -> fallback model/provider -> ProviderUnavailable."""

    def __init__(self, primary: LLM, *, fallback: LLM | None = None, fallback_model: str = "") -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_model = fallback_model
        self.name = f"resilient({primary.name})"

    def _plan(self, model: str) -> list[tuple[LLM, str, bool]]:
        plan = [(self.primary, model, False), (self.primary, model, False)]  # attempt + one retry
        if self.fallback_model or self.fallback:
            plan.append((self.fallback or self.primary, self.fallback_model or model, True))
        return plan

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion:
        errors: list[BaseException] = []
        plan = self._plan(model)
        skip_retry = False
        for i, (llm, mdl, is_fallback) in enumerate(plan):
            if i == 1 and skip_retry:
                continue
            start = time.perf_counter()
            try:
                out = await with_timeout(
                    llm.complete(messages, model=mdl, schema=schema, max_tokens=max_tokens,
                                 temperature=temperature, timeout_ms=timeout_ms),
                    timeout_ms, provider=llm.name)
            except ProviderError as exc:
                errors.append(exc)
                skip_retry = skip_retry or (i == 0 and not exc.transient)
                log.warning("llm attempt %d failed (%s %s): %s", i + 1, llm.name, mdl, exc)
                continue
            record_llm(out.model, out.input_tokens, out.output_tokens)
            return out.model_copy(update={
                "attempts": len(errors) + 1, "fallback_used": is_fallback,
                "latency_ms": out.latency_ms or int((time.perf_counter() - start) * 1000)})
        raise ProviderUnavailable(f"all LLM attempts failed for {model}: {errors[-1]}", errors)

    async def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]:
        """Retry/fallback apply only until the first chunk arrives; timeout_ms bounds time to first chunk.
        After that, errors propagate (a half-streamed answer can't be silently restarted)."""
        errors: list[BaseException] = []
        for llm, mdl, _ in self._plan(model):
            it = llm.stream(messages, model=mdl, timeout_ms=timeout_ms).__aiter__()
            try:
                first = await with_timeout(it.__anext__(), timeout_ms, provider=llm.name)
            except StopAsyncIteration:
                return
            except ProviderError as exc:
                errors.append(exc)
                log.warning("llm stream start failed (%s %s): %s", llm.name, mdl, exc)
                continue
            size = len(first)
            try:
                yield first
                async for chunk in it:
                    size += len(chunk)
                    yield chunk
            finally:                                # streams report no usage: about four characters per token
                record_llm(mdl, sum(len(m.content) for m in messages) // 4, size // 4, estimated=True)
            return
        raise ProviderUnavailable(f"all LLM stream attempts failed for {model}: {errors[-1]}", errors)


class ResilientEmbedder:
    """Embedder with per-call timeout, one retry, optional fallback embedder of the same dimension."""

    def __init__(self, primary: Embedder, *, fallback: Embedder | None = None, timeout_ms: int = 3000) -> None:
        if fallback and fallback.dim != primary.dim:
            raise ValueError(f"fallback embedder dim {fallback.dim} != primary dim {primary.dim}")
        self.primary, self.fallback, self.timeout_ms = primary, fallback, timeout_ms
        self.name, self.dim = f"resilient({primary.name})", primary.dim

    async def embed(self, texts: list[str], *, kind: Literal["query", "document"]) -> list[list[float]]:
        if not texts:
            return []
        errors: list[BaseException] = []
        chain = [self.primary, self.primary] + ([self.fallback] if self.fallback else [])
        for emb in chain:
            try:
                vecs = await with_timeout(emb.embed(texts, kind=kind), self.timeout_ms, provider=emb.name)
            except ProviderError as exc:
                errors.append(exc)
                log.warning("embed attempt failed (%s): %s", emb.name, exc)
                continue
            if len(vecs) != len(texts) or any(len(v) != self.dim for v in vecs):
                errors.append(ProviderError(f"{emb.name} returned wrong shape", provider=emb.name))
                continue
            return vecs
        raise ProviderUnavailable(f"all embedding attempts failed: {errors[-1]}", errors)

