"""Retrieval helpers that need no database (BS-08, LLD-8)."""

import datetime as dt

import pytest

from app.retrieval.hybrid import rrf_fuse, wants_superseded
from app.retrieval.rerank import NoopReranker, Reranker


@pytest.mark.req("FR-RG-01")
def test_rrf_fusion_math_and_tie_break():
    fused = rrf_fuse([1, 2, 3], [3, 1, 4], k=60)
    scores = dict(fused)
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62) and scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert [cid for cid, _ in fused] == [1, 3, 2, 4]
    assert rrf_fuse([5, 6], [6, 5]) == sorted(rrf_fuse([5, 6], [6, 5]), key=lambda kv: (-kv[1], kv[0]))
    assert rrf_fuse([], []) == []


@pytest.mark.req("FR-RG-06")
@pytest.mark.parametrize(("q", "want"), [
    ("Is the warranty 12 months from dispatch?", False),
    ("What does error E04 mean on a DHD-150?", False),
    ("What was the old warranty policy?", True),
    ("previous AMC terms please", True),
    ("What was the warranty in 2024?", True),
    ("warranty for machines bought before 2025", True),
    ("What will the warranty be in 2027?", False),
])
def test_superseded_cue_detection(q, want):
    assert wants_superseded(q, today=dt.date(2026, 11, 23)) is want


def test_noop_reranker_keeps_order():
    r = NoopReranker()
    assert isinstance(r, Reranker) and r.score("q", ["a", "b", "c"]) == [0.0, -1.0, -2.0]
