"""Backend tests for RAG pipeline."""

import pytest

from rag.rrf import reciprocal_rank_fusion
from rag.query_builder import build_clinical_query
from rag.retrieve import retrieve_evidence


def test_rrf_merges_lists():
    list_a = ["a", "b", "c"]
    list_b = ["b", "d", "a"]
    merged = reciprocal_rank_fusion([list_a, list_b], k=60)
    ids = [str(x) for x, _ in merged]
    assert "b" in ids
    assert "a" in ids
    assert len(merged) <= 4


def test_retrieve_returns_top_chunks():
    results = retrieve_evidence(
        query="EOS management HIGH risk benzylpenicillin gentamicin neonatal sepsis",
        active_guideline="NICE",
        top_k=5,
    )
    assert len(results) <= 5
    assert len(results) > 0
    assert results[0].chunk_text
    assert results[0].chunk_id
