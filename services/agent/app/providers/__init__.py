"""Provider factory (LLD-2). Everything else asks for `get_llm()` / `get_embedder()`, never a concrete SDK."""

from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.providers.base import (
    LLM,
    Completion,
    Embedder,
    Msg,
    ProviderError,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    ResilientEmbedder,
    ResilientLLM,
)

__all__ = ["LLM", "Completion", "Embedder", "Msg", "ProviderError", "ProviderRefusal", "ProviderTimeout",
           "ProviderUnavailable", "ResilientEmbedder", "ResilientLLM", "build_embedder", "build_llm",
           "get_embedder", "get_llm", "make_provider"]


def make_provider(name: str, s: Settings) -> Any:
    """Construct one adapter. SDKs are imported lazily so PROVIDER=fake never loads them."""
    if name == "fake":
        from app.providers.fake import FakeProvider
        return FakeProvider(dim=s.embed_dim)
    if name == "vertex":
        from app.providers.vertex import VertexProvider
        return VertexProvider(project=s.gcp_project, location=s.resolved_vertex_location, dim=s.embed_dim,
                              embed_model=s.embed_model,
                              thinking_budgets={s.model_fast: s.thinking_budget_fast},
                              llm_location=s.vertex_llm_location)
    if name == "anthropic":
        from app.providers.anthropic import AnthropicProvider
        return AnthropicProvider(via=s.anthropic_via, project=s.gcp_project, region=s.anthropic_vertex_region,
                                 api_key=s.anthropic_api_key.get_secret_value())
    if name == "openai_compat":
        from app.providers.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(base_url=s.openai_base_url, api_key=s.openai_api_key.get_secret_value(),
                                    dim=s.embed_dim, embed_model=s.embed_model)
    raise ValueError(f"unknown provider {name!r}")


def build_llm(s: Settings) -> ResilientLLM:
    primary = make_provider(s.provider, s)
    fallback = make_provider(s.fallback_provider, s) if s.fallback_provider else None
    return ResilientLLM(primary, fallback=fallback, fallback_model=s.model_fallback)


def build_embedder(s: Settings) -> ResilientEmbedder:
    name = s.embed_provider or ("vertex" if s.provider == "anthropic" else s.provider)
    return ResilientEmbedder(make_provider(name, s), timeout_ms=s.embed_timeout_ms)


@lru_cache
def get_llm() -> ResilientLLM:
    return build_llm(get_settings())


@lru_cache
def get_embedder() -> ResilientEmbedder:
    return build_embedder(get_settings())
