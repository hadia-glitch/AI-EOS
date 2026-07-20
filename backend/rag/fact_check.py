"""
Fact-checking judge agent — Phase 3 of the RAG hardening plan.

Design decision: a single adversarial judge pass, not separate optimist +
pessimist + judge agents. Three sequential LLM calls (draft optimist case,
draft pessimist case, judge synthesises) triple latency and cost for every
request and roughly triple the rate-limit pressure on the free Gemini/Groq
tiers this project runs on. A single judge prompted to actively look for
unsupported claims — rather than to summarise — gets most of the same
error-catching benefit for a third of the cost. This is gated to
HIGH/CRITICAL risk categories only (config.fact_check_categories), which is
exactly the population where a missed hallucination matters most and where
the added ~1-2s latency is acceptable against the clinical stakes; LOW/
INTERMEDIATE stay fast and cheap.

The judge does NOT re-decide the clinical recommendation and does NOT call
out to the open web — it strictly checks the draft's claims against (a) the
retrieved guideline chunks and (b) the deterministic risk_result data it was
given, mirroring how a human colleague "second read" would sanity-check a
note against the chart and the reference text, not against their own
independent judgement.

Uses the same unified provider chain as the rest of the app (Local LLM ->
Gemini -> Groq, see services/llm_provider.py) — a HIPAA-strict deployment
running local_llm_enabled with disable_cloud_llm_fallback=True keeps the
fact-check judge's own traffic local too, same as everything else.
"""

from __future__ import annotations

import json
from typing import Any

from config import get_settings
from rag.retrieve import EvidenceChunkResult
from schemas import FactCheckResult
from services.llm_provider import LLMUnavailableError, call_llm


def should_fact_check(category: str, settings=None) -> bool:
    settings = settings or get_settings()
    if not settings.enable_fact_check:
        return False
    return str(category or "").upper() in settings.fact_check_category_set


def _build_judge_prompt(
    draft_summary: str,
    risk_payload: dict[str, Any],
    active_guideline: str,
    chunks: list[EvidenceChunkResult],
) -> str:
    chunk_text = "\n\n".join(
        f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}"
        for c in chunks
    ) or "(no guideline chunks were retrieved)"

    patient = risk_payload.get("patient", {})
    if hasattr(patient, "model_dump"):
        patient = patient.model_dump()

    return f"""You are a fact-checking judge reviewing clinical AI output before it reaches a doctor. \
You did NOT write the draft below — a separate model did. Your only job is to verify every clinical \
claim in the draft against the two sources of truth provided, and flag anything unsupported. You do \
NOT re-decide the risk category or recommend different actions; you only check whether the draft's \
factual claims are grounded.

ACTIVE GUIDELINE: {active_guideline}

SOURCE 1 — DETERMINISTIC RISK RESULT (already calculated by a rule engine, treat as ground truth):
- Category: {risk_payload.get('category', risk_payload.get('risk_category'))}
- Combined score: {risk_payload.get('total_score', risk_payload.get('combined_score'))}
- Layer scores: L1={risk_payload.get('layer1_score')}, L2={risk_payload.get('layer2_score')}, L3={risk_payload.get('layer3_score')}
- Risk drivers: {json.dumps(risk_payload.get('drivers', []))}
- De-identified patient snapshot: {json.dumps(patient, default=str)}

SOURCE 2 — RETRIEVED GUIDELINE EVIDENCE (the only permitted source for clinical facts/drugs/doses/thresholds):
{chunk_text}

DRAFT UNDER REVIEW:
{draft_summary}

INSTRUCTIONS:
1. Check every factual clinical claim in the draft (drug names, doses, thresholds, timeframes,
   causal statements like "X increases risk because Y") against SOURCE 1 and SOURCE 2 only.
2. A claim is "unsupported" if it is not stated in or reasonably implied by SOURCE 1 or SOURCE 2 —
   even if it is generically true clinical knowledge, flag it, because this system's whole purpose is
   traceability to the cited guideline, not general LLM knowledge.
3. Do not flag stylistic phrasing, summarisation, or reasonable clinical framing — only flag claims
   that assert a fact, number, or causal link not present in the sources.
4. Set verified=false only if there is at least one unsupported claim that could change clinical
   action (e.g. a wrong drug, dose, or threshold) — minor unsupported phrasing still allows
   verified=true but should be listed in flagged_claims and lower the confidence score.
5. confidence is your overall 0.0-1.0 assessment of how well-grounded the draft is in the sources.

Respond with ONLY valid JSON — no markdown fences, no preamble.
Schema:
{{
  "verified": <bool>,
  "confidence": <float 0.0-1.0>,
  "flagged_claims": ["<claim 1 that is unsupported>", "..."],
  "notes": "<1-2 sentence summary of your review>"
}}"""


def _parse_judge_response(raw: str, model_version: str) -> FactCheckResult:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    data = json.loads(text)  # raises on failure -- caller handles

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    return FactCheckResult(
        performed=True,
        verified=bool(data.get("verified", True)),
        confidence=confidence,
        flagged_claims=[str(c) for c in data.get("flagged_claims", [])],
        notes=str(data.get("notes", "")),
        judge_model=model_version,
    )


def run_fact_check(
    draft_summary: str,
    risk_payload: dict[str, Any],
    active_guideline: str,
    chunks: list[EvidenceChunkResult],
) -> FactCheckResult:
    """
    Runs the judge pass. Caller is responsible for gating this with
    should_fact_check() first — this function always attempts a check when
    called. Never raises: any failure downgrades to performed=False so the
    draft is still served rather than blocked.
    """
    prompt = _build_judge_prompt(draft_summary, risk_payload, active_guideline, chunks)

    try:
        raw, model_used = call_llm(
            prompt,
            system="You are a strict clinical fact-checking judge. "
                   "You always respond with valid JSON only.",
        )
        result = _parse_judge_response(raw, f"judge:{model_used}")
        print(f"[FactCheck] {model_used} judge OK verified={result.verified} confidence={result.confidence}")
        return result
    except LLMUnavailableError as e:
        print(f"[FactCheck] No judge provider available ({e}) — skipping (draft served unverified)")
        return FactCheckResult(performed=False, verified=True, confidence=0.0,
                                notes="Fact-check unavailable (no LLM provider).")


def summarize_explanation_for_judge(clinical_summary: str, per_driver: list[dict],
                                     actions: list[str], evidence_summary: str) -> str:
    driver_lines = "\n".join(
        f"- {d.get('factor', '')}: {d.get('explanation', '')}" for d in per_driver
    )
    actions_lines = "\n".join(f"- {a}" for a in actions)
    return (
        f"CLINICAL SUMMARY:\n{clinical_summary}\n\n"
        f"PER-DRIVER EXPLANATIONS:\n{driver_lines}\n\n"
        f"RECOMMENDED ACTIONS:\n{actions_lines}\n\n"
        f"EVIDENCE SUMMARY:\n{evidence_summary}"
    )


def summarize_care_plan_for_judge(clinical_summary: str, risk_analysis: str,
                                   driver_breakdown: str, actions: list[str],
                                   antibiotic_plan: dict, monitoring_plan: str,
                                   escalation_criteria: str) -> str:
    actions_lines = "\n".join(f"- {a}" for a in actions)
    abx_lines = "\n".join(f"- {r}" for r in antibiotic_plan.get("regimen", []))
    return (
        f"CLINICAL SUMMARY:\n{clinical_summary}\n\n"
        f"RISK ANALYSIS:\n{risk_analysis}\n\n"
        f"DRIVER BREAKDOWN:\n{driver_breakdown}\n\n"
        f"RECOMMENDED ACTIONS:\n{actions_lines}\n\n"
        f"ANTIBIOTIC PLAN: required={antibiotic_plan.get('required')} "
        f"urgency={antibiotic_plan.get('urgency')}\n{abx_lines}\n"
        f"duration={antibiotic_plan.get('duration')} "
        f"stop_criteria={antibiotic_plan.get('stop_criteria')}\n\n"
        f"MONITORING PLAN:\n{monitoring_plan}\n\n"
        f"ESCALATION CRITERIA:\n{escalation_criteria}"
    )