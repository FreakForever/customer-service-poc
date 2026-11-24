"""Vertex AI adapter (Gemini models + Vertex text embeddings) via the google-genai SDK. Auth: ADC / service account."""

import time
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

from pydantic import BaseModel

from app.providers.base import Completion, Msg, ProviderError, parse_json, split_system

# The Gemini API refuses deadlines under 10 s (400). The real, shorter budget is enforced on our side: ResilientLLM
# cancels the attempt (D-035).
MIN_DEADLINE_MS = 10_000
EMBED_BATCH = 100  # stay well under the per-request instance limit


def _err(exc: BaseException, provider: str) -> ProviderError:
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", 0) or 0
        return ProviderError(f"vertex {code}: {exc}", provider=provider, transient=code in (408, 429) or code >= 500)
    # transport-level failures (DNS, connection reset, read timeout) are transient
    return ProviderError(f"vertex transport error: {exc}", provider=provider)


class VertexProvider:
    def __init__(self, *, project: str, location: str, dim: int = 768, embed_model: str = "text-embedding-005",
                 name: str = "vertex", thinking_budgets: dict[str, int] | None = None, llm_location: str = "",
                 api_key: str = "") -> None:
        from google import genai

        self.name, self.dim, self.embed_model = name, dim, embed_model
        # Gemini 2.5 "thinks" by default; the fast model is used for routing/short answers where that only adds
        # latency, so its budget is 0 unless configured (THINKING_BUDGET_FAST, -1 = model default).
        self.thinking_budgets = thinking_budgets or {}
        # embeddings stay in `location` (asia-south1); generation may use another endpoint (e.g. "global", where
        # the current Gemini models are served) — VERTEX_LLM_LOCATION, D-030
        self._client = genai.Client(vertexai=True, project=project or None, location=location)
        if api_key:      # Gemini Developer API (D-032): generation only; embeddings stay on Vertex
            self._llm = genai.Client(api_key=api_key)
        elif not llm_location or llm_location == location:
            self._llm = self._client
        else:
            self._llm = genai.Client(vertexai=True, project=project or None, location=llm_location)

    @staticmethod
    def _contents(messages: list[Msg]) -> list[Any]:
        from google.genai import types

        return [types.Content(role="model" if m.role == "assistant" else "user", parts=[types.Part(text=m.content)])
                for m in messages]

    def _config(self, system: str, *, max_tokens: int, temperature: float, timeout_ms: int,
                schema: type[BaseModel] | None = None, model: str = "") -> Any:
        from google.genai import types

        budget = self.thinking_budgets.get(model)
        thinking = None
        if budget is not None and budget >= 0:
            # Gemini 3 takes a thinking level; 2.5 takes a token budget
            thinking = (types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL if budget == 0 else
                                             types.ThinkingLevel.LOW) if model.startswith("gemini-3")
                        else types.ThinkingConfig(thinking_budget=budget))
        return types.GenerateContentConfig(thinking_config=thinking,
            system_instruction=system or None, max_output_tokens=max_tokens, temperature=temperature,
            response_mime_type="application/json" if schema else None, response_schema=schema,
            http_options=types.HttpOptions(timeout=max(timeout_ms, MIN_DEADLINE_MS)))

    async def complete(self, messages: list[Msg], *, model: str, schema: type[BaseModel] | None = None,
                       max_tokens: int = 800, temperature: float = 0.0, timeout_ms: int) -> Completion:
        system, rest = split_system(messages)
        start = time.perf_counter()
        try:
            resp = await self._llm.aio.models.generate_content(
                model=model, contents=self._contents(rest),
                config=self._config(system, max_tokens=max_tokens, temperature=temperature,
                                    timeout_ms=timeout_ms, schema=schema, model=model))
        except Exception as exc:
            raise _err(exc, self.name) from exc
        text = resp.text or ""
        parsed = None
        if schema is not None:
            parsed = resp.parsed if isinstance(resp.parsed, schema) else parse_json(schema, text, provider=self.name)
        usage = resp.usage_metadata
        reason = resp.candidates[0].finish_reason if resp.candidates else None
        return Completion(
            text=text, model=model, provider=self.name, parsed=parsed,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            latency_ms=int((time.perf_counter() - start) * 1000),
            stop_reason=str(reason.value if hasattr(reason, "value") else reason) if reason else None)

    async def stream(self, messages: list[Msg], *, model: str, timeout_ms: int) -> AsyncIterator[str]:
        system, rest = split_system(messages)
        try:
            chunks = await self._llm.aio.models.generate_content_stream(
                model=model, contents=self._contents(rest),
                config=self._config(system, max_tokens=2048, temperature=0.0, timeout_ms=timeout_ms, model=model))
            async for chunk in chunks:
                if chunk.text:
                    yield chunk.text
        except ProviderError:
            raise
        except Exception as exc:
            raise _err(exc, self.name) from exc

    async def embed(self, texts: list[str], *, kind: Literal["query", "document"]) -> list[list[float]]:
        from google.genai import types

        task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), EMBED_BATCH):
                resp = await self._client.aio.models.embed_content(
                    model=self.embed_model, contents=cast(Any, texts[i:i + EMBED_BATCH]),
                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim))
                out.extend(list(e.values or []) for e in resp.embeddings or [])
        except Exception as exc:
            raise _err(exc, self.name) from exc
        return out
