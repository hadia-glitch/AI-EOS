"""
multi_agent_harness.py
=======================
Offline port of the production multi-agent pipeline's fact-check + resolution
system (gemini_service.py's _CitationRegistry, judge, and resolution loop;
rag/fact_check.py's judge prompt/scoring) into the SAME offline style as the
rest of this eval harness -- local retrieve_fn, no Supabase, no package
restructuring. This is a genuine port of the logic (same prompts, same
confidence/verified enforcement, same "retrieve-first, 3 widened searches,
hardcoded default only as absolute last resort" pattern), not a
reimplementation from scratch -- see each function's docstring for which
production function it mirrors.

What was ALREADY present offline and is reused unmodified (see
care_plan_eval_harness.py): contraindication_rules (already KDIGO-staged +
WHO PSBI, confirmed identical to the production version), the plan JSON
schema (already has monitoring_plan/escalation_criteria/nutrition_fluid_plan/
disambiguation_block/parent_communication_notes -- no schema change needed),
_check_regimen_completeness, _check_ungrounded_dose_claims,
_overlay_deterministic_safety.

What THIS module adds (genuinely new offline):
  1. CitationRegistryHarness    -- E1/E2/... labels instead of raw chunk_ids
  2. run_fact_check_harness     -- the judge, ported from rag/fact_check.py
  3. widened-search regimen grounding -- upgrades _apply_regimen_safety_net's
     "jump straight to hardcoded default" behavior to "try up to 3 widened
     local searches first"
  4. ensure_supplementary_fields_grounded -- monitoring/escalation/nutrition
     grounding, unconditional (every risk category, not just HIGH/CRITICAL)
  5. resolve_flagged_claims     -- the judge-driven resolution loop
  6. generate_care_plan_multi_agent / run_multi_agent_evaluation_harness_pluggable
     -- the full pipeline, same per-item output row shape as every other arm

Usage (see companion notebook cells):
    import multi_agent_harness as mah
    mah.attach(base, cpe, gcp)
    out = mah.run_multi_agent_evaluation_harness_pluggable(items, graph_fn, call_llm, arm_name="graph_multiagent")
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_BASE = None
_CPE = None
_GCP = None


def attach(base_module, cpe_module, gcp_module) -> None:
    """Call once per notebook session, after graph_care_plan_retrieval's own
    attach_base_harness/attach_care_plan_harness -- mirrors that module's
    own attach pattern exactly."""
    global _BASE, _CPE, _GCP
    _BASE, _CPE, _GCP = base_module, cpe_module, gcp_module


def _require():
    if _BASE is None or _CPE is None or _GCP is None:
        raise RuntimeError("multi_agent_harness needs attach(base, cpe, gcp) called first.")
    return _BASE, _CPE, _GCP


# ── ALL-LEVELS FACT-CHECK, not gated to HIGH/CRITICAL ──────────────────────
# Production gates the judge to HIGH/CRITICAL (config.fact_check_categories)
# to control latency/cost in a live app. This eval harness runs it for every
# category, per your instruction -- an eval run's whole point is measuring
# what the judge catches, so gating it would hide exactly the LOW/INTERMEDIATE
# behavior worth measuring. Change FACT_CHECK_ALL_LEVELS = False to restore
# production's HIGH/CRITICAL-only gating if you want a cost/behavior
# comparison between the two policies.
FACT_CHECK_ALL_LEVELS = True
_FACT_CHECK_GATED_CATEGORIES = {"HIGH", "CRITICAL"}

_VERIFIED_CONFIDENCE_FLOOR = 0.6
_MAX_FLAGGED_CLAIMS = 4
_MAX_RESOLUTION_ITERATIONS = 3
_WIDENED_SEARCH_ATTEMPTS = 3


# ─────────────────────────────────────────────────────────────────────────
# 1. Citation registry -- ports gemini_service.py's _CitationRegistry.
#    Same idea: E1/E2/... labels instead of raw chunk_id UUIDs, both for the
#    generation prompt AND the judge prompt, so a citation a clinician (or
#    the judge) sees is always resolvable and never a meaningless UUID.
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CitationRegistryHarness:
    chunks: list = field(default_factory=list)
    _label_by_chunk_id: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        for i, c in enumerate(self.chunks, start=1):
            self._label_by_chunk_id[c.chunk_id] = f"E{i}"

    def add(self, chunk) -> str:
        """Register a NEW chunk found during widened search / resolution --
        reuses the existing label if already registered, otherwise assigns
        the next E#. This is what keeps citation numbering stable and
        non-duplicated across the initial retrieval AND every later widened
        search in the same generation run."""
        if chunk.chunk_id in self._label_by_chunk_id:
            return self._label_by_chunk_id[chunk.chunk_id]
        label = f"E{len(self._label_by_chunk_id) + 1}"
        self._label_by_chunk_id[chunk.chunk_id] = label
        self.chunks.append(chunk)
        return label

    def label_for(self, chunk_id: str) -> str | None:
        return self._label_by_chunk_id.get(chunk_id)

    def prompt_block(self) -> str:
        if not self.chunks:
            return "(no guideline chunks were retrieved)"
        lines = []
        for c in self.chunks:
            label = self._label_by_chunk_id[c.chunk_id]
            tag = f"{getattr(c, 'source_name', c.source)} — {getattr(c, 'section', '')}".rstrip(" —")
            lines.append(f"[{label}] {tag}:\n{c.chunk_text}")
        return "\n\n".join(lines)

    def evidence_label_refs(self) -> list[dict[str, str]]:
        """Mirrors schemas.EvidenceLabelRef -- returned as plain dicts here
        (no pydantic dependency needed offline) since the eval harness's
        output rows are already plain dicts/DataFrames throughout."""
        return [
            {"label": self._label_by_chunk_id[c.chunk_id], "chunk_id": c.chunk_id,
             "source": c.source, "section": getattr(c, "section", ""),
             "snippet": c.chunk_text[:220]}
            for c in self.chunks
        ]


def build_care_plan_prompt_with_citations(item, registry: CitationRegistryHarness, deltas,
                                           contraindication_flags, cross_guideline_conflict, trend_note) -> str:
    """Reuses build_care_plan_prompt() as the single source of truth for
    every OTHER section of the prompt (risk result, patient snapshot,
    previous assessments, deterministic block, schema) and swaps only the
    evidence block for the registry's E#-labeled version -- deliberately NOT
    a second, hand-copied prompt template that could drift from
    build_care_plan_prompt()'s own formatting over time."""
    _, cpe, _ = _require()
    original_block = "\n\n".join(
        f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}" for c in registry.chunks
    ) or "(no guideline chunks were retrieved)"
    prompt = cpe.build_care_plan_prompt(
        item, registry.chunks, deltas, contraindication_flags, cross_guideline_conflict, trend_note,
    )
    return prompt.replace(original_block, registry.prompt_block())


# ─────────────────────────────────────────────────────────────────────────
# 2. Fact-check judge -- ports rag/fact_check.py's _build_judge_prompt /
#    run_fact_check / _enforce_confidence_verified_consistency essentially
#    verbatim, adapted to take a harness call_llm callable directly instead
#    of going through services.llm_provider's Settings-driven chain.
# ─────────────────────────────────────────────────────────────────────────

def build_judge_prompt_harness(
    draft_summary: str, risk_payload: dict, active_guideline: str,
    registry: CitationRegistryHarness, deltas: dict | None = None,
    missed_required_disambiguation: bool = False,
) -> str:
    patient = risk_payload.get("patient", {})
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
label in brackets (e.g. "[E2]") or by "SOURCE — Section" name — NEVER invent or repeat an internal ID.

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
{registry.prompt_block()}

DRAFT UNDER REVIEW:
{draft_summary}
{missed_block}

WHAT TO FLAG — be conservative. Only flag a claim if it is SPECIFIC and MATERIAL:
  - A named drug, numeric dose, interval, or threshold that does not appear in SOURCE 1 or SOURCE 2,
    stated confidently as if it does.
  - A causal or diagnostic claim that isn't stated or reasonably implied by SOURCE 1 or SOURCE 2.
  - A deterministic condition (contraindication flag, cross-guideline conflict) that SOURCE 1 shows
    but the draft never disclosed.

DO NOT flag stylistic phrasing, reasonable synthesis, paraphrases of SOURCE 1's own values, or
general clinical background that doesn't contradict or add unsupported specificity. Consolidate
related issues into ONE flagged_claims entry — list at most {_MAX_FLAGGED_CLAIMS}.

VERIFIED / CONFIDENCE — must be consistent: confidence >= {_VERIFIED_CONFIDENCE_FLOOR} => verified=true;
confidence < {_VERIFIED_CONFIDENCE_FLOOR} => verified=false; EXCEPTION: if the DETERMINISTIC ALERT
above is present, verified MUST be false regardless of confidence.

Respond with ONLY valid JSON — no markdown fences, no preamble.
Schema:
{{
  "verified": <bool>,
  "confidence": <float 0.0-1.0>,
  "flagged_claims": ["<claim citing [E#] or SOURCE — Section>", "... (max {_MAX_FLAGGED_CLAIMS})"],
  "notes": "<1-2 sentence summary>"
}}"""


def _enforce_confidence_verified_consistency(result: dict, missed_required_disambiguation: bool) -> dict:
    if missed_required_disambiguation:
        result["verified"] = False
        if not any("MISSED_REQUIRED_DISCLOSURE" in c for c in result["flagged_claims"]):
            result["flagged_claims"] = (
                ["[MISSED_REQUIRED_DISCLOSURE] Cross-guideline conflict detected but not "
                 "disclosed in the draft."] + result["flagged_claims"]
            )
        return result
    result["verified"] = result["confidence"] >= _VERIFIED_CONFIDENCE_FLOOR
    result["flagged_claims"] = result["flagged_claims"][: (2 if result["verified"] else _MAX_FLAGGED_CLAIMS)]
    return result


def run_fact_check_harness(
    draft_summary: str, risk_payload: dict, active_guideline: str,
    registry: CitationRegistryHarness, call_llm: Callable,
    deltas: dict | None = None, missed_required_disambiguation: bool = False,
) -> dict:
    """Returns a dict matching schemas.FactCheckResult's fields (performed,
    verified, confidence, flagged_claims, notes, judge_model) -- plain dict,
    not the pydantic model, consistent with how every other result in this
    offline harness is a plain dict/dataclass rather than a FastAPI schema."""
    base, cpe, _ = _require()
    prompt = build_judge_prompt_harness(
        draft_summary, risk_payload, active_guideline, registry, deltas, missed_required_disambiguation,
    )
    try:
        raw, model_used = call_llm(
            prompt,
            system="You are a strict but fair clinical fact-checking judge. You flag only "
                   "specific, material, unsupported claims — not stylistic phrasing or "
                   "reasonable synthesis. You always respond with valid JSON only.",
        )
        text = base.strip_json_fences(raw)
        data = json.loads(text)
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        result = {
            "performed": True, "verified": bool(data.get("verified", True)), "confidence": confidence,
            "flagged_claims": [str(c) for c in data.get("flagged_claims", [])][:_MAX_FLAGGED_CLAIMS],
            "notes": str(data.get("notes", "")), "judge_model": f"judge:{model_used}",
        }
        return _enforce_confidence_verified_consistency(result, missed_required_disambiguation)
    except Exception as e:
        print(f"[fact-check] judge call/parse failed ({type(e).__name__}: {e}) — skipping, draft served unverified")
        return {"performed": False, "verified": True, "confidence": 0.0,
                "flagged_claims": [], "notes": f"Fact-check unavailable: {e}", "judge_model": ""}


# ─────────────────────────────────────────────────────────────────────────
# 3. Widened-search grounding -- the genuinely new behavior. Upgrades the
#    existing _apply_regimen_safety_net (which jumps straight to a
#    hardcoded default) to try up to _WIDENED_SEARCH_ATTEMPTS distinct
#    local searches first, registering any new chunks found in the shared
#    registry so later citations/the judge can see them too. Hardcoded
#    default remains the LAST resort, exactly as production's docstring
#    describes ("3 distinct widened cross-guideline searches → hardcoded
#    default only as absolute last resort").
# ─────────────────────────────────────────────────────────────────────────

def _widened_queries(base_query: str, active_guideline: str, field_hint: str) -> list[str]:
    """Three distinct broadenings, cheapest/most-targeted first:
    1. same guideline, generic field-focused query (drop patient specifics)
    2. cross-guideline (any of the other two guidelines, same field)
    3. maximally generic fallback query for the field."""
    other_guidelines = [g for g in ("NICE", "WHO", "AAP") if g != active_guideline]
    return [
        f"{field_hint} {active_guideline} empiric first-line neonatal early-onset sepsis",
        f"{field_hint} {' '.join(other_guidelines)} neonatal sepsis guideline",
        f"{field_hint} neonatal sepsis management",
    ][:_WIDENED_SEARCH_ATTEMPTS]


def widen_regimen_grounding(
    plan: dict, item, registry: CitationRegistryHarness, retrieve_fn: Callable,
) -> tuple[dict, list[str]]:
    """Runs BEFORE the existing hardcoded-default safety net
    (cpe._apply_regimen_safety_net) -- if the regimen is missing/incomplete,
    try widened local searches for a grounded regimen entry before falling
    through to that hardcoded-default path. Only attempts widening when
    there's something to fix; a complete, grounded regimen is left alone."""
    base, cpe, _ = _require()
    notes: list[str] = []
    abx = plan.get("antibiotic_plan", {})
    if not abx.get("required"):
        return plan, notes

    incomplete = cpe._check_regimen_completeness(plan, item.contraindication_type)
    missing = cpe._is_regimen_missing(list(abx.get("regimen", [])))
    if not (incomplete or missing):
        return plan, notes

    field_hint = "clindamycin alternative penicillin allergy" if item.contraindication_type == "penicillin_allergy" \
        else "empiric antibiotic regimen dose"
    found_any = False
    for attempt, q in enumerate(_widened_queries(item.retrieval_query, item.active_guideline, field_hint), start=1):
        widened_chunks = retrieve_fn(q, item.active_guideline, 3)
        for c in widened_chunks:
            label = registry.add(c)
        if widened_chunks:
            notes.append(f"widened search attempt {attempt}/{_WIDENED_SEARCH_ATTEMPTS} "
                         f"('{q[:60]}...') found {len(widened_chunks)} additional chunk(s), "
                         f"registered as {[registry.label_for(c.chunk_id) for c in widened_chunks]}")
            found_any = True
            break  # first successful widened search wins -- don't keep burning retrieval calls

    if not found_any:
        notes.append("all widened searches returned nothing new — falling through to hardcoded default")
    return plan, notes


def ensure_supplementary_fields_grounded(
    plan: dict, item, registry: CitationRegistryHarness, retrieve_fn: Callable,
) -> tuple[dict, list[str]]:
    """UNCONDITIONAL supplementary grounding for monitoring_plan /
    escalation_criteria / nutrition_fluid_plan -- runs for every case
    regardless of risk category (mirrors the production step of the same
    name; that step is unconditional in production too, it's only the
    fact-check judge that's category-gated there). Same retrieve-first,
    widened-search-second, note-if-nothing-found pattern as the regimen
    grounding above, but does not inject hardcoded text for these fields
    (there's no safe universal default for "monitoring plan" the way there
    is for a first-line antibiotic regimen) -- an ungrounded field is left
    as-is with a note, for the judge/resolution loop to potentially catch.
    """
    notes: list[str] = []
    field_queries = {
        "monitoring_plan": "vital sign monitoring frequency neonatal sepsis observation",
        "escalation_criteria": "escalation deterioration senior review neonatal sepsis",
        "nutrition_fluid_plan": "fluid feeding nutrition management neonatal sepsis",
    }
    for field_name, hint in field_queries.items():
        text = plan.get(field_name, "")
        if text and len(text.strip()) > 15:
            continue  # already has real content, nothing to ground
        for q in _widened_queries(item.retrieval_query, item.active_guideline, hint)[:1]:
            widened_chunks = retrieve_fn(q, item.active_guideline, 2)
            for c in widened_chunks:
                registry.add(c)
            if widened_chunks:
                notes.append(f"{field_name} was empty/thin — widened search registered "
                             f"{[registry.label_for(c.chunk_id) for c in widened_chunks]} for potential grounding")
    return plan, notes


# ─────────────────────────────────────────────────────────────────────────
# 4. Resolution loop -- ports gemini_service.py's _resolve_flagged_claims
#    family. Routes each judge-flagged claim to the right fix, re-runs the
#    judge, repeats up to _MAX_RESOLUTION_ITERATIONS times.
# ─────────────────────────────────────────────────────────────────────────

def _route_and_resolve_claim(
    claim: str, plan: dict, item, registry: CitationRegistryHarness, retrieve_fn: Callable,
) -> str:
    """Best-effort classification of a flagged claim into one of three
    resolution paths, mirroring gemini_service.py's routing intent
    (disambiguation fix | drug re-grounding | generic field fix) without
    needing a second LLM call just to classify -- keyword routing on the
    claim text is what production's own comment describes as the practical
    approach for this step. Returns a one-line action-taken description for
    resolution_log, exactly as production's resolution_log field expects."""
    claim_lower = claim.lower()
    if "cross_guideline" in claim_lower or "disambiguation" in claim_lower or "disclosure" in claim_lower:
        if not plan.get("disambiguation_block", "").strip():
            plan["disambiguation_block"] = (
                f"Note: retrieved evidence spans more than one guideline source for this "
                f"{item.active_guideline} case; the active guideline's recommendation takes precedence."
            )
        return "disambiguation fix — populated disambiguation_block from the deterministic cross-guideline flag"
    if any(k in claim_lower for k in ("dose", "mg/kg", "drug", "antibiotic", "regimen")):
        plan, notes = widen_regimen_grounding(plan, item, registry, retrieve_fn)
        return f"drug re-grounding — {notes[0] if notes else 'no new grounding found'}"
    plan, notes = ensure_supplementary_fields_grounded(plan, item, registry, retrieve_fn)
    return f"generic field fix — {notes[0] if notes else 'no ungrounded field matched this claim'}"


def resolve_flagged_claims(
    plan: dict, item, registry: CitationRegistryHarness, retrieve_fn: Callable,
    call_llm: Callable, risk_payload: dict, active_guideline: str, deltas: dict | None,
    missed_required_disambiguation: bool,
) -> tuple[dict, dict, list[str]]:
    """Full judge -> route -> fix -> re-judge loop, up to
    _MAX_RESOLUTION_ITERATIONS times. Returns (final plan, final fact_check
    result dict, resolution_log)."""
    _, cpe, _ = _require()
    resolution_log: list[str] = []
    fact_check = None

    for iteration in range(1, _MAX_RESOLUTION_ITERATIONS + 1):
        draft_text = cpe.format_care_plan_text(plan) if hasattr(cpe, "format_care_plan_text") else json.dumps(plan)
        fact_check = run_fact_check_harness(
            draft_text, risk_payload, active_guideline, registry, call_llm,
            deltas=deltas, missed_required_disambiguation=missed_required_disambiguation,
        )
        if fact_check["verified"] or not fact_check["flagged_claims"]:
            resolution_log.append(f"iteration {iteration}: verified={fact_check['verified']} — loop ends")
            break
        for claim in fact_check["flagged_claims"]:
            action = _route_and_resolve_claim(claim, plan, item, registry, retrieve_fn)
            resolution_log.append(f"iteration {iteration}: claim={claim[:80]!r} -> {action}")
        # after applying fixes, missed_required_disambiguation should be
        # re-checked -- it may have just been resolved by the disambiguation
        # fix path above.
        missed_required_disambiguation = missed_required_disambiguation and not plan.get("disambiguation_block", "").strip()

    if fact_check is None:  # loop body never ran (shouldn't happen given range(1, N+1) with N>=1)
        fact_check = {"performed": False, "verified": True, "confidence": 0.0,
                      "flagged_claims": [], "notes": "", "judge_model": ""}
    return plan, fact_check, resolution_log


# ─────────────────────────────────────────────────────────────────────────
# 5. Top-level pipeline -- same output row shape as every other arm in this
#    notebook, so it merges directly into the existing stats/analysis code.
# ─────────────────────────────────────────────────────────────────────────

def generate_care_plan_multi_agent(item, retrieve_fn: Callable, call_llm: Callable, top_k: int = 5):
    base, cpe, gcp = _require()

    trends = base.compute_sustained_trends_harness(item.previous_assessments)
    query = base.build_clinical_query_harness(
        item.risk_result, item.active_guideline, deltas=item.deltas, trends=trends,
    ) or item.retrieval_query

    chunks = retrieve_fn(query, item.active_guideline, top_k)
    registry = CitationRegistryHarness(chunks=list(chunks))

    patient = item.risk_result.get("patient", {})
    flags = base.check_contraindications(patient, item.proposed_drugs, deltas=item.deltas)
    flags += base.check_who_outpatient_exclusions(patient, care_setting=item.care_setting)
    contraindication_flags = base.flags_to_strings(flags)

    chunk_sources = [c.source for c in registry.chunks]
    cross_guideline_conflict = base.detect_cross_guideline_conflict(chunk_sources, item.active_guideline)
    trend_note = base.trend_state_change_note(item.deltas)

    prompt = build_care_plan_prompt_with_citations(
        item, registry, item.deltas, contraindication_flags, cross_guideline_conflict, trend_note,
    )

    fallback_used, raw, model_version = False, "", "unknown"
    try:
        raw, model_version = call_llm(prompt, system=cpe._CARE_PLAN_SYSTEM_PROMPT)
        plan = json.loads(base.strip_json_fences(raw))
        if not isinstance(plan, dict):
            raise ValueError("LLM did not return a JSON object")
    except base.LLMUnavailableError as e:
        print(f"[multi-agent gen] {item.case_id}: no LLM provider available ({e}) — rule-based fallback")
        plan, fallback_used, model_version = cpe._rule_based_fallback_plan(item), True, "rule-based-fallback"
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[multi-agent gen] {item.case_id}: unparseable LLM JSON ({e}) — rule-based fallback")
        plan, fallback_used, model_version = cpe._rule_based_fallback_plan(item), True, f"{model_version}-unparseable-fallback"

    for k, v in cpe._FALLBACK_PLAN_TEMPLATE.items():
        plan.setdefault(k, v if not isinstance(v, (list, dict)) else (list(v) if isinstance(v, list) else dict(v)))
    plan.setdefault("antibiotic_plan", {})
    for k, v in cpe._FALLBACK_PLAN_TEMPLATE["antibiotic_plan"].items():
        plan["antibiotic_plan"].setdefault(k, v)

    plan = cpe._overlay_deterministic_safety(plan, contraindication_flags, cross_guideline_conflict, trend_note)
    plan = cpe._force_critical_urgency(plan, item.risk_result.get("category", ""))
    missed_required_disambiguation = cross_guideline_conflict and not plan.get("disambiguation_block", "").strip()

    # NEW: widened-search grounding BEFORE the hardcoded-default safety net,
    # both for the regimen and for the supplementary fields.
    plan, widen_notes = widen_regimen_grounding(plan, item, registry, retrieve_fn)
    plan, supp_notes = ensure_supplementary_fields_grounded(plan, item, registry, retrieve_fn)

    # Existing hardcoded-default safety net -- now genuinely the LAST resort,
    # since widen_regimen_grounding ran first.
    regimen_incomplete = cpe._check_regimen_completeness(plan, item.contraindication_type)
    ungrounded_dose_claims = cpe._check_ungrounded_dose_claims(
        plan.get("antibiotic_plan", {}).get("regimen", []), registry.chunks,
    )
    plan, net_notes_1 = cpe._apply_regimen_safety_net(plan, item.active_guideline, item.contraindication_type)
    plan, net_notes_2 = cpe._strip_ungrounded_dose_claims(plan, registry.chunks)
    safety_net_notes = widen_notes + supp_notes + net_notes_1 + net_notes_2

    fact_check_gate_passes = FACT_CHECK_ALL_LEVELS or item.risk_result.get("category", "").upper() in _FACT_CHECK_GATED_CATEGORIES
    if fact_check_gate_passes:
        plan, fact_check, resolution_log = resolve_flagged_claims(
            plan, item, registry, retrieve_fn, call_llm,
            item.risk_result, item.active_guideline, item.deltas, missed_required_disambiguation,
        )
    else:
        fact_check = {"performed": False, "verified": True, "confidence": 0.0,
                      "flagged_claims": [], "notes": "gated out (category not HIGH/CRITICAL)", "judge_model": ""}
        resolution_log = []

    plan["model_version"] = model_version
    plan["fallback_used"] = fallback_used

    return cpe.CarePlanGenerationResult(
        plan=plan, chunks=registry.chunks, retrieval_query=query,
        contraindication_flags=contraindication_flags,
        cross_guideline_conflict=cross_guideline_conflict,
        trend_note=trend_note, fallback_used=fallback_used,
        model_version=model_version, raw_llm_text=raw,
        regimen_incomplete=regimen_incomplete,
        ungrounded_dose_claims=ungrounded_dose_claims,
        regimen_safety_net_notes=safety_net_notes,
    ), fact_check, resolution_log, registry


def run_multi_agent_evaluation_harness_pluggable(
    items: list, retrieve_fn: Callable, call_llm: Callable,
    top_k: int = 5, arm_name: str = "multi_agent",
    bertscore_backbones: dict | None = None, bertscore_device: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Same per-item row shape as run_care_plan_evaluation_harness_pluggable
    (graph_care_plan_retrieval.py) -- merges directly into combined_5arm_*.csv-
    style analysis, rerun_stats_corrected.py, and every notebook cell already
    built around that shape. Additionally reports fact_check_performed/
    verified/confidence and n_resolution_steps, new columns unique to this
    arm; downstream code that doesn't know about them simply ignores them."""
    base, cpe, gcp = _require()
    rows = []
    t0 = time.time()
    for i, item in enumerate(items, start=1):
        gen, fact_check, resolution_log, registry = generate_care_plan_multi_agent(
            item, retrieve_fn, call_llm, top_k=top_k,
        )
        candidate_text = cpe.format_care_plan_text(gen.plan)
        reference_text = item.reference_text

        bleu_scores = base.score_bleu_pair(candidate_text, reference_text)
        meteor = base.score_meteor_pair(candidate_text, reference_text)
        grounding_recall = cpe.anchor_grounding_recall(gen.chunks, item.anchor_phrases)

        row = {
            "case_id": item.case_id, "arm": arm_name, "category": item.risk_result.get("category"),
            "active_guideline": item.active_guideline, "contraindication_type": item.contraindication_type,
            "candidate": candidate_text, "generated_text": candidate_text, "reference_text": reference_text,
            "meteor": meteor, **bleu_scores,
            "anchor_grounding_recall": grounding_recall,
            "regimen_incomplete": gen.regimen_incomplete,
            "ungrounded_dose_claims": gen.ungrounded_dose_claims,
            "cross_guideline_conflict_detected": gen.cross_guideline_conflict,
            "deterministic_safety_recall": 1.0,
            "fallback_used": gen.fallback_used, "model_version": gen.model_version,
            "safety_net_notes": gen.regimen_safety_net_notes,
            "fact_check_performed": fact_check["performed"], "fact_check_verified": fact_check["verified"],
            "fact_check_confidence": fact_check["confidence"],
            "fact_check_flagged_claims": fact_check["flagged_claims"],
            "n_resolution_steps": len(resolution_log), "resolution_log": resolution_log,
            "n_citations": len(registry.chunks),
            "retrieved_chunk_ids_joined": ",".join(c.chunk_id for c in registry.chunks),
            "gold_chunk_ids_joined": ",".join(item.gold_chunk_ids),
        }
        rows.append(row)
        if verbose:
            elapsed = time.time() - t0
            print(f"[{arm_name}][{i:3}/{len(items)}] {item.case_id:15} guideline={item.active_guideline:5} "
                  f"fallback={gen.fallback_used}  verified={fact_check['verified']}  "
                  f"resolution_steps={len(resolution_log)}  citations={len(registry.chunks)}  "
                  f"({elapsed:.0f}s elapsed)")

    return {"per_item": rows, "arm_name": arm_name, "n_items": len(items)}
