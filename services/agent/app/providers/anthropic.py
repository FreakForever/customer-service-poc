"""Claude adapter: Claude on Vertex AI (AsyncAnthropicVertex, ADC auth) or the Claude API (AsyncAnthropic).

Notes for current Claude models (anthropic SDK 2.x):
- `temperature` is not sent; current models reject sampling parameters, and the Protocol argument is ignored.
- Thinking is adaptive by default and counts toward `max_tokens`, so callers should size max_tokens per node.
- stop_reason "refusal" raises ProviderRefusal, so ResilientLLM moves to the fallback instead of retrying.
- Claude has no embeddings endpoint; the factory pairs this LLM with the Vertex embedder.
"""

import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.base import Completion, Msg, ProviderError, ProviderRefusal, split_system


def _err(exc: BaseException, provider: str) -> ProviderError:
    import anthropic

    # Most specific first: timeouts/network and 429/5xx are transient; other HTTP errors (400/401/403/404) are not.
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError(f"claude timeout: {exc}", provider=provider)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(f"claude connection error: {exc}", provider=provider)
    if isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError)):
        return ProviderError(f"claude {exc.status_code}: {exc}", provider=provider)
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError(f"claude {exc.status_code}: {exc}", provider=provider,
                             transient=exc.status_code in (408, 409) or exc.status_code >= 500)
    raise exc


class AnthropicProvider:
    def __init__(self, *, via: Literal["vertex", "api"] = "vertex", project: str = "", region: str = "global",
                 api_key: str = "", name: str = "anthropic") -> None:
        import anthropic

        self.name = name
        if via == "vertex":
            self._client: Any = anthropic.AsyncAnthropicVertex(project_id=project or None, region=region,
                                                               max_retries=0)
        else:
            self._client = anthropic.AsyncAnthropic(api_key=api_key or None, max_retries=0)

    @staticmethod
    def _kwargs(messages: list[Msg], *, model: str, max_tokens: int, timeout_ms: int) -> dict[str, Any]:
        system, rest = split_system(messages)
        kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "timeout": timeout_ms / 1000,
                              "messages": [{"role": m.role, "content": m.content} for m in rest]}
        if system:
            kw["system"] = system
        return kw

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion:
        kw = self._kwargs(messages, model=model, max_tokens=max_tokens, timeout_ms=timeout_ms)
        start = time.perf_counter()
        try:
            if schema is not None:
                resp = await self._client.messages.parse(output_format=schema, **kw)
            else:
                resp = await self._client.messages.create(**kw)
        except Exception as exc:
            raise _err(exc, self.name) from exc
        if resp.stop_reason == "refusal":
            raise ProviderRefusal(f"{model} refused", provider=self.name)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(
            text=text, model=model, provider=self.name, stop_reason=resp.stop_reason,
            parsed=getattr(resp, "parsed_output", None) if schema is not None else None,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000))

    async def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]:
        kw = self._kwargs(messages, model=model, max_tokens=4096, timeout_ms=timeout_ms)
        try:
            async with self._client.messages.stream(**kw) as s:
                async for text in s.text_stream:
                    yield text
        except Exception as exc:
            raise _err(exc, self.name) from exc
