"""Reciprocal Rank Fusion for merging hybrid retrieval results."""

'''from typing import Any

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
'''
from typing import Any, Callable

def reciprocal_rank_fusion(
    ranked_lists: list[list[Any]],
    k: int = 60,
    id_fn: Callable[[Any], str] | None = None,
) -> list[tuple[Any, float]]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion (RRF)."""
    
    if id_fn is None:
        def default_id_fn(x: Any) -> str:
            if isinstance(x, dict):
                res = x.get("node_id") or x.get("id")
                return str(res) if res is not None else str(x)
            res = getattr(x, "node_id", None) or getattr(x, "id", None)
            return str(res) if res is not None else str(x)
        id_fn = default_id_fn

    scores: dict[str, float] = {}
    items: dict[str, Any] = {}

    for ranked in ranked_lists:
        seen_in_list = set()
        for rank, item in enumerate(ranked, start=1):
            item_id = id_fn(item)
            
            # Avoid duplicate scoring within the same list
            if item_id in seen_in_list:
                continue
            seen_in_list.add(item_id)

            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            
            # Store the first occurrence of the item
            if item_id not in items:
                items[item_id] = item

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(items[item_id], score) for item_id, score in merged]