"""OpenAI-compatible adapter (any /v1/chat/completions + /v1/embeddings endpoint: vLLM, Ollama, gateways…)."""

import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.base import Completion, Msg, ProviderError, parse_json, schema_instruction


def _err(exc: BaseException, provider: str) -> ProviderError:
    import openai

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return ProviderError(f"openai-compat transport error: {exc}", provider=provider)
    if isinstance(exc, (openai.RateLimitError, openai.InternalServerError)):
        return ProviderError(f"openai-compat {exc.status_code}: {exc}", provider=provider)
    if isinstance(exc, openai.APIStatusError):
        return ProviderError(f"openai-compat {exc.status_code}: {exc}", provider=provider,
                             transient=exc.status_code in (408, 409) or exc.status_code >= 500)
    raise exc


class OpenAICompatProvider:
    def __init__(self, *, base_url: str, api_key: str = "", dim: int = 768, embed_model: str = "",
                 name: str = "openai_compat") -> None:
        import openai

        self.name, self.dim, self.embed_model = name, dim, embed_model
        self._client = openai.AsyncOpenAI(base_url=base_url or None, api_key=api_key or "unused", max_retries=0)

    @staticmethod
    def _messages(messages: list[Msg], schema: type[BaseModel] | None) -> list[dict[str, str]]:
        out = [{"role": m.role, "content": m.content} for m in messages]
        if schema is not None:
            out.insert(0, {"role": "system", "content": schema_instruction(schema)})
        return out

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion:
        kw: dict[str, Any] = {"model": model, "messages": self._messages(messages, schema), "max_tokens": max_tokens,
                              "temperature": temperature, "timeout": timeout_ms / 1000}
        if schema is not None:
            kw["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kw)
        except Exception as exc:
            raise _err(exc, self.name) from exc
        choice = resp.choices[0]
        text = choice.message.content or ""
        return Completion(
            text=text, model=model, provider=self.name, stop_reason=choice.finish_reason,
            parsed=parse_json(schema, text, provider=self.name) if schema is not None else None,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            latency_ms=int((time.perf_counter() - start) * 1000))

    async def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]:
        try:
            chunks = await self._client.chat.completions.create(
                model=model, messages=self._messages(messages, None), stream=True, timeout=timeout_ms / 1000)
            async for chunk in chunks:
                if chunk.choices and (delta := chunk.choices[0].delta.content):
                    yield delta
        except ProviderError:
            raise
        except Exception as exc:
            raise _err(exc, self.name) from exc

    async def embed(self, texts: list[str], *, kind: Literal["query", "document"]) -> list[list[float]]:
        try:
            resp = await self._client.embeddings.create(model=self.embed_model, input=texts, dimensions=self.dim)
        except Exception as exc:
            raise _err(exc, self.name) from exc
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
