"""kNN intent classifier over the taxonomy examples (LLD-6, FR-RT-02).

Cosine similarity to every example; k = 7 nearest; score per intent = similarity-weighted vote share, scaled by
how close the best example of that intent is (`min(1, best_sim / sim_ref)`), so a unanimous vote of distant
neighbours is not "confident". Accept when the top score >= tau_intent (channel policy, 0.80) and the margin over
the second intent >= 0.10.
"""

import asyncio
from dataclasses import dataclass

import numpy as np

from app.providers.base import Embedder
from app.router.taxonomy import RULE_ONLY, IntentSpec, load_taxonomy

K = 7
MARGIN = 0.10
SIM_REF = 0.80          # tuned on make eval-router with text-embedding-005 (D-030)


@dataclass
class KnnResult:
    ranked: list[tuple[str, float]]       # (intent, score) best first
    best_sim: float

    @property
    def top(self) -> tuple[str | None, float]:
        return self.ranked[0] if self.ranked else (None, 0.0)

    def accepted(self, tau: float, margin: float = MARGIN) -> bool:
        if not self.ranked:
            return False
        second = self.ranked[1][1] if len(self.ranked) > 1 else 0.0
        return self.ranked[0][1] >= tau and self.ranked[0][1] - second >= margin


class KnnClassifier:
    def __init__(self, taxonomy: dict[str, IntentSpec] | None = None, k: int = K, sim_ref: float = SIM_REF) -> None:
        tax = taxonomy or load_taxonomy()
        self.labels = [name for name, spec in tax.items() if name not in RULE_ONLY for _ in spec.examples]
        self.texts = [ex for name, spec in tax.items() if name not in RULE_ONLY for ex in spec.examples]
        self.k, self.sim_ref = k, sim_ref
        self._matrix: np.ndarray | None = None
        self._build_task: asyncio.Task[None] | None = None

    @staticmethod
    def _norm(m: np.ndarray) -> np.ndarray:
        return m / np.clip(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12, None)

    async def _build(self, embedder: Embedder) -> None:
        vecs: list[list[float]] = []
        for i in range(0, len(self.texts), 100):
            vecs += await embedder.embed(self.texts[i:i + 100], kind="query")
        self._matrix = self._norm(np.asarray(vecs, dtype=np.float32))

    @property
    def ready(self) -> bool:
        return self._matrix is not None

    async def build(self, embedder: Embedder) -> None:
        """Embed the examples once per process (API startup warms it). Shielded: a request that times out while
        waiting doesn't cancel the build for everyone else; a failed build is retried by the next caller."""
        if self._matrix is not None:
            return
        if self._build_task is None or (self._build_task.done() and self._build_task.exception() is not None):
            self._build_task = asyncio.ensure_future(self._build(embedder))
        await asyncio.shield(self._build_task)

    def classify_vector(self, qvec: list[float]) -> KnnResult:
        if self._matrix is None:
            raise RuntimeError("kNN index not built")
        q = self._norm(np.asarray(qvec, dtype=np.float32))
        sims = self._matrix @ q
        idx = np.argsort(-sims)[: self.k]
        votes: dict[str, float] = {}
        best: dict[str, float] = {}
        for i in idx:
            s = max(float(sims[i]), 0.0)
            lab = self.labels[i]
            votes[lab] = votes.get(lab, 0.0) + s
            best[lab] = max(best.get(lab, 0.0), s)
        total = sum(votes.values()) or 1.0
        scored = {lab: (v / total) * min(1.0, best[lab] / self.sim_ref) for lab, v in votes.items()}
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
        return KnnResult([(lab, round(s, 4)) for lab, s in ranked], round(float(sims[idx[0]]), 4))

    async def classify(self, text: str, embedder: Embedder) -> tuple[KnnResult, list[float]]:
        await self.build(embedder)
        qvec = (await embedder.embed([text], kind="query"))[0]
        return self.classify_vector(qvec), qvec


_knn: KnnClassifier | None = None


def get_knn() -> KnnClassifier:
    global _knn
    if _knn is None:
        from app.config.settings import get_settings

        _knn = KnnClassifier(sim_ref=get_settings().knn_sim_ref)
    return _knn


def set_knn(k: KnnClassifier | None) -> None:
    global _knn
    _knn = k
