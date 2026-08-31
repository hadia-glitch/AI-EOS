"""
care_plan_eval_harness.py
==========================

Implements — and evaluates — the NeoGuard **care-plan generation** pipeline
(`POST /api/v1/encounters/{id}/care-plan`, see backend/main.py's
`encounter_care_plan()` -> `generate_care_plan()`), reusing the retrieval /
contraindication / trend infrastructure already built and validated in
`neoguard_harness_improved__3_.py` (RQ1/RQ2/RQ3 harness) rather than
re-deriving it.

Pipeline this module reimplements end-to-end, mirroring main.py exactly:

    risk_result (RiskPayload) + active_guideline + previous_assessments
        │
        ├─ build_clinical_query_harness()              (query construction)
        ├─ retrieve_evidence_harness()                 (hybrid retrieval)
        ├─ check_contraindications() /
        │  check_who_outpatient_exclusions()            (deterministic safety)
        ├─ detect_cross_guideline_conflict()             (deterministic safety)
        ├─ trend_state_change_note()                     (deterministic trend)
        ├─ build_care_plan_prompt() -> call_llm()         (7+-section JSON)
        └─ deterministic overlay: any rule-engine-detected contraindication /
           conflict that the LLM's own JSON omitted is force-appended
           (mirrors fact_check.py's "DETERMINISTIC ALERT ... required
           disclosure failure" check) so a generation-only-safety failure is
           still visible in the scored output rather than silently lost.

Then scores the flattened hypothesis text against a reference care plan with:
  - METEOR                          (score_meteor_pair, from the base harness)
  - BERTScore, 3 backbones          (score_bertscore_batch: roberta-large,
                                      distilbert-base-uncased, deberta-large)
  - RAGAS                           (faithfulness / answer_relevancy /
                                      context_precision / context_recall,
                                      this module — optional, degrades
                                      gracefully if ragas isn't installed)
  - BLEU                            (score_bleu_pair, free — same base harness)

Usage (see the companion notebook for a full worked run):

    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "neoguard_harness_improved__3_", "neoguard_harness_improved__3_.py")
    base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)

    import care_plan_eval_harness as cpe
    cpe.attach_base_harness(base)

    chunks = base.load_corpus_from_csv("chunks_ingested.csv")
    store = base.HarnessChunkStore(chunks, embed_fn=your_embed_fn)
    reranker = your_cross_encoder
    call_llm = base.make_call_llm(mock=True)   # or a real provider

    items = cpe.load_care_plan_dataset("neoguard_care_plan_eval_dataset_v1.json")
    eval_out = cpe.run_care_plan_evaluation_harness(items, store, reranker, call_llm)
    summary = cpe.summarize_care_plan_generation_results(eval_out)
    ragas_out = cpe.run_ragas_evaluation(eval_out["per_item"])   # optional
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, fields as _dataclass_fields
from typing import Any, Callable

import numpy as np

# ─────────────────────────────────────────────────────────────────────────
# 0. Wiring to the base RQ1/RQ2/RQ3 harness (retrieval, contraindications,
#    trends, BLEU/METEOR/BERTScore). Kept as an injected module rather than
#    a hard `import neoguard_harness_improved__3_` so this file works
#    whichever way the base harness ends up on disk/sys.path (plain import,
#    importlib.util.spec_from_file_location, or a Colab `%run`).
# ─────────────────────────────────────────────────────────────────────────

_BASE = None  # set by attach_base_harness()


def attach_base_harness(base_module) -> None:
    """Call once with the loaded neoguard_harness_improved__3_ module before
    using anything else in this file."""
    global _BASE
    _BASE = base_module


def _require_base():
    if _BASE is None:
        raise RuntimeError(
            "care_plan_eval_harness needs the base harness attached first — "
            "call attach_base_harness(base_module). See this file's module "
            "docstring for the exact import snippet."
        )
    return _BASE


# ─────────────────────────────────────────────────────────────────────────
# 1. Dataset loading
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CarePlanEvalItem:
    case_id: str
    encounter_id: str
    active_guideline: str
    care_setting: str
    contraindication_type: str | None
    trend_type: str | None
    ambiguous: bool
    risk_result: dict[str, Any]
    previous_assessments: list[dict[str, Any]]
    deltas: dict[str, Any] | None
    proposed_drugs: list[str]
    retrieval_query: str
    gold_chunk_ids: list[str]
    reference_care_plan: dict[str, Any]
    reference_text: str
    anchor_phrases: list[str] = field(default_factory=list)  # v2 dataset field; empty for v1 files


def load_care_plan_dataset(json_path: str) -> list[CarePlanEvalItem]:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    known_fields = {f.name for f in _dataclass_fields(CarePlanEvalItem)}
    items = []
    dropped_keys_seen: set[str] = set()
    for c in data["cases"]:
        extra = set(c.keys()) - known_fields
        if extra:
            dropped_keys_seen |= extra
            c = {k: v for k, v in c.items() if k in known_fields}
        items.append(CarePlanEvalItem(**c))
    if dropped_keys_seen:
        print(f"[care-plan eval] WARNING: ignored unexpected case field(s) not in "
              f"CarePlanEvalItem: {sorted(dropped_keys_seen)}. If these carry information "
              f"you need (e.g. provenance/version metadata), read them separately rather "
              f"than relying on this loader to surface them.")
    print(f"[care-plan eval] loaded {len(items)} cases from {json_path}")
    return items


# ─────────────────────────────────────────────────────────────────────────
# 2. Prompt construction — mirrors the real 7(+)-section
#    ClinicalCarePlanResponse schema (backend/schemas.py) exactly, including
#    the extra safety fields (nutrition_fluid_plan, parent_communication_
#    notes, disambiguation_block, contraindication_flags, trend_state_change)
#    added on top of the original 7-section explanation schema. The system
#    prompt asks for ONLY JSON, matching make_call_llm()'s `want_json`
#    JSON-mode detection in the base harness.
# ─────────────────────────────────────────────────────────────────────────

_CARE_PLAN_SYSTEM_PROMPT = (
    "You are a neonatal early-onset sepsis (EOS) clinical decision-support assistant "
    "generating a structured care plan for a paediatric clinician. Use ONLY the retrieved "
    "guideline excerpts and the deterministic risk/patient/trend data provided below — do not "
    "invent drug names, doses, durations, or thresholds not present in the excerpts. If a "
    "deterministic contraindication or cross-guideline conflict is flagged below, you MUST "
    "surface it in contraindication_flags / disambiguation_block — omitting a flagged safety "
    "issue is a critical failure.\n\n"
    "ANTIBIOTIC REGIMEN SPECIFICITY: each regimen entry must be a full clinical order, not just "
    "a drug name — include route, dose, and frequency exactly as given in the retrieved evidence "
    "(e.g. 'IV benzylpenicillin 25 mg/kg every 12 hours', not just 'Penicillin'). If the "
    "retrieved evidence doesn't give a specific dose/frequency for a drug, say so explicitly "
    "rather than inventing one or dropping the detail silently.\n\n"
    "WITHHELD DRUGS: if a deterministic contraindication means a drug that would otherwise be "
    "indicated is being withheld or substituted, that drug MUST still appear as its own regimen "
    "entry, phrased as '<Drug> — WITHHELD: <short reason>' or '<Drug> — SUBSTITUTED for <other "
    "drug>: <short reason>'. Never simply omit a withheld drug from the list; a clinician reading "
    "only the regimen must be able to tell 'not indicated' apart from 'indicated but withheld for "
    "a documented reason'.\n\n"
    "DO NOT FILL IN A DOSE FROM GENERAL MEDICAL KNOWLEDGE: a specific mg/kg dose or dosing "
    "frequency (e.g. 'every 8 hours', 'once daily') is a clinical fact that MUST come from the "
    "retrieved evidence above, word for word — never from what you generally know about typical "
    "neonatal antibiotic dosing, even if it sounds plausible. Different guidelines, and even the "
    "same guideline at different postnatal ages, specify different numbers for the same drug — "
    "a plausible-sounding number that isn't the one THIS patient's guideline actually specifies is "
    "a patient-safety error, not a harmless approximation. If the retrieved evidence gives a dose "
    "that depends on a condition (e.g. 'every 12 hours in the first week of life, every 8 hours "
    "after') use the age-appropriate branch for THIS patient's stated postnatal age — do not "
    "collapse a conditional dosing table into a single number. If no number for this specific drug "
    "appears anywhere in the retrieved evidence, say so explicitly instead of estimating one.\n\n"
    "Respond with ONLY valid JSON, no markdown fences, no preamble."
)

_CARE_PLAN_JSON_SCHEMA = """{
  "clinical_summary": "<2-3 sentence overview>",
  "risk_analysis": "<why this risk category, grounded in drivers>",
  "trend_narrative": "<how risk has changed across previous_assessments, or empty string>",
  "driver_breakdown": "<per-driver explanation, one line each>",
  "recommended_actions": ["<action 1>", "<action 2>", "..."],
  "antibiotic_plan": {
    "required": <bool>,
    "urgency": "<e.g. Within 1 hour>",
    "regimen": ["<full order: drug + route + dose + frequency, per the system prompt's ANTIBIOTIC REGIMEN SPECIFICITY / WITHHELD DRUGS rules>", "..."],
    "duration": "<e.g. 48-72 hours pending cultures>",
    "stop_criteria": "<criteria to stop antibiotics>"
  },
  "monitoring_plan": "<vitals/labs monitoring cadence>",
  "escalation_criteria": "<when to escalate>",
  "nutrition_fluid_plan": "<feeding/fluid plan>",
  "parent_communication_notes": "<what to tell the family>",
  "disambiguation_block": "<cross-guideline conflict note, or empty string>",
  "contraindication_flags": ["<flag 1>", "..."],
  "trend_state_change": "<deterministic trend note, or empty string>",
  "citation_list": [{"source": "<NICE|AAP|WHO>", "section": "<section>", "chunk_id": "<id>", "similarity_score": <float>}],
  "confidence_disclaimer": "<standard disclaimer>"
}"""


def build_care_plan_prompt(
    item: "CarePlanEvalItem",
    chunks: list,
    deltas: dict | None,
    contraindication_flags: list[str],
    cross_guideline_conflict: bool,
    trend_note: str,
) -> str:
    base = _require_base()
    chunk_block = "\n\n".join(
        f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}" for c in chunks
    ) or "(no guideline chunks were retrieved)"

    risk = item.risk_result
    patient = risk.get("patient", {})
    prev_block = "\n".join(
        f"- {a.get('created_at', '?')}: score={a.get('combined_score', '?')} category={a.get('category', '?')}"
        for a in item.previous_assessments
    ) or "(no previous assessments — first assessment for this encounter)"

    deterministic_block = (
        f"DETERMINISTIC RULE-ENGINE OUTPUT (must be reflected in your JSON, verbatim content may "
        f"be paraphrased but the SAFETY SIGNAL must not be dropped):\n"
        f"- contraindication_flags detected: {contraindication_flags or '(none)'}\n"
        f"- cross_guideline_conflict detected: {cross_guideline_conflict}\n"
        f"- trend_state_change detected: {trend_note or '(none)'}"
    )

    return f"""ACTIVE GUIDELINE: {item.active_guideline}
CARE SETTING: {item.care_setting}

RISK RESULT:
- Category: {risk.get('category')}
- Combined score: {risk.get('total_score')}
- Layer scores: L1={risk.get('layer1_score')} L2={risk.get('layer2_score')} L3={risk.get('layer3_score')}
- Probability per 1000: {risk.get('probability_per_1000')}
- Drivers: {json.dumps(risk.get('drivers', []))}

DE-IDENTIFIED PATIENT SNAPSHOT:
{json.dumps(patient, default=str)}
NOTE: this is an EARLY-ONSET sepsis assessment (within the first 72 hours of life). Where the
retrieved evidence gives an age-conditional dose (e.g. "in the first week of life" vs. "after the
first week of life"), use the FIRST-WEEK-OF-LIFE branch for this patient.

PREVIOUS ASSESSMENTS (oldest first):
{prev_block}

ASSESSMENT DELTAS: {json.dumps(deltas, default=str) if deltas else '(none — first assessment)'}

{deterministic_block}

RETRIEVED GUIDELINE EVIDENCE:
{chunk_block}

Produce a structured clinical care plan as JSON matching EXACTLY this schema (no extra keys, no missing keys):
{_CARE_PLAN_JSON_SCHEMA}"""


# ─────────────────────────────────────────────────────────────────────────
# 3. Deterministic overlay — same "don't let the LLM silently drop a
#    rule-engine-detected safety signal" logic as fact_check.py's
#    DETERMINISTIC ALERT check, applied directly here instead of only at
#    judge time, so a plain (non-judged) run still surfaces it.
# ─────────────────────────────────────────────────────────────────────────

_DRUG_FROM_FLAG_RE = re.compile(r"^\s*\[\w+\]\s*([^:]+):")


def _drug_name_from_deterministic_flag(f: str) -> str:
    """Deterministic flags are always '[SEVERITY] drugname: reason' (see
    contraindication_rules.py's flags_to_strings). Extract just the drug
    name so it can be matched against the LLM's own, differently-worded
    flag text."""
    m = _DRUG_FROM_FLAG_RE.match(f)
    return m.group(1).strip().lower() if m else f.strip().lower()


def _overlay_deterministic_safety(
    plan: dict[str, Any],
    contraindication_flags: list[str],
    cross_guideline_conflict: bool,
    trend_note: str,
) -> dict[str, Any]:
    existing_flags: list[str] = list(plan.get("contraindication_flags") or [])
    existing_blob = " || ".join(existing_flags).lower()

    for f in contraindication_flags:
        drug = _drug_name_from_deterministic_flag(f)
        # If the LLM already mentioned this drug in ANY of its own flags
        # (however it phrased it), the safety signal is already surfaced --
        # don't append a second, near-duplicate line about the same drug.
        # If it did NOT, append the deterministic version so the signal is
        # never silently dropped (this is the actual safety requirement;
        # dedup is purely a presentation fix on top of it).
        if drug and drug in existing_blob:
            continue
        existing_flags.append(f)
        existing_blob += " || " + f.lower()

    plan["contraindication_flags"] = existing_flags

    if cross_guideline_conflict and not (plan.get("disambiguation_block") or "").strip():
        plan["disambiguation_block"] = (
            "[DETERMINISTIC] Retrieved evidence spans >=2 off-guideline sources — "
            "recommendations may differ across guideline frameworks for this presentation."
        )

    if trend_note and not (plan.get("trend_state_change") or "").strip():
        plan["trend_state_change"] = trend_note

    return plan


_FALLBACK_PLAN_TEMPLATE: dict[str, Any] = {
    "clinical_summary": "", "risk_analysis": "", "trend_narrative": "", "driver_breakdown": "",
    "recommended_actions": [], "antibiotic_plan": {"required": False, "urgency": "Within 1 hour",
                                                      "regimen": [], "duration": "", "stop_criteria": ""},
    "monitoring_plan": "", "escalation_criteria": "", "nutrition_fluid_plan": "",
    "parent_communication_notes": "", "disambiguation_block": "", "contraindication_flags": [],
    "trend_state_change": "", "citation_list": [],
    "confidence_disclaimer": "Rule-based fallback — LLM generation was unavailable or unparseable.",
}


def _rule_based_fallback_plan(item: "CarePlanEvalItem") -> dict[str, Any]:
    """Mirrors generate_care_plan()'s documented behaviour of degrading to a
    rule-based answer (fallback_used=True) rather than raising, when every
    LLM provider fails or returns unparseable JSON."""
    risk = item.risk_result
    plan = dict(_FALLBACK_PLAN_TEMPLATE)
    plan["clinical_summary"] = (
        f"[FALLBACK] {risk.get('category')} risk (score {risk.get('total_score')}) under "
        f"{item.active_guideline}. LLM generation unavailable — rule-based summary only."
    )
    plan["risk_analysis"] = f"Deterministic category: {risk.get('risk_category')}."
    plan["driver_breakdown"] = "\n".join(
        f"- {d.get('name')}: {d.get('reason')}" for d in risk.get("drivers", [])
    )
    required = risk.get("category") in ("HIGH", "CRITICAL")
    plan["antibiotic_plan"]["required"] = required
    if required:
        plan["recommended_actions"] = ["Initiate empiric antibiotics per active guideline.",
                                        "Obtain blood culture before first dose."]
    else:
        plan["recommended_actions"] = ["Continue observation per active guideline."]
    plan["monitoring_plan"] = "Standard vital sign and inflammatory marker monitoring."
    plan["escalation_criteria"] = "Escalate on any clinical deterioration or rising risk score."
    return plan


# ─────────────────────────────────────────────────────────────────────────
# 4. End-to-end generation for one case
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CarePlanGenerationResult:
    plan: dict[str, Any]
    chunks: list
    retrieval_query: str
    contraindication_flags: list[str]
    cross_guideline_conflict: bool
    trend_note: str
    fallback_used: bool
    model_version: str
    raw_llm_text: str = ""
    regimen_incomplete: bool = False          # required 2nd-line agent silently missing (BEFORE safety net)
    ungrounded_dose_claims: list[str] = field(default_factory=list)  # numeric doses not found in retrieved evidence (BEFORE safety net)
    regimen_safety_net_notes: list[str] = field(default_factory=list)  # corrections actually applied


# ─────────────────────────────────────────────────────────────────────────
# 3b. Additional deterministic checks added after reviewing a real run's
#     output (see the harness's own dosing/completeness findings): urgency
#     wording, regimen completeness, and numeric-dose grounding are all
#     things a clinical system should verify deterministically rather than
#     trust an LLM to phrase consistently, the same way contraindication
#     flags and cross-guideline conflicts already are.
# ─────────────────────────────────────────────────────────────────────────

_FIRST_LINE_PAIRS = {
    "gentamicin": ("benzylpenicillin", "ampicillin", "penicillin"),
    "benzylpenicillin": ("gentamicin",),
    "ampicillin": ("gentamicin",),
}
_DOSE_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?\s*mg/kg(?:/dose)?|\bevery\s+\d+\s*hours?\b|\bonce\s+(?:a\s+)?daily\b", re.I)


def _force_critical_urgency(plan: dict[str, Any], category: str) -> dict[str, Any]:
    """urgency is a controlled field with real clinical meaning (how fast a
    dose must be given), not free text -- don't let LLM phrasing variance
    (observed: every CRITICAL case still said the same 'Within 1 hour' as
    HIGH cases) blur a distinction the app's own reference logic makes."""
    abx = plan.get("antibiotic_plan", {})
    if abx.get("required") and category == "CRITICAL":
        abx["urgency"] = "Immediate (within 1 hour)"
    elif abx.get("required") and not abx.get("urgency"):
        abx["urgency"] = "Within 1 hour"
    plan["antibiotic_plan"] = abx
    return plan


def _check_regimen_completeness(plan: dict[str, Any], contraindication_type: str | None) -> bool:
    """Flags a beta-lactam present without its paired aminoglycoside (or
    vice versa) when antibiotics are required and there's no documented
    contraindication explaining the gap -- e.g. cp_case_022 in a real run:
    NICE CRITICAL, blood-culture-positive, generated benzylpenicillin
    monotherapy with gentamicin silently absent (not withheld, not
    substituted -- just missing). contraindication_flags already covers
    drugs deliberately withheld for a documented reason; this covers drugs
    that go missing for no stated reason at all, which is a different and
    arguably worse failure (a clinician has no flag telling them to look
    for it)."""
    abx = plan.get("antibiotic_plan", {})
    if not abx.get("required"):
        return False
    regimen_blob = " || ".join(abx.get("regimen", [])).lower()
    present = {drug for drug in _FIRST_LINE_PAIRS if drug in regimen_blob}
    if not present:
        return False
    for drug in present:
        for partner in _FIRST_LINE_PAIRS[drug]:
            if partner in regimen_blob:
                return False  # a valid pairing was found somewhere in the regimen
    # a first-line drug is present with none of its expected partners anywhere
    # in the regimen text, AND this isn't the documented gentamicin-AKI case
    # (where gentamicin is EXPECTED to be the missing/withheld one)
    if contraindication_type == "gentamicin_aki":
        return False
    return True


def _check_ungrounded_dose_claims(regimen: list[str], chunks: list) -> list[str]:
    """Extracts numeric dose/frequency tokens (e.g. '5 mg/kg', 'every 8
    hours', 'once daily') from the generated regimen and flags any that
    don't appear verbatim in the retrieved chunk text -- i.e. a specific,
    confident-looking number the model stated without it actually being in
    front of it. Verbatim substring matching is deliberately strict (not
    fuzzy): a real run showed the model can be numerically confident AND
    wrong (gentamicin '2.5 mg/kg every 12 hours' vs. the real WHO corpus's
    '5 mg/kg once a day' for the exact same age bracket) -- a paraphrase-
    tolerant check would have missed exactly that error."""
    if not regimen:
        return []
    context_blob = " ".join(c.chunk_text for c in chunks)
    context_blob = " ".join(context_blob.replace("\xa0", " ").split()).lower()
    flagged = []
    for entry in regimen:
        for tok in _DOSE_TOKEN_RE.findall(entry):
            tok_norm = " ".join(tok.replace("\xa0", " ").split()).lower()
            if tok_norm not in context_blob:
                flagged.append(f"'{tok.strip()}' in \"{entry[:80]}\"")
    return flagged


# ─────────────────────────────────────────────────────────────────────────
# 3c. Regimen safety net — ACTIVE correction, not just detection.
#
# A real run showed detection alone isn't enough: cases with
# `regimen_incomplete=True` still shipped monotherapy, because flagging a
# gap doesn't fix it if nothing acts on the flag. This mirrors
# _overlay_deterministic_safety's existing "verify AND fix" pattern
# (contraindication_flags) and extends the same approach to three more
# regimen failure modes found in real runs:
#   1. No regimen at all ("No specific antibiotic regimen is provided")
#      for a category that requires one -- injects the full guideline-
#      appropriate default.
#   2. A first-line agent present with its expected partner missing and no
#      contraindication explaining the gap -- injects just the missing
#      partner.
#   3. Penicillin allergy without a Gram-positive-covering alternative
#      (clindamycin) actually present -- injects it.
#   4. An ungrounded numeric dose claim -- strips the specific number and
#      replaces it with an explicit "not specified in retrieved evidence"
#      note, rather than leaving a confident-looking wrong number in a
#      clinician-facing plan.
#   5. WHO cases naming benzylpenicillin (NICE's drug, not WHO's) --
#      corrected to ampicillin, WHO's actual first-line agent.
#
# Every correction is tagged '[DETERMINISTIC DEFAULT]' / '[DETERMINISTIC
# CORRECTION]' in the regimen text itself -- this is a deliberate
# transparency choice: a clinician reading the plan must be able to tell
# "the model produced this" from "the rule engine overrode this",
# especially for #1, where the rule engine is now effectively prescribing
# rather than just checking. That's a real escalation in what this
# component does, not a cosmetic fix -- see the harness's module docstring
# discussion of this tradeoff.
# ─────────────────────────────────────────────────────────────────────────

_REGIMEN_PLACEHOLDER_PHRASES = (
    "no specific antibiotic regimen", "regimen not provided", "not provided in",
    "no antibiotic regimen", "regimen not available",
)
_KNOWN_DRUG_WORDS = ("gentamicin", "ampicillin", "benzylpenicillin", "penicillin", "clindamycin", "cefotaxime")

# Corpus-verified defaults -- identical figures to build_care_plan_dataset.py's
# _regimen_for() (kept in sync manually; both were checked directly against
# chunks_ingested.csv before hardcoding anything here -- see that file's
# comments for the grep results this is based on).
_WHO_AMPICILLIN_DOSE = "50 mg/kg/dose every 12 hours (first week of life)"
_WHO_GENTAMICIN_DOSE = "5 mg/kg once daily (first week of life)"

_DEFAULT_REGIMEN_BY_GUIDELINE: dict[str, list[str]] = {
    "WHO": [f"[DETERMINISTIC DEFAULT] Ampicillin IM/IV {_WHO_AMPICILLIN_DOSE}, for at least 10 days",
            f"[DETERMINISTIC DEFAULT] Gentamicin IM/IV {_WHO_GENTAMICIN_DOSE}, for at least 10 days"],
    "AAP": ["[DETERMINISTIC DEFAULT] IV ampicillin (dose per institutional neonatal formulary "
            "— not specified in retrieved evidence)",
            "[DETERMINISTIC DEFAULT] IV gentamicin (dose per institutional neonatal formulary "
            "— not specified in retrieved evidence)"],
    "NICE": ["[DETERMINISTIC DEFAULT] IV benzylpenicillin sodium (dose per NICE-specified "
             "weight-based protocol — not specified in retrieved evidence)",
             "[DETERMINISTIC DEFAULT] IV gentamicin (dose per NICE-specified weight-based "
             "protocol — not specified in retrieved evidence)"],
}
_DEFAULT_PARTNER_ENTRY: dict[str, dict[str, str]] = {
    "WHO": {"ampicillin": _DEFAULT_REGIMEN_BY_GUIDELINE["WHO"][1],
            "gentamicin": _DEFAULT_REGIMEN_BY_GUIDELINE["WHO"][0]},
    "AAP": {"ampicillin": _DEFAULT_REGIMEN_BY_GUIDELINE["AAP"][1],
            "gentamicin": _DEFAULT_REGIMEN_BY_GUIDELINE["AAP"][0]},
    "NICE": {"benzylpenicillin": _DEFAULT_REGIMEN_BY_GUIDELINE["NICE"][1],
             "penicillin": _DEFAULT_REGIMEN_BY_GUIDELINE["NICE"][1],
             "gentamicin": _DEFAULT_REGIMEN_BY_GUIDELINE["NICE"][0]},
}
_CLINDAMYCIN_DEFAULT = ("[DETERMINISTIC DEFAULT] IV clindamycin (per documented penicillin "
                        "allergy — provides Group B Streptococcus coverage in place of the "
                        "withheld beta-lactam)")


def _is_regimen_missing(regimen: list[str]) -> bool:
    if not regimen:
        return True
    blob = " || ".join(regimen).lower()
    has_drug_word = any(w in blob for w in _KNOWN_DRUG_WORDS)
    has_placeholder = any(p in blob for p in _REGIMEN_PLACEHOLDER_PHRASES)
    return has_placeholder and not has_drug_word


def _apply_regimen_safety_net(
    plan: dict[str, Any],
    guideline: str,
    contraindication_type: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Returns (possibly-modified plan, list of correction notes applied).
    Idempotent-ish: safe to call once per generation; does nothing if the
    regimen already looks complete and grounded."""
    notes: list[str] = []
    abx = plan.get("antibiotic_plan", {})
    if not abx.get("required"):
        return plan, notes

    regimen = list(abx.get("regimen", []))

    # 1. Nothing there at all -> inject the full default.
    if _is_regimen_missing(regimen):
        regimen = list(_DEFAULT_REGIMEN_BY_GUIDELINE.get(guideline, _DEFAULT_REGIMEN_BY_GUIDELINE["NICE"]))
        notes.append(f"regimen was missing entirely — injected {guideline} default two-agent regimen")
        if contraindication_type == "penicillin_allergy":
            # swap the beta-lactam default entry for clindamycin
            regimen = [e for e in regimen if "ampicillin" not in e.lower() and "benzylpenicillin" not in e.lower()]
            regimen.insert(0, _CLINDAMYCIN_DEFAULT)
            notes.append("penicillin-allergy case — substituted clindamycin for the beta-lactam default")

    else:
        # 2. Partner-drug gap (not explained by a contraindication).
        if _check_regimen_completeness({"antibiotic_plan": {"required": True, "regimen": regimen}},
                                        contraindication_type):
            blob = " || ".join(regimen).lower()
            present_drug = next((d for d in _DEFAULT_PARTNER_ENTRY.get(guideline, {}) if d in blob), None)
            if present_drug:
                # _check_regimen_completeness already confirmed no partner
                # drug is present anywhere in the regimen text, so this is
                # safe to append without a further dedup check.
                partner_entry = _DEFAULT_PARTNER_ENTRY[guideline][present_drug]
                regimen.append(partner_entry)
                notes.append(f"'{present_drug}' present without its expected partner drug — "
                             f"injected the missing agent")

        # 3. Penicillin allergy without an actual alternative present.
        if contraindication_type == "penicillin_allergy":
            blob = " || ".join(regimen).lower()
            if "clindamycin" not in blob:
                regimen.append(_CLINDAMYCIN_DEFAULT)
                notes.append("penicillin allergy documented but no Gram-positive-covering "
                             "alternative was present — injected clindamycin")

    # 5. WHO drug-name correction: benzylpenicillin is NICE's first-line
    # agent, not WHO's -- a guideline-identity fact, not something that
    # should depend on retrieval quality.
    if guideline == "WHO":
        corrected = []
        who_name_fixed = False
        for e in regimen:
            if re.search(r"\bbenzylpenicillin(?:\s+sodium)?\b", e, re.I) and "ampicillin" not in e.lower():
                e = re.sub(r"\bbenzylpenicillin(?:\s+sodium)?\b", "ampicillin", e, flags=re.I)
                who_name_fixed = True
            corrected.append(e)
        regimen = corrected
        if who_name_fixed:
            notes.append("WHO case named benzylpenicillin (NICE's agent) — corrected to "
                         "ampicillin (WHO's actual first-line agent)")

    abx["regimen"] = regimen
    plan["antibiotic_plan"] = abx
    return plan, notes


def _strip_ungrounded_dose_claims(plan: dict[str, Any], chunks: list) -> tuple[dict[str, Any], list[str]]:
    """Replaces any regimen entry containing a numeric dose/frequency token
    not found in the retrieved evidence with an explicit 'not specified in
    retrieved evidence' note instead of leaving a confident, ungrounded
    number in a clinician-facing plan. Runs AFTER _apply_regimen_safety_net
    so newly-injected default entries (which never claim a fabricated
    number in the first place) aren't touched."""
    abx = plan.get("antibiotic_plan", {})
    regimen = list(abx.get("regimen", []))
    if not regimen:
        return plan, []

    context_blob = " ".join(c.chunk_text for c in chunks)
    context_blob = " ".join(context_blob.replace("\xa0", " ").split()).lower()

    notes = []
    cleaned = []
    for entry in regimen:
        stripped_tokens = []
        new_entry = entry
        for tok in _DOSE_TOKEN_RE.findall(entry):
            tok_norm = " ".join(tok.replace("\xa0", " ").split()).lower()
            if tok_norm not in context_blob:
                stripped_tokens.append(tok)
                new_entry = new_entry.replace(tok, "[dose/frequency not specified in retrieved evidence]")
        if stripped_tokens:
            notes.append(f"stripped ungrounded {stripped_tokens} from \"{entry[:60]}\"")
        cleaned.append(new_entry)

    abx["regimen"] = cleaned
    plan["antibiotic_plan"] = abx
    return plan, notes


def generate_care_plan_harness(
    item: "CarePlanEvalItem",
    store,
    reranker,
    call_llm: Callable,
    top_k: int = 5,
    retrieval_top_k: int = 10,
    use_mmr: bool = True,
    # Matches backend/config.py's real production `guideline_nudge_weight`
    # default (0.15), NOT retrieve_evidence_harness()'s own internal default
    # (0.05) -- the v1 run was silently using the weaker 0.05 value because
    # nothing here overrode it, which likely contributed to
    # detect_cross_guideline_conflict firing on 23/30 cases (vs. the
    # dataset's ~4-8 intended genuinely-ambiguous cases): active-guideline
    # content wasn't being preferred strongly enough over generic,
    # semantically-similar off-guideline content in a near-tie.
    guideline_nudge_weight: float = 0.15,
) -> CarePlanGenerationResult:
    base = _require_base()
    risk = item.risk_result

    # ── trends (streak-aware, mirrors patient_context_builder.py) ─────────
    trends = base.compute_sustained_trends_harness(item.previous_assessments)

    # ── query + retrieval (mirrors main.py's encounter_care_plan()) ───────
    query = base.build_clinical_query_harness(
        risk, item.active_guideline, deltas=item.deltas, trends=trends,
    ) or item.retrieval_query
    chunks = base.retrieve_evidence_harness(
        store, query, item.active_guideline, reranker,
        top_k=top_k, retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
        guideline_nudge_weight=guideline_nudge_weight,
    )

    # ── deterministic safety checks (contraindication_rules.py mirror) ────
    patient = risk.get("patient", {})
    flags = base.check_contraindications(patient, item.proposed_drugs, deltas=item.deltas)
    flags += base.check_who_outpatient_exclusions(patient, care_setting=item.care_setting)
    contraindication_flags = base.flags_to_strings(flags)

    chunk_sources = [c.source for c in chunks]
    cross_guideline_conflict = base.detect_cross_guideline_conflict(chunk_sources, item.active_guideline)
    trend_note = base.trend_state_change_note(item.deltas)

    # ── LLM generation ─────────────────────────────────────────────────────
    prompt = build_care_plan_prompt(
        item, chunks, item.deltas, contraindication_flags, cross_guideline_conflict, trend_note,
    )
    fallback_used = False
    raw = ""
    model_version = "unknown"
    try:
        raw, model_version = call_llm(prompt, system=_CARE_PLAN_SYSTEM_PROMPT)
        plan = json.loads(base.strip_json_fences(raw))
        if not isinstance(plan, dict):
            raise ValueError("LLM did not return a JSON object")
    except base.LLMUnavailableError as e:
        print(f"[care-plan gen] {item.case_id}: no LLM provider available ({e}) — rule-based fallback")
        plan = _rule_based_fallback_plan(item)
        fallback_used = True
        model_version = "rule-based-fallback"
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[care-plan gen] {item.case_id}: unparseable LLM JSON ({e}) — rule-based fallback")
        plan = _rule_based_fallback_plan(item)
        fallback_used = True
        model_version = f"{model_version}-unparseable-fallback"

    # fill any keys the LLM omitted so downstream formatting never KeyErrors
    for k, v in _FALLBACK_PLAN_TEMPLATE.items():
        plan.setdefault(k, v if not isinstance(v, (list, dict)) else (list(v) if isinstance(v, list) else dict(v)))
    plan.setdefault("antibiotic_plan", {})
    for k, v in _FALLBACK_PLAN_TEMPLATE["antibiotic_plan"].items():
        plan["antibiotic_plan"].setdefault(k, v)

    plan = _overlay_deterministic_safety(plan, contraindication_flags, cross_guideline_conflict, trend_note)
    plan = _force_critical_urgency(plan, item.risk_result.get("category", ""))
    plan["model_version"] = model_version
    plan["fallback_used"] = fallback_used

    # Detect BEFORE correcting, so eval_out honestly reports what the raw
    # LLM output looked like (regimen_incomplete / ungrounded_dose_claims
    # below reflect the pre-safety-net plan) -- then actively fix it, since
    # a real run showed detection alone doesn't help the clinician reading
    # the final plan if nothing acts on the flag.
    regimen_incomplete = _check_regimen_completeness(plan, item.contraindication_type)
    ungrounded_dose_claims = _check_ungrounded_dose_claims(
        plan.get("antibiotic_plan", {}).get("regimen", []), chunks,
    )

    plan, net_notes_1 = _apply_regimen_safety_net(plan, item.active_guideline, item.contraindication_type)
    plan, net_notes_2 = _strip_ungrounded_dose_claims(plan, chunks)
    safety_net_notes = net_notes_1 + net_notes_2

    return CarePlanGenerationResult(
        plan=plan, chunks=chunks, retrieval_query=query,
        contraindication_flags=contraindication_flags,
        cross_guideline_conflict=cross_guideline_conflict,
        trend_note=trend_note, fallback_used=fallback_used,
        model_version=model_version, raw_llm_text=raw,
        regimen_incomplete=regimen_incomplete,
        ungrounded_dose_claims=ungrounded_dose_claims,
        regimen_safety_net_notes=safety_net_notes,
    )


def format_care_plan_text(plan: dict[str, Any]) -> str:
    """Identical section order/shape to fact_check.py's
    summarize_care_plan_for_judge() and build_care_plan_dataset.py's
    format_reference_text(), so generated and reference text are directly
    comparable by METEOR/BERTScore/RAGAS."""
    actions_lines = "\n".join(f"- {a}" for a in plan.get("recommended_actions", []))
    abx = plan.get("antibiotic_plan", {})
    abx_lines = "\n".join(f"- {r}" for r in abx.get("regimen", []))
    ci_lines = "\n".join(f"- {f}" for f in plan.get("contraindication_flags", []))
    return (
        f"CLINICAL SUMMARY:\n{plan.get('clinical_summary', '')}\n\n"
        f"RISK ANALYSIS:\n{plan.get('risk_analysis', '')}\n\n"
        f"DRIVER BREAKDOWN:\n{plan.get('driver_breakdown', '')}\n\n"
        f"RECOMMENDED ACTIONS:\n{actions_lines}\n\n"
        f"ANTIBIOTIC PLAN: required={abx.get('required')} urgency={abx.get('urgency')}\n{abx_lines}\n"
        f"duration={abx.get('duration')} stop_criteria={abx.get('stop_criteria')}\n\n"
        f"MONITORING PLAN:\n{plan.get('monitoring_plan', '')}\n\n"
        f"ESCALATION CRITERIA:\n{plan.get('escalation_criteria', '')}\n\n"
        f"NUTRITION/FLUID PLAN:\n{plan.get('nutrition_fluid_plan', '')}\n\n"
        f"DISAMBIGUATION BLOCK:\n{plan.get('disambiguation_block', '')}\n\n"
        f"CONTRAINDICATION FLAGS:\n{ci_lines}\n\n"
        f"TREND STATE CHANGE:\n{plan.get('trend_state_change', '')}"
    )


# ─────────────────────────────────────────────────────────────────────────
# 5. Full evaluation loop — generation + BLEU/METEOR/BERTScore
# ─────────────────────────────────────────────────────────────────────────

import re as _re_section_helper


def _chunk_page_key(chunk_id: str) -> str:
    """'NICE_pg12_box-1-risk-factors-for-e_chunk1' -> 'NICE_pg12'. Falls back
    to the raw chunk_id if it doesn't match the '<source>_pg<N>_...' naming
    convention this corpus's chunker uses (see chunks_ingested_*.csv)."""
    m = _re_section_helper.match(r"([A-Za-z_]+?)_pg(\d+)", chunk_id)
    return f"{m.group(1)}_pg{m.group(2)}" if m else chunk_id


def _section_level_precision_recall_f1(retrieved_ids: set[str], gold_ids: set[str]) -> dict[str, float]:
    """Precision/recall over PAGES rather than exact chunk_ids: did retrieval
    land on the same guideline page as a gold chunk, even if the specific
    paragraph-level chunk differs? See BUGFIX note in
    run_care_plan_evaluation_harness for why exact chunk_id P/R is
    unreliable on this corpus. This is a coarser, exact-match-free
    diagnostic that sits between the (too strict) exact-id metric and the
    (already primary) anchor_grounding_recall."""
    ret_pages = {_chunk_page_key(c) for c in retrieved_ids}
    gold_pages = {_chunk_page_key(c) for c in gold_ids}
    if not gold_pages:
        return {"precision": float(len(ret_pages) == 0), "recall": 1.0, "f1": float(len(ret_pages) == 0)}
    overlap = len(ret_pages & gold_pages)
    precision = overlap / len(ret_pages) if ret_pages else 0.0
    recall = overlap / len(gold_pages) if gold_pages else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def audit_gold_chunk_label_quality(items: list, chunk_text_by_id: dict[str, str]) -> "pd.DataFrame":
    """Quantify how much to trust gold_chunk_ids on a given eval dataset,
    BEFORE reporting retrieval_precision/recall against it. For each item,
    checks what fraction of its gold_chunk_ids actually contain at least
    one of that item's own anchor_phrases (a looser bar than true clinical
    relevance -- if gold chunks fail even this, retrieval P/R against them
    is not a valid metric, full stop).

    Run this once per dataset version. If mean frac_relevant is low (this
    dataset: ~0.27, with 9/30 cases at 0.0 -- see neoguard_care_plan_eval_dataset.json's
    own metadata.changelog, which independently documents the same root
    cause) do not report retrieval_precision/recall as a paper result;
    use anchor_grounding_recall instead.

    chunk_text_by_id: e.g. pd.read_csv("chunks_ingested.csv").set_index("chunk_id")["chunk_text"].to_dict()
    """
    import pandas as pd
    rows = []
    for item in items:
        case_id = item["case_id"] if isinstance(item, dict) else item.case_id
        anchors = [a.lower() for a in (item.get("anchor_phrases", []) if isinstance(item, dict) else item.anchor_phrases)]
        gold_ids = item.get("gold_chunk_ids", []) if isinstance(item, dict) else item.gold_chunk_ids
        n_relevant = sum(
            1 for cid in gold_ids
            if isinstance(chunk_text_by_id.get(cid, ""), str) and any(a in chunk_text_by_id[cid].lower() for a in anchors)
        )
        rows.append({
            "case_id": case_id, "n_gold": len(gold_ids),
            "n_gold_relevant_to_own_anchors": n_relevant,
            "frac_relevant": (n_relevant / len(gold_ids)) if gold_ids else None,
        })
    df = pd.DataFrame(rows)
    mean_frac = df["frac_relevant"].mean()
    zero_count = (df["n_gold_relevant_to_own_anchors"] == 0).sum()
    print(f"Gold-label quality audit: mean frac_relevant={mean_frac:.3f}, "
          f"{zero_count}/{len(df)} cases with ZERO relevant gold chunks.")
    if mean_frac < 0.5:
        print("  -> WARNING: gold_chunk_ids is unreliable ground truth on this dataset. "
              "Do not report retrieval_precision/recall against it as a paper result. "
              "Use anchor_grounding_recall as the primary retrieval-quality metric instead.")
    return df


def rebuild_gold_chunk_ids_from_anchors(
    items: list,
    chunk_df: "pd.DataFrame",
    min_hits: int = 2,
    max_gold: int = 8,
) -> dict[str, list[str]]:
    """Reconstruct a usable gold_chunk_ids set per case from anchor_phrases,
    since the shipped gold_chunk_ids was confirmed unreliable (see
    audit_gold_chunk_label_quality). This is a distant/weak-supervision
    relabeling, not a clinician-verified gold set -- document it as such
    wherever it's cited (e.g. "gold labels re-derived from anchor-phrase
    matching against the guideline corpus, not independently verified by a
    clinician"). It is a legitimate improvement over the original broken
    labels (which the dataset's own audit trail already discredited) but is
    a different kind of ground truth, not a fix that makes the original
    hand-curation trustworthy again.

    Method: for each case, restrict candidate chunks to the guideline
    source(s) matching case['active_guideline'] (AAP also pulls
    AAP_PRETERM), score each chunk by how many of the case's anchor_phrases
    it contains (case-insensitive substring match), and keep chunks with
    >= min_hits distinct anchor-phrase matches, ranked by hit count and
    capped at max_gold. Cases with no chunk reaching min_hits fall back to
    the best available single-hit chunks (should be rare -- 0/30 cases
    needed the fallback on this dataset at min_hits=2).

    items:    case list, e.g. json.load(open("neoguard_care_plan_eval_dataset.json"))["cases"]
    chunk_df: e.g. pd.read_csv("chunks_ingested_1_.csv") -- needs
              'source', 'chunk_id', 'chunk_text' columns.

    Returns {case_id: [chunk_id, ...]}. Does NOT mutate items or write any
    file -- caller decides whether/how to persist (e.g. write a new
    gold_chunk_ids_v2 field, or a standalone JSON, so the original shipped
    labels remain inspectable for comparison).
    """
    import re as _re

    def _sources_for_guideline(g: str) -> set[str]:
        return {"AAP", "AAP_PRETERM"} if g == "AAP" else {g}

    def _norm(s: str) -> str:
        return _re.sub(r"\s+", " ", s.lower().strip())

    new_gold: dict[str, list[str]] = {}
    for case in items:
        case_id = case["case_id"] if isinstance(case, dict) else case.case_id
        anchors = case.get("anchor_phrases", []) if isinstance(case, dict) else case.anchor_phrases
        guideline = case.get("active_guideline") if isinstance(case, dict) else case.active_guideline
        cand = chunk_df[chunk_df["source"].isin(_sources_for_guideline(guideline))]

        scored = []
        for _, r in cand.iterrows():
            txt = _norm(str(r["chunk_text"]))
            n_hits = sum(1 for a in anchors if _norm(a) in txt)
            if n_hits > 0:
                scored.append((r["chunk_id"], n_hits))
        scored.sort(key=lambda x: -x[1])

        strong = [cid for cid, n in scored if n >= min_hits][:max_gold]
        new_gold[case_id] = strong if strong else [cid for cid, _ in scored][:max_gold]

    return new_gold


def anchor_grounding_recall(chunks: list, anchor_phrases: list[str]) -> float | None:
    """Fraction of a case's anchor_phrases (guideline-verified short phrases,
    see build_care_plan_dataset.py v2 -- reused directly from the base
    harness's own hand-curated ANCHOR_PHRASES bank) found anywhere in the
    retrieved chunks' text, case-insensitive and whitespace-normalized
    (handles the \\xa0 non-breaking-space issue the base harness's own
    _anchor_hit() documents for PDF-extracted text). This is the PRIMARY
    retrieval-grounding signal in v2 -- see the module-level docstring's
    "why exact chunk-id gold matching failed in v1" note. Returns None if
    the case has no anchor phrases (shouldn't happen with the v2 dataset,
    but guards against an older dataset file)."""
    if not anchor_phrases:
        return None
    blob = " ".join(c.chunk_text for c in chunks)
    blob = " ".join(blob.replace("\xa0", " ").split()).lower()
    hits = sum(1 for p in anchor_phrases if " ".join(p.replace("\xa0", " ").split()).lower() in blob)
    return hits / len(anchor_phrases)


def score_bertscore_batch_robust(
    base_module,
    candidates: list[str],
    references: list[str],
    model_type: str,
    device: str | None = None,
    batch_sizes: tuple[int, ...] = (32, 8, 2),
) -> dict[str, Any]:
    """Wraps base.score_bertscore_batch with a retry ladder instead of one
    shot at batch_size=32 on whatever device is auto-selected.

    Root-caused against the v1 run: roberta-large and distilbert-base-
    uncased scored fine but microsoft/deberta-large returned NaN for ALL
    30/30 cases. deberta-large's disentangled-attention relative-position
    matrices are meaningfully more memory-hungry per token than roberta-
    large's plain attention at the same batch_size/seq_len -- on a
    memory-constrained GPU (e.g. Colab's free-tier T4) this is a plausible
    CUDA-OOM-on-first-call, which the harness's own try/except caught and
    silently turned into a blanket NaN with no visible diagnosis unless you
    were watching stdout closely at the moment it printed "FAILED".

    This retries with progressively smaller batch sizes, then falls back to
    CPU if every GPU batch size still fails, and always prints the ACTUAL
    exception (not just a generic "FAILED") so a real, non-OOM problem
    (e.g. a genuinely missing/renamed model on HuggingFace) is easy to spot
    instead of looking identical to a transient memory issue.
    """
    import torch as _torch

    last_err: Exception | None = None
    devices_to_try = [device] if device else (
        ["cuda", "cpu"] if _torch.cuda.is_available() else ["cpu"]
    )
    for dev in devices_to_try:
        for bs in batch_sizes:
            try:
                return base_module.score_bertscore_batch(
                    candidates, references, model_type, device=dev, batch_size=bs,
                )
            except Exception as e:
                last_err = e
                print(f"  [{model_type}] failed on device={dev} batch_size={bs}: "
                      f"{type(e).__name__}: {str(e)[:200]}")
                if dev == "cuda":
                    _torch.cuda.empty_cache()
    print(f"  [{model_type}] all retries exhausted — giving up. Last error: {last_err}")
    return {
        "model_type": model_type, "precision": float("nan"), "recall": float("nan"),
        "f1": float("nan"), "per_item_precision": [], "per_item_recall": [], "per_item_f1": [],
    }


def run_care_plan_evaluation_harness(
    items: list["CarePlanEvalItem"],
    store,
    reranker,
    call_llm: Callable,
    top_k: int = 5,
    retrieval_top_k: int = 10,
    use_mmr: bool = True,
    guideline_nudge_weight: float = 0.15,   # matches config.py's production default -- see generate_care_plan_harness
    bertscore_backbones: dict[str, str] | None = None,
    bertscore_device: str | None = None,
    bertscore_batch_sizes: tuple[int, ...] = (32, 8, 2),
    verbose: bool = True,
) -> dict[str, Any]:
    base = _require_base()
    if bertscore_backbones is None:
        bertscore_backbones = base.BERTSCORE_BACKBONES

    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for i, item in enumerate(items, start=1):
        gen = generate_care_plan_harness(
            item, store, reranker, call_llm,
            top_k=top_k, retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
            guideline_nudge_weight=guideline_nudge_weight,
        )
        candidate_text = format_care_plan_text(gen.plan)
        reference_text = item.reference_text

        bleu_scores = base.score_bleu_pair(candidate_text, reference_text)
        meteor = base.score_meteor_pair(candidate_text, reference_text)

        retrieved_ids = {c.chunk_id for c in gen.chunks}
        gold_ids = set(item.gold_chunk_ids)

        # ── BUGFIX, UPDATED AGAIN 2026-08 (previous diagnosis was itself stale) ─
        # Original diagnosis (still true in general): exact chunk_id P/R is a
        # strict match that's sensitive to chunking granularity and gold-label
        # quality -- worth treating with caution on ANY dataset until checked.
        #
        # The specific numbers this comment used to cite ("only ~27% of
        # gold_chunk_ids entries contain even ONE anchor_phrase", "9/30 cases
        # have ZERO relevant gold chunks", the cp_case_001 example) describe
        # neoguard_care_plan_eval_dataset.json's v1/v2 state, BEFORE the
        # dataset's metadata.changelog records a v3 clinician re-curation of
        # gold_chunk_ids and anchor_phrases. That comment was never updated
        # after v3 shipped, so it was actively describing a superseded
        # problem. Re-audited 2026-08 directly against the CURRENT dataset
        # (v4, after an additional retrieval-pooling gold-expansion pass and
        # an anchor_phrases-coverage closure pass -- see that JSON's own
        # changelog): every one of the 30 cases' gold_chunk_ids entries now
        # contains at least one of that case's own anchor_phrases verbatim
        # (100%, up from 81% at v3 and the ~27% this comment used to claim),
        # and 0/30 cases have zero matching gold chunks. If you're running
        # against an older dataset file, re-run this audit yourself before
        # trusting either number -- don't assume either the old "27%" or the
        # new "100%" without checking the actual file in hand.
        #
        # CONSEQUENCE: with the v4 dataset, gold_chunk_ids-based exact-id/
        # section-level P/R IS a meaningful (if still granularity-sensitive)
        # retrieval-quality signal, not pure label noise -- but
        # anchor_grounding_recall remains the metric with the strongest,
        # most directly-verified ground truth (every anchor phrase is a
        # literal substring check, no chunking-granularity ambiguity), so
        # keep reporting it as primary and the P/R columns as a secondary,
        # corroborating diagnostic rather than switching which one is
        # "primary" on the strength of this one re-audit.
        # ─────────────────────────────────────────────────────────────────
        retrieval_prf_exact = base.precision_recall_f1(sorted(retrieved_ids), sorted(gold_ids)) if gold_ids else None
        retrieval_prf_section = _section_level_precision_recall_f1(retrieved_ids, gold_ids) if gold_ids else None
        grounding_recall = anchor_grounding_recall(gen.chunks, getattr(item, "anchor_phrases", []) or [])

        # deterministic-safety recall: did the generated plan (post-overlay)
        # actually surface every contraindication the rule engine detected?
        # Matched by drug-name substring (same helper _overlay_deterministic_
        # safety uses), not a literal '[SEVERITY] drug:' prefix match -- the
        # old prefix check under-counted recall whenever the LLM's own
        # phrasing (correctly) surfaced the flag in different words.
        safety_recall = 1.0
        if gen.contraindication_flags:
            surfaced_blob = " || ".join(gen.plan.get("contraindication_flags", [])).lower()
            hits = sum(1 for f in gen.contraindication_flags
                       if _drug_name_from_deterministic_flag(f) in surfaced_blob)
            safety_recall = hits / len(gen.contraindication_flags)

        rows.append({
            "case_id": item.case_id,
            "active_guideline": item.active_guideline,
            "category": item.risk_result.get("category"),
            "contraindication_type": item.contraindication_type,
            "trend_type": item.trend_type,
            "ambiguous": item.ambiguous,
            "fallback_used": gen.fallback_used,
            "model_version": gen.model_version,
            "retrieval_query": gen.retrieval_query,
            "retrieved_chunk_ids": sorted(retrieved_ids),
            "gold_chunk_ids": sorted(gold_ids),
            # PRIMARY retrieval-quality metric (see BUGFIX note above).
            "anchor_grounding_recall": grounding_recall,
            # Section/page-level P/R: same guideline page as gold, chunk_id
            # need not match exactly. New middle-ground diagnostic.
            "retrieval_precision_section_UNRELIABLE_GOLD": retrieval_prf_section["precision"] if retrieval_prf_section else None,
            "retrieval_recall_section_UNRELIABLE_GOLD": retrieval_prf_section["recall"] if retrieval_prf_section else None,
            "retrieval_f1_section_UNRELIABLE_GOLD": retrieval_prf_section["f1"] if retrieval_prf_section else None,
            # Strict exact-chunk-id P/R -- kept for backward compatibility
            # and as a diagnostic ONLY. Do not treat as the headline
            # retrieval-quality result; see BUGFIX note above for why.
            "retrieval_precision_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["precision"] if retrieval_prf_exact else None,
            "retrieval_recall_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["recall"] if retrieval_prf_exact else None,
            "retrieval_f1_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["f1"] if retrieval_prf_exact else None,
            "cross_guideline_conflict_detected": gen.cross_guideline_conflict,
            "deterministic_safety_recall": safety_recall,
            "regimen_incomplete": gen.regimen_incomplete,
            "ungrounded_dose_claims": gen.ungrounded_dose_claims,
            "regimen_safety_net_notes": gen.regimen_safety_net_notes,
            "reference_text": reference_text,
            "generated_text": candidate_text,
            "generated_plan": gen.plan,
            "contexts": [c.chunk_text for c in gen.chunks],
            "question": gen.retrieval_query,
            "meteor": meteor,
            **bleu_scores,
        })
        if verbose:
            gr_str = f"{grounding_recall:.3f}" if grounding_recall is not None else "n/a"
            print(f"[{i:>3}/{len(items)}] {item.case_id:<14} guideline={item.active_guideline:<5} "
                  f"category={item.risk_result.get('category'):<12} fallback={gen.fallback_used}  "
                  f"BLEU={bleu_scores['bleu']:.3f}  METEOR={meteor:.3f}  grounding_recall={gr_str}  "
                  f"({time.time() - t0:.0f}s elapsed)")

    candidates = [r["generated_text"] for r in rows]
    references = [r["reference_text"] for r in rows]

    bertscore_results: dict[str, Any] = {}
    for label, model_name in bertscore_backbones.items():
        print(f"[BERTScore] {label} ({model_name}) — scoring {len(candidates)} care plans…")
        res = score_bertscore_batch_robust(
            base, candidates, references, model_name,
            device=bertscore_device, batch_sizes=bertscore_batch_sizes,
        )
        bertscore_results[label] = res
        if not np.isnan(res["f1"]):
            print(f"  P={res['precision']:.4f}  R={res['recall']:.4f}  F1={res['f1']:.4f}")

    for label, res in bertscore_results.items():
        safe_label = label.replace("-", "_").replace(".", "_")
        per_f1 = res.get("per_item_f1") or []
        for i, row in enumerate(rows):
            row[f"bertscore_f1_{safe_label}"] = per_f1[i] if i < len(per_f1) else float("nan")

    return {"per_item": rows, "bertscore": bertscore_results}


def summarize_care_plan_generation_results(eval_out: dict[str, Any]) -> dict[str, Any]:
    base = _require_base()
    rows = eval_out.get("per_item", [])
    if not rows:
        print("[care-plan eval] No results to summarize.")
        return {}

    candidates = [r["generated_text"] for r in rows]
    references = [[r["reference_text"]] for r in rows]
    corpus_bleu_score = base.corpus_bleu(candidates, references)
    mean_meteor = float(np.mean([r["meteor"] for r in rows]))

    from collections import defaultdict

    def _group_mean(key: str, value_key: str) -> dict[str, float]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            v = r.get(value_key)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                groups[str(r.get(key))].append(v)
        return {g: round(float(np.mean(v)), 4) for g, v in sorted(groups.items()) if v}

    n_fallback = sum(1 for r in rows if r["fallback_used"])
    safety_recalls = [r["deterministic_safety_recall"] for r in rows if r["contraindication_type"]]
    grounding_recalls = [r["anchor_grounding_recall"] for r in rows if r.get("anchor_grounding_recall") is not None]
    section_precisions = [r["retrieval_precision_section_UNRELIABLE_GOLD"] for r in rows if r.get("retrieval_precision_section_UNRELIABLE_GOLD") is not None]
    section_recalls = [r["retrieval_recall_section_UNRELIABLE_GOLD"] for r in rows if r.get("retrieval_recall_section_UNRELIABLE_GOLD") is not None]
    exact_precisions = [r["retrieval_precision_exact_id_UNRELIABLE_GOLD"] for r in rows if r.get("retrieval_precision_exact_id_UNRELIABLE_GOLD") is not None]
    exact_recalls = [r["retrieval_recall_exact_id_UNRELIABLE_GOLD"] for r in rows if r.get("retrieval_recall_exact_id_UNRELIABLE_GOLD") is not None]
    n_ambiguous_meta = sum(1 for r in rows if r.get("ambiguous"))
    n_conflict_detected = sum(1 for r in rows if r.get("cross_guideline_conflict_detected"))
    n_regimen_incomplete = sum(1 for r in rows if r.get("regimen_incomplete"))
    n_ungrounded_dose = sum(1 for r in rows if r.get("ungrounded_dose_claims"))
    n_abx_required_cases = sum(1 for r in rows
                                if r.get("generated_plan", {}).get("antibiotic_plan", {}).get("required"))

    summary: dict[str, Any] = {
        "n_cases": len(rows),
        "corpus_bleu": round(corpus_bleu_score, 4),
        "mean_meteor": round(mean_meteor, 4),
        "meteor_by_guideline": _group_mean("active_guideline", "meteor"),
        "meteor_by_category": _group_mean("category", "meteor"),
        "fallback_rate": round(n_fallback / len(rows), 4),
        "anchor_grounding_recall_mean": round(float(np.mean(grounding_recalls)), 4) if grounding_recalls else None,
        # Secondary/diagnostic retrieval metrics -- NOT headline results.
        # See BUGFIX note in run_care_plan_evaluation_harness. Report
        # anchor_grounding_recall_mean above as the primary retrieval number.
        "retrieval_precision_section_UNRELIABLE_GOLD_mean": round(float(np.mean(section_precisions)), 4) if section_precisions else None,
        "retrieval_recall_section_UNRELIABLE_GOLD_mean": round(float(np.mean(section_recalls)), 4) if section_recalls else None,
        "retrieval_precision_exact_id_UNRELIABLE_GOLD_mean": round(float(np.mean(exact_precisions)), 4) if exact_precisions else None,
        "retrieval_recall_exact_id_UNRELIABLE_GOLD_mean": round(float(np.mean(exact_recalls)), 4) if exact_recalls else None,
        "deterministic_safety_recall_mean": round(float(np.mean(safety_recalls)), 4) if safety_recalls else None,
        "n_contraindication_cases": len(safety_recalls),
        "cross_guideline_conflict_detected_count": n_conflict_detected,
        "cross_guideline_conflict_expected_count": n_ambiguous_meta,
        "regimen_incomplete_count": n_regimen_incomplete,
        "ungrounded_dose_claim_count": n_ungrounded_dose,
        "n_antibiotic_required_cases": n_abx_required_cases,
        "bertscore": {},
    }

    print("\n" + "=" * 78)
    print(f"Care-plan generation evaluation summary — {summary['n_cases']} cases")
    print("=" * 78)
    print(f"Corpus BLEU:                   {summary['corpus_bleu']:.4f}")
    print(f"Mean METEOR:                   {summary['mean_meteor']:.4f}")
    if grounding_recalls:
        print(f"Anchor grounding recall:       {summary['anchor_grounding_recall_mean']:.4f}  "
              f"(n={len(grounding_recalls)} cases with anchor phrases)")
    print(f"Cross-guideline conflict rate:  {n_conflict_detected}/{len(rows)} detected  "
          f"(dataset intends ~{n_ambiguous_meta}/{len(rows)} genuinely ambiguous — "
          f"large gap here means retrieval isn't favoring the active guideline enough)")
    print(f"Regimen incomplete (2nd agent silently missing): {n_regimen_incomplete}/{n_abx_required_cases} "
          f"antibiotic-required cases — a first-line agent present with none of its "
          f"expected partner drug, and no contraindication explains the gap.")
    print(f"Ungrounded numeric dose claims: {n_ungrounded_dose}/{n_abx_required_cases} "
          f"antibiotic-required cases have at least one mg/kg/frequency figure not found "
          f"anywhere in the retrieved evidence — see each case's ungrounded_dose_claims "
          f"list for exactly which figure and why it matters.")
    n_safety_net_applied = sum(1 for r in rows if r.get("regimen_safety_net_notes"))
    print(f"Safety-net corrections applied: {n_safety_net_applied}/{len(rows)} cases needed an "
          f"active regimen correction (missing regimen injected, partner drug injected, "
          f"clindamycin added, ungrounded dose stripped, or WHO drug name corrected) — see "
          f"regimen_safety_net_notes per case for exactly what was changed.")
    print(f"Fallback rate:                {summary['fallback_rate']:.4f}  ({n_fallback}/{len(rows)})")
    if safety_recalls:
        print(f"Deterministic safety recall:  {summary['deterministic_safety_recall_mean']:.4f}  "
              f"(n={len(safety_recalls)} contraindication cases)")
    print("-" * 78)
    for label, res in eval_out.get("bertscore", {}).items():
        summary["bertscore"][label] = {
            "precision": round(res["precision"], 4),
            "recall": round(res["recall"], 4),
            "f1": round(res["f1"], 4),
        }
        print(f"BERTScore [{label:<24}] P={res['precision']:.4f}  R={res['recall']:.4f}  F1={res['f1']:.4f}")
    print("=" * 78)
    return summary


# ─────────────────────────────────────────────────────────────────────────
# 6. RAGAS — faithfulness / answer_relevancy / context_precision /
#    context_recall. Optional: this whole section degrades to a clear
#    "not installed / not configured" message rather than breaking the
#    METEOR/BERTScore results above, since ragas needs its OWN judge LLM +
#    embeddings (separate from whatever call_llm you used for generation)
#    and is commonly the thing that isn't available in an offline/local-only
#    run (config.py's disable_cloud_llm_fallback / HIPAA-strict mode).
#
#    Two ways to supply the ragas judge, either works:
#      (a) local_llm_base_url pointed at an OpenAI-compatible endpoint
#          (Ollama's own http://localhost:11434/v1, matches config.py's
#          local_llm_base_url exactly) — fully offline, no data leaves
#          your infrastructure, same constraint the app itself runs under.
#      (b) openai_api_key — official OpenAI endpoint, if you have one and
#          offline-only isn't a requirement for this particular eval run.
# ─────────────────────────────────────────────────────────────────────────

def run_ragas_evaluation(
    per_item_rows: list[dict[str, Any]],
    local_llm_base_url: str = "",
    local_llm_model: str = "qwen3:4b-q4_K_M",
    openai_api_key: str = "",
    judge_model: str = "gpt-4o-mini",
    embedding_model: str = "text-embedding-3-small",
    metrics: list[str] | None = None,
) -> dict[str, Any] | None:
    """Runs RAGAS over the SAME rows produced by
    run_care_plan_evaluation_harness()'s eval_out["per_item"] — each row
    already carries `question` (the retrieval query), `generated_text`
    (answer), `contexts` (retrieved chunk texts), and `reference_text`
    (ground_truth), so no reshaping is needed beyond column renaming.

    Returns None (with an explanatory print) if ragas/datasets/langchain-
    openai aren't installed, or if neither local_llm_base_url nor
    openai_api_key was supplied — never raises, so a missing optional dep
    doesn't take down the rest of the notebook's results.
    """
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
        from datasets import Dataset
    except ImportError as e:
        print(f"[RAGAS] not installed ({e}). Install with:\n"
              f"  pip install ragas datasets langchain-openai --break-system-packages\n"
              f"Skipping RAGAS — METEOR/BERTScore results above are unaffected.")
        return None

    if not local_llm_base_url and not openai_api_key:
        print("[RAGAS] no judge LLM configured (pass local_llm_base_url=... for a local "
              "Ollama/vLLM OpenAI-compatible endpoint, or openai_api_key=... for OpenAI). "
              "Skipping RAGAS.")
        return None

    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError as e:
        print(f"[RAGAS] langchain-openai not installed ({e}). "
              f"pip install langchain-openai --break-system-packages")
        return None

    if local_llm_base_url:
        url = local_llm_base_url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        judge_llm = ChatOpenAI(base_url=url, api_key="ollama-local", model=local_llm_model, temperature=0.0)
        # Local embedding models rarely serve an OpenAI-compatible /embeddings
        # route the same way chat completions do — fall back to a local
        # sentence-transformers embedder via ragas' wrapper if available,
        # otherwise this will error clearly rather than silently misscoring.
        try:
            from ragas.embeddings import HuggingfaceEmbeddings
            judge_embeddings = HuggingfaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        except Exception:
            judge_embeddings = OpenAIEmbeddings(base_url=url, api_key="ollama-local", model=embedding_model)
    else:
        judge_llm = ChatOpenAI(api_key=openai_api_key, model=judge_model, temperature=0.0)
        judge_embeddings = OpenAIEmbeddings(api_key=openai_api_key, model=embedding_model)

    metric_registry = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    metric_names = metrics or list(metric_registry.keys())
    ragas_metrics = [metric_registry[m] for m in metric_names]

    ds_dict = {
        "question": [r["question"] for r in per_item_rows],
        "answer": [r["generated_text"] for r in per_item_rows],
        "contexts": [r["contexts"] if r["contexts"] else ["(no context retrieved)"] for r in per_item_rows],
        "ground_truth": [r["reference_text"] for r in per_item_rows],
    }
    dataset = Dataset.from_dict(ds_dict)

    print(f"[RAGAS] evaluating {len(per_item_rows)} care plans on {metric_names} "
          f"(judge={'local:' + local_llm_model if local_llm_base_url else judge_model})…")
    result = evaluate(dataset, metrics=ragas_metrics, llm=judge_llm, embeddings=judge_embeddings)
    result_df = result.to_pandas()

    summary = {m: round(float(result_df[m].mean()), 4) for m in metric_names if m in result_df.columns}
    print("\n" + "=" * 60)
    print("RAGAS summary")
    print("=" * 60)
    for m, v in summary.items():
        print(f"{m:<20} {v:.4f}")
    print("=" * 60)

    return {"summary": summary, "per_item": result_df.to_dict(orient="records")}
