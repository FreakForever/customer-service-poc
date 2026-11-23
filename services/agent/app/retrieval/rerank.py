"""Rerankers behind one interface (LLD-8, FR-RG-04, OD-005 → D-025).

CrossEncoderReranker: local ONNX cross-encoder (fastembed, no torch), loaded once per process.
NoopReranker: keeps the incoming (RRF) order. Scores are higher-is-better floats, one per document.
"""

from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.config.settings import Settings, get_settings


@runtime_checkable
class Reranker(Protocol):
    name: str

    def score(self, query: str, documents: list[str]) -> list[float]: ...


class NoopReranker:
    name = "none"

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [float(-i) for i in range(len(documents))]


class CrossEncoderReranker:
    def __init__(self, model: str, cache_dir: str = "", threads: int | None = None, max_length: int = 256) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = f"cross_encoder:{model}"
        self._enc = TextCrossEncoder(model_name=model, cache_dir=cache_dir or None, threads=threads)
        # Shorter pairs are much faster on CPU (cost grows with tokens); headings + the start of a chunk carry
        # most of the relevance signal. fastembed exposes no option, so set it on the tokenizer it loaded.
        tokenizer = getattr(getattr(self._enc, "model", None), "tokenizer", None)
        if tokenizer is not None:
            tokenizer.enable_truncation(max_length=max_length)

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return [float(s) for s in self._enc.rerank(query, documents, batch_size=32)]


def make_reranker(s: Settings) -> Reranker:
    if s.reranker == "none":
        return NoopReranker()
    return CrossEncoderReranker(s.rerank_model, s.rerank_cache_dir, max_length=s.rerank_max_length)


@lru_cache
def get_reranker() -> Reranker:
    return make_reranker(get_settings())


def warm() -> None:
    """Download and load the model (Docker build step, so Cloud Run never downloads at request time)."""
    get_reranker().score("warm up", ["warm up"])
