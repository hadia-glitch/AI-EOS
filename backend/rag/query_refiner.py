"""Query refinement for RAG ablation experiments (RQ1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from services.llm_provider import LLMUnavailableError, call_llm


RefineAction = Literal["rewrite", "decompose", "disambiguate", "pass_through"]


@dataclass
class RefinedQuery:
    action: RefineAction
    sub_queries: list[str]


def _fallback_refined(raw_query: str) -> RefinedQuery:
    return RefinedQuery(action="pass_through", sub_queries=[raw_query.strip()])


def refine_query(raw_query: str, risk_payload: dict) -> RefinedQuery:
    """
    Zero-shot classify + refine via local/cloud LLM chain.
    On LLMUnavailableError, pass through unchanged (fail toward no claim).
    """
    category = risk_payload.get("category", risk_payload.get("risk_category", ""))
    prompt = f"""Classify this clinical retrieval query and produce refined search strings.

RAW QUERY: {raw_query}
RISK CATEGORY: {category}

Choose exactly ONE action:
- "rewrite": improve the query wording (output 1 refined string)
- "decompose": split into 2-5 sub-queries for multi-aspect retrieval
  (cap at 5 sub-queries — matches QOQA Recommendation 3 for clinical/emergency settings)
- "disambiguate": clarify an ambiguous term (output 1 refined string)
- "pass_through": query is already good (output the original)

Respond with ONLY valid JSON:
{{
  "action": "<rewrite|decompose|disambiguate|pass_through>",
  "sub_queries": ["<query1>", "..."]
}}"""

    try:
        raw, _ = call_llm(
            prompt,
            system="You classify clinical search queries. Respond with valid JSON only.",
        )
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()
        data = json.loads(text)
        action = str(data.get("action", "pass_through"))
        if action not in ("rewrite", "decompose", "disambiguate", "pass_through"):
            action = "pass_through"
        sub_queries = [str(q).strip() for q in data.get("sub_queries", []) if str(q).strip()]
        if not sub_queries:
            sub_queries = [raw_query.strip()]
        # Cap decompose sub-queries at 5 (QOQA Recommendation 3).
        if action == "decompose":
            sub_queries = sub_queries[:5]
        return RefinedQuery(action=action, sub_queries=sub_queries)
    except (LLMUnavailableError, json.JSONDecodeError, KeyError, ValueError):
        return _fallback_refined(raw_query)
