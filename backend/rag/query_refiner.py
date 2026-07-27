"""Query refinement for RAG ablation experiments (RQ1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from services.llm_provider import LLMUnavailableError, call_llm


RefineAction = Literal["rewrite", "decompose", "hyde", "pass_through"]


@dataclass
class RefinedQuery:
    action: RefineAction
    sub_queries: list[str]
    # Set only when action == "hyde": a hypothetical guideline passage the
    # LLM believes would answer the query, embedded for the SEMANTIC search
    # leg only (see retrieve.py) — Hypothetical Document Embeddings (Gao et
    # al. 2022). BM25 keeps using the real query/sub_queries[0]; embedding a
    # hypothetical answer closes the lexical gap between a short query and
    # a long guideline passage better than embedding the query itself does,
    # but it has no bearing on keyword/lexical matching, so it's scoped to
    # the dense-retrieval leg only, not a full query replacement.
    hypothetical_document: str | None = None


def _fallback_refined(raw_query: str) -> RefinedQuery:
    return RefinedQuery(action="pass_through", sub_queries=[raw_query.strip()])


def refine_query(
    raw_query: str,
    risk_payload: dict,
    context: Any = None,  # Optional[PatientContext] -- see patient_context_builder.py
) -> RefinedQuery:
    """
    Zero-shot classify + refine via local/cloud LLM chain.
    On LLMUnavailableError, pass through unchanged (fail toward no claim).

    When `context` is provided, the classification prompt also sees a
    compact trend summary (not raw history -- same "compact for retrieval,
    full for generation" split as query_builder.py) so refinement can
    correctly choose "decompose" for a genuinely multi-hop case (e.g. a
    patient with both a rising-CRP trend AND a new contraindication) rather
    than just judging the bare query text in isolation.
    """
    category = risk_payload.get("category", risk_payload.get("risk_category", ""))

    context_block = ""
    if context is not None and hasattr(context, "query_context_terms"):
        trend_terms = context.query_context_terms()
        assessment_count = getattr(getattr(context, "trends", None), "assessment_count", 0)
        if trend_terms or assessment_count:
            context_block = (
                f"\nPATIENT CONTEXT: {assessment_count} assessment(s) on record"
                + (f"; trends: {', '.join(trend_terms)}" if trend_terms else "")
                + "\n"
            )

    prompt = f"""Classify this clinical retrieval query and produce refined search strings.

RAW QUERY: {raw_query}
RISK CATEGORY: {category}
{context_block}
Choose exactly ONE action:
- "rewrite": improve the query wording (output 1 refined string)
- "decompose": split into 2-5 sub-queries for multi-aspect retrieval --
  consider decomposing if PATIENT CONTEXT shows multiple distinct trend
  signals (e.g. a rising-CRP trend AND an AKI stage) that likely need
  different guideline sections, not just a single symptom
  (cap at 5 sub-queries — matches QOQA Recommendation 3 for clinical/emergency settings)
- "hyde": the query is under-specified or uses terminology that may not
  literally overlap with guideline wording (Hypothetical Document
  Embeddings). Write a short (3-5 sentence) HYPOTHETICAL clinical guideline
  passage that WOULD answer this query if it existed -- plausible clinical
  register and structure, matching how NICE/AAP/WHO guidance is actually
  phrased. This hypothetical passage is used only to improve semantic
  retrieval; it is NEVER shown to the clinician or treated as a real
  citation. Also output the original query in sub_queries[0] unchanged, for
  the lexical/BM25 side of retrieval, which does not use the hypothetical
  passage.
- "pass_through": query is already good (output the original)

Respond with ONLY valid JSON:
{{
  "action": "<rewrite|decompose|hyde|pass_through>",
  "sub_queries": ["<query1>", "..."],
  "hypothetical_document": "<only when action is hyde, else omit or null>"
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
        if action not in ("rewrite", "decompose", "hyde", "pass_through"):
            action = "pass_through"
        sub_queries = [str(q).strip() for q in data.get("sub_queries", []) if str(q).strip()]
        if not sub_queries:
            sub_queries = [raw_query.strip()]
        # Cap decompose sub-queries at 5 (QOQA Recommendation 3).
        if action == "decompose":
            sub_queries = sub_queries[:5]

        hypothetical_document = None
        if action == "hyde":
            doc = data.get("hypothetical_document")
            hypothetical_document = str(doc).strip() if doc else None
            if not hypothetical_document:
                # Model chose "hyde" but didn't actually produce a passage --
                # fail toward the safer, simpler behavior rather than
                # embedding an empty string.
                action = "pass_through"

        return RefinedQuery(action=action, sub_queries=sub_queries, hypothetical_document=hypothetical_document)
    except (LLMUnavailableError, json.JSONDecodeError, KeyError, ValueError):
        return _fallback_refined(raw_query)