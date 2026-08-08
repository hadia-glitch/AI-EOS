"""Fact-checking judge agent — Phase 3 of the RAG hardening plan."""

from __future__ import annotations

import json
from typing import Any

from config import get_settings
from rag.retrieve import EvidenceChunkResult
from schemas import FactCheckResult
from services.llm_provider import LLMUnavailableError, call_llm

# Below this confidence, "verified" must be False (the judge found enough
# ungrounded material to distrust the draft). At or above it, "verified"
# must be True UNLESS a MISSED_REQUIRED_DISCLOSURE deterministic override
# fired (see _enforce_confidence_verified_consistency below). This exists
# because an LLM judge can be internally inconsistent — e.g. reporting
# confidence=0.7 (mostly grounded) while also setting verified=false, which
# is what was showing a "Fact-check flagged this output" warning banner on
# outputs that were actually fine. The prompt instructs the judge toward
# this banding already; this constant is the server-side enforcement that
# makes the contract absolute rather than advisory.
_VERIFIED_CONFIDENCE_FLOOR = 0.6

# Hard cap on how many claims the judge can flag. The judge is instructed to
# consolidate rather than list every minor wording nit, but LLMs don't
# always comply — this is the backstop that keeps the response usable in a
# UI (no "+N more" pile-up of items nobody can actually see) rather than
# relying on a frontend affordance to hide the overflow.
_MAX_FLAGGED_CLAIMS = 4


def should_fact_check(category: str, settings=None) -> bool:
    settings = settings or get_settings()
    if not settings.enable_fact_check:
        return False
    return str(category or "").upper() in settings.fact_check_category_set


def _label_chunks(chunks: list[EvidenceChunkResult]) -> tuple[str, dict[str, str]]:
    """
    Builds the SOURCE 2 evidence block using short, human-readable labels
    (E1, E2, ...) instead of raw chunk_id UUIDs. Two problems this fixes at
    once: (1) the judge was previously shown "[<uuid>] SOURCE — Section:"
    per chunk, and its flagged_claims prose would then literally quote that
    UUID back ("chunk ID: 656f85e5-59c0-...") in clinician-facing output,
    which is meaningless to a doctor; (2) it gives the judge an unambiguous
    per-chunk anchor to cite without needing the real ID at all. Returns
    (formatted_block, {label: chunk_id}) — the mapping is currently unused
    by callers but kept for any future need to resolve a judge citation
    back to its source chunk.
    """
    if not chunks:
        return "(no guideline chunks were retrieved)", {}
    label_map: dict[str, str] = {}
    lines = []
    for i, c in enumerate(chunks, start=1):
        label = f"E{i}"
        label_map[label] = c.chunk_id
        tag = f"{c.source_name or c.source} — {c.section}" if c.section else (c.source_name or c.source)
        lines.append(f"[{label}] {tag}:\n{c.chunk_text}")
    return "\n\n".join(lines), label_map


def label_evidence_chunks(chunks: list[EvidenceChunkResult]) -> tuple[str, dict[str, str]]:
    """
    Public entry point for _label_chunks — the single place in the codebase
    that assigns E1/E2/... labels to a chunk list. gemini_service.py calls
    this to build BOTH the care-plan/explanation generation prompt's
    evidence block AND the response's evidence_labels field, and passes
    the exact same `chunks` list (in the same order) that later reaches
    run_fact_check() here — so a given response's driver_breakdown
    citations, its evidence_labels lookup, and the judge's own
    flagged_claims citations all agree on what "[E3]" means. Returns
    (formatted_block, {label: chunk_id}), same shape as _label_chunks.
    """
    return _label_chunks(chunks)


def _build_judge_prompt(
    draft_summary: str,
    risk_payload: dict[str, Any],
    active_guideline: str,
    chunks: list[EvidenceChunkResult],
    deltas: dict | None = None,
    missed_required_disambiguation: bool = False,
) -> str:
    chunk_text, _label_map = _label_chunks(chunks)

    patient = risk_payload.get("patient", {})
    if hasattr(patient, "model_dump"):
        patient = patient.model_dump()

    delta_block = json.dumps(deltas, default=str) if deltas else "(no delta data available)"

    missed_block = ""
    if missed_required_disambiguation:
        missed_block = (
            "\n\nDETERMINISTIC ALERT: Cross-guideline conflict was detected in retrieved "
            "evidence but the draft's disambiguation_block is EMPTY. This is a required "
            "disclosure failure — set verified=false and flag it, regardless of how "
            "well-grounded everything else in the draft is."
        )

    return f"""You are a fact-checking judge reviewing clinical AI output before it reaches a doctor. \
You did NOT write the draft below — a separate model did. Your only job is to verify every SPECIFIC, \
material clinical claim in the draft against the two sources of truth provided, and flag anything \
unsupported. You do NOT re-decide the risk category or recommend different actions; you only check \
whether the draft's factual claims are grounded. When you reference a source, cite it ONLY by its \
label in brackets (e.g. "[E2]") or by "SOURCE — Section" name (e.g. "NICE — Empiric Antibiotics") — \
NEVER invent or repeat an internal ID; there isn't one for you to see, and a doctor reading your notes \
needs a citation they can act on, not an opaque identifier.

ACTIVE GUIDELINE: {active_guideline}

SOURCE 1 — DETERMINISTIC RISK RESULT (already calculated by a rule engine, treat as ground truth):
- Category: {risk_payload.get('category', risk_payload.get('risk_category'))}
- Combined score: {risk_payload.get('total_score', risk_payload.get('combined_score'))}
- Layer scores: L1={risk_payload.get('layer1_score')}, L2={risk_payload.get('layer2_score')}, L3={risk_payload.get('layer3_score')}
- Risk drivers: {json.dumps(risk_payload.get('drivers', []))}
- De-identified patient snapshot: {json.dumps(patient, default=str)}

ASSESSMENT DELTAS (trend data from prior assessment):
{delta_block}

SOURCE 2 — RETRIEVED GUIDELINE EVIDENCE, labeled [E1], [E2], ... (the only permitted source for
clinical facts/drugs/doses/thresholds):
{chunk_text}

DRAFT UNDER REVIEW:
{draft_summary}
{missed_block}

WHAT TO FLAG — be conservative. Only flag a claim if it is SPECIFIC and MATERIAL:
  - A named drug, numeric dose, interval, or threshold that does not appear in SOURCE 1 or SOURCE 2,
    stated confidently as if it does.
  - A causal or diagnostic claim ("X increases risk because Y", "this confirms Z") that isn't stated
    or reasonably implied by SOURCE 1 or SOURCE 2.
  - A deterministic condition (contraindication flag, cross-guideline conflict) that SOURCE 1 shows
    but the draft never disclosed.

DO NOT flag:
  - Stylistic phrasing, summarisation, reasonable synthesis across multiple evidence chunks, or
    standard clinical terminology/framing that doesn't assert a specific unsupported fact.
  - A claim that restates or paraphrases SOURCE 1's own deterministic values (category, score,
    drivers, patient snapshot) — that source is ground truth by definition, not something to
    cross-check against SOURCE 2.
  - General clinical background that is common neonatal-sepsis knowledge AND doesn't contradict or
    add specificity beyond what SOURCE 1/SOURCE 2 already support (e.g. "sepsis can progress
    quickly" needs no citation; "give 5mg/kg gentamicin" does).
  - A vague or minor phrasing quibble that wouldn't change what a clinician does.
Consolidate related issues into ONE flagged_claims entry rather than several near-duplicates — list
at most {_MAX_FLAGGED_CLAIMS} entries, the most clinically material ones, even if you notice more.

VERIFIED / CONFIDENCE — these must be consistent with each other:
  - confidence is your overall 0.0-1.0 assessment of how well-grounded the draft is in the sources.
  - If confidence >= {_VERIFIED_CONFIDENCE_FLOOR}, verified MUST be true (the draft is well enough
    grounded that a flagged minor issue, if any, doesn't invalidate it).
  - If confidence < {_VERIFIED_CONFIDENCE_FLOOR}, verified MUST be false.
  - EXCEPTION: if the DETERMINISTIC ALERT above is present, verified MUST be false regardless of
    confidence — a required safety disclosure was missed, which isn't a "how well-grounded is the
    prose" question.
  - Do not report a confidence of 0.7+ and then set verified=false (or vice versa) — pick the
    confidence value that actually matches your verified decision.

Respond with ONLY valid JSON — no markdown fences, no preamble.
Schema:
{{
  "verified": <bool>,
  "confidence": <float 0.0-1.0>,
  "flagged_claims": ["<claim 1 that is unsupported, citing [E#] or SOURCE — Section>", "... (max {_MAX_FLAGGED_CLAIMS})"],
  "notes": "<1-2 sentence summary of your review>"
}}"""


def _enforce_confidence_verified_consistency(result: FactCheckResult, missed_required_disambiguation: bool) -> FactCheckResult:
    """
    Server-side backstop for the confidence/verified banding the prompt
    asks for — LLM judges don't always follow instructions exactly, and an
    inconsistent (high-confidence, verified=false) result is precisely what
    was showing a "flagged" warning banner on outputs that the judge itself
    considered well-grounded. This makes the contract absolute: the
    MISSED_REQUIRED_DISCLOSURE case always wins (a real safety disclosure
    gap should never be suppressed by a confidence score), otherwise
    verified is DERIVED from confidence rather than trusted as a separate,
    possibly-contradictory field from the model.
    """
    if missed_required_disambiguation:
        result.verified = False
        if not any("MISSED_REQUIRED_DISCLOSURE" in c for c in result.flagged_claims):
            result.flagged_claims = (
                ["[MISSED_REQUIRED_DISCLOSURE] Cross-guideline conflict detected but not "
                 "disclosed in the draft."] + result.flagged_claims
            )
        return result

    result.verified = result.confidence >= _VERIFIED_CONFIDENCE_FLOOR
    if result.verified:
        # A verified draft's flagged_claims (if the judge listed any anyway
        # despite the "don't nitpick" instruction) are, by construction,
        # minor — keep at most 2 so the UI isn't showing a warning-shaped
        # list of claims attached to an otherwise-passing result.
        result.flagged_claims = result.flagged_claims[:2]
    else:
        result.flagged_claims = result.flagged_claims[:_MAX_FLAGGED_CLAIMS]
    return result


def _parse_judge_response(raw: str, model_version: str) -> FactCheckResult:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    data = json.loads(text)

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    return FactCheckResult(
        performed=True,
        verified=bool(data.get("verified", True)),
        confidence=confidence,
        flagged_claims=[str(c) for c in data.get("flagged_claims", [])][:_MAX_FLAGGED_CLAIMS],
        notes=str(data.get("notes", "")),
        judge_model=model_version,
    )


def run_fact_check(
    draft_summary: str,
    risk_payload: dict[str, Any],
    active_guideline: str,
    chunks: list[EvidenceChunkResult],
    deltas: dict | None = None,
    missed_required_disambiguation: bool = False,
) -> FactCheckResult:
    prompt = _build_judge_prompt(
        draft_summary, risk_payload, active_guideline, chunks,
        deltas=deltas,
        missed_required_disambiguation=missed_required_disambiguation,
    )

    try:
        raw, model_used = call_llm(
            prompt,
            system="You are a strict but fair clinical fact-checking judge. You flag only "
                   "specific, material, unsupported claims — not stylistic phrasing or "
                   "reasonable synthesis. You always respond with valid JSON only.",
        )
        result = _parse_judge_response(raw, f"judge:{model_used}")
        result = _enforce_confidence_verified_consistency(result, missed_required_disambiguation)
        print(f"[FactCheck] {model_used} judge OK verified={result.verified} confidence={result.confidence} "
              f"flags={len(result.flagged_claims)}")
        return result
    except LLMUnavailableError as e:
        print(f"[FactCheck] No judge provider available ({e}) — skipping (draft served unverified)")
        return FactCheckResult(
            performed=False, verified=True, confidence=0.0,
            notes="Fact-check unavailable (no LLM provider).",
        )


def summarize_explanation_for_judge(
    clinical_summary: str, per_driver: list[dict],
    actions: list[str], evidence_summary: str,
) -> str:
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


def summarize_care_plan_for_judge(
    clinical_summary: str,
    risk_analysis: str,
    driver_breakdown: str,
    actions: list[str],
    antibiotic_plan: dict,
    monitoring_plan: str,
    escalation_criteria: str,
    nutrition_fluid_plan: str = "",
    disambiguation_block: str = "",
    contraindication_flags: list[str] | None = None,
    trend_state_change: str = "",
) -> str:
    actions_lines = "\n".join(f"- {a}" for a in actions)
    abx_lines = "\n".join(f"- {r}" for r in antibiotic_plan.get("regimen", []))
    ci_lines = "\n".join(f"- {f}" for f in (contraindication_flags or []))
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
        f"ESCALATION CRITERIA:\n{escalation_criteria}\n\n"
        f"NUTRITION/FLUID PLAN:\n{nutrition_fluid_plan}\n\n"
        f"DISAMBIGUATION BLOCK:\n{disambiguation_block}\n\n"
        f"CONTRAINDICATION FLAGS:\n{ci_lines}\n\n"
        f"TREND STATE CHANGE:\n{trend_state_change}"
    )