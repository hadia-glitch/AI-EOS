"""Reciprocal Rank Fusion for merging hybrid retrieval results."""

from typing import Any

def reciprocal_rank_fusion(
    ranked_lists: list[list[Any]],
    k: int = 60,
    id_fn=None,
) -> list[tuple[Any, float]]:
    """Merge multiple ranked lists using RRF. Returns (item, score) sorted desc."""
    if id_fn is None:
        id_fn = lambda x: getattr(x, "node_id", None) or getattr(x, "id", None) or str(x)

    scores: dict[str, float] = {}
    items: dict[str, Any] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            item_id = str(id_fn(item))
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            items[item_id] = item

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(items[item_id], score) for item_id, score in merged]
