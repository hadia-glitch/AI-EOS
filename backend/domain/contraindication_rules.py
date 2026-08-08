"""
Deterministic contraindication checks — no LLM.

Nephrotoxicity rule is staged using neonatal-modified KDIGO AKI criteria, as
used by the AWAKEN study consortium (Jetton et al. 2017, Lancet Child
Adolesc Health; summarized in Selewski et al. 2019, Pediatr Nephrol):
  - Creatinine: rise >=0.3 mg/dL within 48h, OR >=1.5x baseline within 7
    days (baseline = lowest previous value for this encounter) -> stage 1;
    >=2x baseline -> stage 2; >=3x baseline -> stage 3.
  - Urine output: <0.5 mL/kg/hr sustained >=6h -> stage 1; sustained >=12h
    -> stage 2; <0.3 mL/kg/hr sustained >=24h -> stage 3.
  - Overall stage = worse of the two criteria.

This replaces the earlier fixed-cutoff version (urine <1.0 mL/kg/hr,
creatinine >1.5 mg/dL as flat point-in-time checks), which had no citable
guideline source. See supabase/migrations/006_aki_staging.sql for the view
that computes staging from assessment history; that view's own header
documents the one real limitation: a patient's FIRST-EVER creatinine
reading can never trigger a rise-based flag, because there is nothing to
compare it against yet. That is "missing data never guesses" working as
intended, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContraindicationFlag:
    drug: str
    reason: str
    severity: str = "HIGH"


_GENTAMICIN_NAMES = {"gentamicin", "gentamycin"}
_PENICILLIN_NAMES = {"penicillin", "ampicillin", "benzylpenicillin", "amoxicillin"}

# AKI stage -> flag severity. Stage 2/3 is CRITICAL (unambiguous KDIGO
# criteria met, not just a single abnormal reading); stage 1 is HIGH.
_AKI_STAGE_SEVERITY = {1: "HIGH", 2: "CRITICAL", 3: "CRITICAL"}


def _normalize_drug(name: str) -> str:
    return name.strip().lower()


def _staged_nephrotoxicity_flag(drug: str, aki: dict) -> ContraindicationFlag | None:
    """Build a flag from real windowed AKI staging (assessment_deltas view)."""
    stage = aki.get("aki_stage")
    if not stage or int(stage) < 1:
        return None

    reasons = []
    creat_stage = aki.get("creatinine_aki_stage") or 0
    urine_stage = aki.get("urine_aki_stage") or 0

    if creat_stage >= 1:
        rise48 = aki.get("creatinine_rise_48h")
        ratio = aki.get("creatinine_ratio_to_baseline")
        if rise48 is not None and float(rise48) >= 0.3:
            reasons.append(f"creatinine rose {float(rise48):.2f} mg/dL within 48h (KDIGO stage {creat_stage})")
        elif ratio is not None:
            reasons.append(f"creatinine {float(ratio):.2f}x baseline (KDIGO stage {creat_stage})")
    if urine_stage >= 1:
        reasons.append(f"sustained oliguria meeting KDIGO stage {urine_stage} urine-output criteria")

    if not reasons:
        # aki_stage set but neither sub-reason resolved -- fail closed, don't guess.
        return None

    return ContraindicationFlag(
        drug=drug,
        reason="Neonatal-modified KDIGO AKI stage " + str(int(stage)) + " — nephrotoxicity risk with "
               "gentamicin: " + "; ".join(reasons),
        severity=_AKI_STAGE_SEVERITY.get(int(stage), "HIGH"),
    )


def _snapshot_fallback_flag(drug: str, patient_snapshot: dict) -> ContraindicationFlag | None:
    """
    Fallback when no windowed staging data is available (e.g. first
    assessment for this encounter, or the caller didn't pass `deltas`).
    Uses the same raw fields as before, but the reason string is explicit
    that this is an UNSTAGED single-reading check, not a confirmed KDIGO
    diagnosis -- do not silently upgrade its confidence in downstream text.
    """
    urine = patient_snapshot.get("urine_output_ml_kg_hr")
    creatinine = patient_snapshot.get("creatinine_mg_dl")
    low_urine = urine is not None and float(urine) < 0.5
    high_creatinine = creatinine is not None and float(creatinine) > 1.5

    if not (low_urine or high_creatinine):
        return None

    reasons = []
    if low_urine:
        reasons.append(f"single urine output reading {urine} mL/kg/hr (<0.5)")
    if high_creatinine:
        reasons.append(f"single creatinine reading {creatinine} mg/dL (>1.5, unstaged)")

    return ContraindicationFlag(
        drug=drug,
        reason=(
            "Possible nephrotoxicity risk with gentamicin (UNSTAGED — single reading only, "
            "no prior assessment to compare against, does not yet meet full KDIGO staging "
            "criteria): " + "; ".join(reasons)
        ),
        severity="HIGH",
    )


def check_contraindications(
    patient_snapshot: dict,
    proposed_drugs: list[str],
    deltas: dict | None = None,
) -> list[ContraindicationFlag]:
    """
    Pure deterministic contraindication checker. Returns flags only when
    patient data supports the rule — missing data never triggers a guess.

    `deltas`: the latest row from the `assessment_deltas` view (see
    supabase/migrations/006_aki_staging.sql), if available. When present and
    it contains a real `aki_stage`, staged KDIGO logic is used. When absent
    (or `aki_stage` is 0/None), falls back to a single-reading check that is
    explicitly labeled unstaged in its reason text — this can still surface
    a genuine early warning on a patient's very first assessment, it just
    can't claim KDIGO-confirmed AKI from one data point.
    """
    flags: list[ContraindicationFlag] = []
    normalized = [_normalize_drug(d) for d in proposed_drugs]

    for drug in normalized:
        if any(g in drug for g in _GENTAMICIN_NAMES):
            flag = None
            if deltas:
                flag = _staged_nephrotoxicity_flag(drug, deltas)
            if flag is None:
                flag = _snapshot_fallback_flag(drug, patient_snapshot)
            if flag is not None:
                flags.append(flag)

        if any(p in drug for p in _PENICILLIN_NAMES):
            allergy = patient_snapshot.get("penicillin_allergy")
            if allergy is True:
                flags.append(ContraindicationFlag(
                    drug=drug,
                    reason="Documented penicillin allergy — avoid beta-lactam agents",
                    severity="CRITICAL",
                ))

    return flags


def flags_to_strings(flags: list[ContraindicationFlag]) -> list[str]:
    return [f"[{f.severity}] {f.drug}: {f.reason}" for f in flags]


# ─────────────────────────────────────────────────────────────────────────
# WHO PSBI outpatient-eligibility exclusions — ported from the offline eval
# harness (RESOLVED there as a previously-undetected divergence: this
# module originally only encoded two rules, gentamicin/KDIGO nephrotoxicity
# and penicillin allergy, but the golden vignette set also tests a WHO PSBI
# outpatient-eligibility rule the shipped code never implemented).
#
# WHO's "Recommendations for management of serious bacterial infections in
# infants aged 0-59 days" outpatient regimens (oral amoxicillin / IM
# gentamicin+amoxicillin, given when hospital referral isn't feasible) do
# NOT apply to every infant with a qualifying sign pattern — birth weight
# <1500g or hospitalization for illness in the prior 14 days are explicit
# exclusions requiring hospitalization regardless of presenting signs or
# referral accessibility.
#
# NOTE (disclosed gap, not silently assumed away): this only fires when
# called with care_setting="outpatient_no_referral" — a no-op otherwise.
# NeoGuard's current data model (schemas.CarePlanRequest /
# DeidentifiedPatient in deidentify.dart) has no `care_setting` or
# `birth_weight_g`/`hospitalized_within_prior_14_days` fields yet, so this
# function is not wired into any call site in gemini_service.py by this
# change — wiring it up requires adding those fields end-to-end (schema,
# Flutter patient model, care-setting selector in the UI), which is outside
# the scope of "make retrieval/generation match the harness." Left here,
# tested and ready, rather than adding a fake default that would give false
# confidence that outpatient triage safety is covered when it isn't yet.
# The remaining 15 of 20 golden-set contraindication vignettes (confirmed
# Gram-negative organism, NICE's gentamicin-interval exceptions, maternal
# beta-lactam allergy, major congenital malformation precluding oral
# dosing, unexplained bleeding/thrombocytopenia) are likewise NOT encoded
# as deterministic code anywhere in this codebase — report that honestly
# rather than implying full coverage of the stratum.
# ─────────────────────────────────────────────────────────────────────────

_WHO_LBW_EXCLUSION_G = 1500
_WHO_RECENT_HOSPITALIZATION_DAYS = 14


def check_who_outpatient_exclusions(
    patient_snapshot: dict,
    care_setting: str = "hospital",
) -> list[ContraindicationFlag]:
    """
    care_setting must be "outpatient_no_referral" for this to fire at all —
    a no-op for hospital-based care (NeoGuard's only currently-supported
    setting; see module-level note above).
    """
    if care_setting != "outpatient_no_referral":
        return []
    flags: list[ContraindicationFlag] = []
    birth_weight = patient_snapshot.get("birth_weight_g")
    if birth_weight is not None and float(birth_weight) < _WHO_LBW_EXCLUSION_G:
        flags.append(ContraindicationFlag(
            drug="WHO outpatient PSBI regimen (oral amoxicillin / IM gentamicin+amoxicillin)",
            reason=(
                f"Birth weight {birth_weight}g is under WHO's explicit "
                f"{_WHO_LBW_EXCLUSION_G}g exclusion threshold for outpatient PSBI "
                "regimens — this infant must be hospitalized regardless of the "
                "presenting sign pattern or referral accessibility."
            ),
            severity="CRITICAL",
        ))
    recent_hosp = patient_snapshot.get("hospitalized_within_prior_14_days")
    if recent_hosp is True:
        flags.append(ContraindicationFlag(
            drug="WHO outpatient PSBI regimen (oral amoxicillin / IM gentamicin+amoxicillin)",
            reason=(
                f"Infant was hospitalized for illness within the prior "
                f"{_WHO_RECENT_HOSPITALIZATION_DAYS} days — WHO's outpatient "
                "regimens explicitly do not apply; this infant must be "
                "hospitalized for the current illness."
            ),
            severity="CRITICAL",
        ))
    return flags