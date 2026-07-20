"""Deterministic contraindication checks — no LLM."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContraindicationFlag:
    drug: str
    reason: str
    severity: str = "HIGH"


_GENTAMICIN_NAMES = {"gentamicin", "gentamycin"}
_PENICILLIN_NAMES = {"penicillin", "ampicillin", "benzylpenicillin", "amoxicillin"}


def _normalize_drug(name: str) -> str:
    return name.strip().lower()


def check_contraindications(
    patient_snapshot: dict,
    proposed_drugs: list[str],
) -> list[ContraindicationFlag]:
    """
    Pure deterministic contraindication checker. Returns flags only when
    patient data supports the rule — missing data never triggers a guess.
    """
    flags: list[ContraindicationFlag] = []
    normalized = [_normalize_drug(d) for d in proposed_drugs]

    urine = patient_snapshot.get("urine_output_ml_kg_hr")
    creatinine = patient_snapshot.get("creatinine_mg_dl")

    for drug in normalized:
        if any(g in drug for g in _GENTAMICIN_NAMES):
            low_urine = urine is not None and float(urine) < 1.0
            high_creatinine = creatinine is not None and float(creatinine) > 1.5
            if low_urine or high_creatinine:
                reasons = []
                if low_urine:
                    reasons.append(f"urine output {urine} mL/kg/hr (<1.0)")
                if high_creatinine:
                    reasons.append(f"creatinine {creatinine} mg/dL (>1.5)")
                flags.append(ContraindicationFlag(
                    drug=drug,
                    reason=(
                        "Nephrotoxicity risk with gentamicin: "
                        + "; ".join(reasons)
                    ),
                    severity="HIGH",
                ))

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
