"""
Guideline knowledge cache — builds the small (guideline x 4 categories)
table of pre-synthesized care-plan protocols that powers the mobile app's
OFFLINE care plan (see lib/data/offline_guideline_cache.dart).

This is deliberately separate from llm_explanations (the per-request,
per-patient cache): that table has effectively unbounded cardinality (one
row per unique risk payload) and is useless offline because a specific
patient's exact score combination was almost certainly never generated
before. This table has bounded cardinality (num_guidelines x 4) so it CAN
be fully synced to every device and always has an answer ready, at the cost
of being generic-per-category rather than patient-exact — the app fills in
the patient's actual score/drivers into the template client-side.

Rebuild triggers (see main.py):
  - POST /api/v1/admin/guidelines/rebuild-cache (manual, e.g. from the
    Guideline Configuration screen's "Rebuild vector index" flow)
  - Automatically after POST /api/v1/admin/guidelines/ingest succeeds — this
    is what makes a newly-uploaded LOCAL guideline show up in the offline
    cache once the app next syncs.

Synthesis preference: real LLM synthesis grounded in that guideline's
actual retrieved chunks (so a locally uploaded protocol's real content
comes through) > curated_protocols.py hand-written fallback (only used
when no LLM provider is reachable at build time).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from db import get_supabase
from rag.curated_protocols import CATEGORIES, get_curated_protocol
from rag.retrieve import EvidenceChunkResult, retrieve_evidence
from services.llm_provider import LLMUnavailableError, call_llm

DEFAULT_SOURCES = ["NICE", "AAP", "WHO"]


def _discover_sources() -> list[str]:
    """
    Active guideline sources = the three defaults plus any distinct
    `source` value present in guideline_documents (covers locally uploaded
    protocols tagged by document_mapping.infer_document_meta, e.g. 'LOCAL').
    """
    sources = set(DEFAULT_SOURCES)
    try:
        supabase = get_supabase()
        resp = (
            supabase.table("guideline_documents")
            .select("source")
            .eq("active", True)
            .execute()
        )
        for row in resp.data or []:
            src = (row.get("source") or "").strip().upper()
            if src:
                sources.add(src)
    except Exception as e:
        print(f"[GuidelineCache] Could not read guideline_documents ({e}) — using defaults only")
    return sorted(sources)


def _filter_chunks_by_source(chunks: list[EvidenceChunkResult], source: str) -> list[EvidenceChunkResult]:
    source_upper = source.upper()
    filtered = [c for c in chunks if source_upper in (c.source or "").upper()
                or source_upper in (c.source_name or "").upper()]
    return filtered or chunks  # fall back to unfiltered rather than empty-handed


def _build_synthesis_prompt(source: str, category: str, chunks: list[EvidenceChunkResult]) -> str:
    chunk_text = "\n\n".join(
        f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}" for c in chunks
    ) or "(no chunks retrieved for this guideline)"

    return f"""You are building a GENERIC, category-level clinical protocol template for a neonatal \
early-onset sepsis (EOS) decision support app's OFFLINE mode. This is NOT for a specific patient — \
it will be filled in with a specific patient's score/drivers later. Base it strictly on the guideline \
evidence provided below.

GUIDELINE: {source}
RISK CATEGORY: {category}

RETRIEVED GUIDELINE EVIDENCE (the only permitted source of clinical facts):
{chunk_text}

Produce a JSON object with this exact schema. Use the literal placeholder tokens {{patient_ref}}, \
{{total_score}}, {{layer1_score}}, {{layer2_score}}, {{layer3_score}} inside the two template strings \
exactly as shown — do not replace them with real values, they get filled in per-patient later.

{{
  "clinical_summary_template": "1-2 sentence generic summary for a {category} risk patient under {source}, using {{patient_ref}} and {{total_score}} placeholders",
  "risk_analysis_template": "1 sentence referencing {{total_score}}, {{layer1_score}}, {{layer2_score}}, {{layer3_score}}",
  "driver_breakdown_template": "1 short sentence, generic (actual drivers are listed separately in the app)",
  "recommended_actions": ["action 1", "action 2", "..."],
  "antibiotic_plan": {{
    "required": <bool>,
    "urgency": "<e.g. 'Not indicated' / 'Within 1 hour' / 'Immediate'>",
    "regimen": ["drug 1", "drug 2"],
    "duration": "<duration/reassessment guidance>",
    "stop_criteria": "<criteria to stop antibiotics, or empty string if not applicable>"
  }},
  "monitoring_plan": "<monitoring guidance for this category>",
  "escalation_criteria": "<what should trigger escalation to the next category up>"
}}

Respond with ONLY valid JSON — no markdown fences, no preamble."""


def _parse_synthesis(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return json.loads(text)


def synthesize_guideline_protocol(source: str, category: str) -> tuple[dict[str, Any], str, list[dict]]:
    """
    Returns (synthesized_plan, generated_by, source_documents_used).
    Never raises — falls back to curated_protocols on any failure.
    """
    query = f"{category} risk early-onset neonatal sepsis {source} management recommended actions antibiotics monitoring escalation"

    try:
        chunks = retrieve_evidence(query=query, active_guideline=source, top_k=8)
        chunks = _filter_chunks_by_source(chunks, source)
    except Exception as e:
        print(f"[GuidelineCache] Retrieval failed for {source}/{category}: {e}")
        chunks = []

    source_docs = [{"source_name": c.source_name, "section": c.section, "chunk_id": c.chunk_id}
                    for c in chunks]

    if chunks:
        prompt = _build_synthesis_prompt(source, category, chunks)
        try:
            raw, model_used = call_llm(prompt)
            plan = _parse_synthesis(raw)
            print(f"[GuidelineCache] Synthesized {source}/{category} via {model_used}")
            return plan, model_used, source_docs
        except LLMUnavailableError as e:
            print(f"[GuidelineCache] All LLM providers unavailable for {source}/{category}: {e}")

    print(f"[GuidelineCache] Using curated fallback for {source}/{category}")
    return get_curated_protocol(source, category), "curated-fallback", source_docs


def rebuild_all(sources: list[str] | None = None) -> dict[str, Any]:
    """
    Rebuilds guideline_knowledge_cache for every (source, category) pair.
    Called manually (admin endpoint) or automatically after guideline
    ingestion completes.
    """
    sources = sources or _discover_sources()
    supabase = get_supabase()
    built, failed = [], []

    for source in sources:
        for category in CATEGORIES:
            try:
                plan, generated_by, source_docs = synthesize_guideline_protocol(source, category)
                supabase.table("guideline_knowledge_cache").upsert(
                    {
                        "guideline_source": source,
                        "risk_category": category,
                        "synthesized_plan": plan,
                        "source_documents": source_docs,
                        "generated_by": generated_by,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    on_conflict="guideline_source,risk_category",
                ).execute()
                built.append(f"{source}/{category}")
            except Exception as e:
                print(f"[GuidelineCache] Failed to build/write {source}/{category}: {e}")
                failed.append(f"{source}/{category}")

    print(f"[GuidelineCache] Rebuild complete: {len(built)} built, {len(failed)} failed")
    return {"built": built, "failed": failed, "sources": sources}


def get_sync_payload(since: str | None = None) -> list[dict[str, Any]]:
    """Returns cache rows for the mobile app to pull and store locally."""
    supabase = get_supabase()
    query = supabase.table("guideline_knowledge_cache").select("*")
    if since:
        query = query.gt("generated_at", since)
    resp = query.execute()
    return resp.data or []