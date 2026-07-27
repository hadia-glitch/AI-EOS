"""
Construct clinical queries from risk assessment payloads (Section 7.3.2).

Rewritten for two things that were previously missing:

1. Exhaustive current-symptom coverage. The original version only checked
   respiratory_distress, rom_hours, maternal_temperature, gbs_positive,
   crp_level, and blood_culture_positive -- six of the ~20 fields actually
   captured per assessment. lib/data/rag_service.dart's OFFLINE fallback
   query builder was already more thorough than this ONLINE path (checked
   poor perfusion, neuro status, WBC/IT-ratio/platelets/PCT, apgar,
   gestational-age extremes, temperature extremes) -- this version brings
   the online path to at least that level of coverage, using the schema.py
   PatientSnapshot fields added alongside this file.

2. Trend descriptors from PatientContext (see rag/patient_context_builder.py)
   instead of just the latest delta row. IMPORTANT: this deliberately stays
   a compact set of phrases ("CRP rising 3 consecutive readings"), not a
   dump of raw history -- see patient_context_builder.py's module docstring
   for why raw history belongs in the generation prompt, not the retrieval
   query. Diluting a BM25/embedding query with a paragraph of history text
   makes retrieval worse, not better.
"""

from typing import Any

from config import get_settings


def build_clinical_query(
    risk_payload: dict[str, Any],
    active_guideline: str = "NICE",
    deltas: dict | None = None,
    context: Any = None,  # Optional[PatientContext] -- typed loosely to avoid
                           # a hard import dependency for callers that only
                           # have a risk_payload (e.g. tests, offline paths).
) -> str:
    """Build a clinical search query from de-identified risk result data."""
    parts: list[str] = [
        "EOS management",
        "neonatal sepsis guidelines",
    ]

    category = risk_payload.get("category", risk_payload.get("risk_category", ""))
    if category:
        parts.append(f"{str(category).upper()} risk")

    guideline = active_guideline.upper()
    if guideline == "NICE":
        parts.append("NICE NG195")
    elif guideline == "AAP":
        parts.append("AAP 2023 early-onset sepsis")
    elif guideline == "WHO":
        parts.append("WHO newborn sepsis")

    drivers = risk_payload.get("drivers", [])
    if isinstance(drivers, list):
        for driver in drivers:
            if isinstance(driver, dict):
                name = driver.get("name", "")
                reason = driver.get("reason", "")
                if name:
                    parts.append(str(name))
                if reason:
                    parts.append(str(reason))
            elif isinstance(driver, str):
                parts.append(driver)

    # ── Current-symptom terms — exhaustive, matching PatientSnapshot's full
    # field set (schemas.py), not just the original 6 threshold checks. ─────
    patient = risk_payload.get("patient", {})
    if hasattr(patient, "model_dump"):
        patient = patient.model_dump()
    if isinstance(patient, dict):
        # Maternal / peripartum
        if patient.get("respiratory_distress") not in (None, "None", "Normal"):
            parts.extend(["respiratory distress", "tachypnoea", "grunting"])
        if (patient.get("rom_hours") or 0) >= 18:
            parts.extend(["prolonged ROM", "rupture of membranes"])
        if (patient.get("maternal_temperature") or 0) >= 38.0:
            parts.extend(["maternal fever", "intrapartum fever"])
        if patient.get("gbs_positive"):
            parts.extend(["GBS", "group B streptococcus", "intrapartum antibiotics"])
        if patient.get("clinical_chorioamnionitis"):
            parts.extend(["chorioamnionitis", "intra-amniotic infection"])
        if patient.get("adequate_intrapartum_antibiotics") is False and patient.get("gbs_positive"):
            parts.extend(["inadequate prophylaxis", "IAP"])
        if patient.get("delivery_mode") == "Caesarean":
            parts.append("caesarean delivery")

        # Neonatal clinical signs
        if patient.get("oxygen_need") not in (None, "None"):
            parts.extend(["oxygen requirement", "respiratory support", "CPAP"])
        apgar = patient.get("apgar_5_min")
        if apgar is not None and apgar <= 6:
            parts.extend(["low Apgar score", "resuscitation"])
        if patient.get("poor_perfusion"):
            parts.extend(["poor perfusion", "shock", "capillary refill"])
        neuro = patient.get("neurological_status")
        if neuro not in (None, "Normal"):
            parts.extend(["lethargy", "irritability", "neurological status", str(neuro)])
        neo_temp = patient.get("neonatal_temperature")
        if neo_temp is not None:
            if float(neo_temp) < 36.0:
                parts.extend(["hypothermia", "temperature instability"])
            elif float(neo_temp) > 38.0:
                parts.extend(["hyperthermia", "neonatal fever"])
        ga = patient.get("gestational_age_weeks")
        if ga is not None and float(ga) < 37.0:
            parts.extend(["preterm", "prematurity"])

        # Labs — previously only CRP/blood culture were checked at all.
        if patient.get("crp_level") and float(patient["crp_level"]) >= 10:
            parts.extend(["CRP", "inflammatory markers"])
        if patient.get("blood_culture_positive"):
            parts.extend(["blood culture", "bacteremia", "empirical antibiotics"])
        wbc = patient.get("wbc_count")
        if wbc is not None:
            parts.extend(["white blood cell count", "WBC"])
        it_ratio = patient.get("it_ratio")
        if it_ratio is not None and float(it_ratio) >= 0.2:
            parts.extend(["I:T ratio", "immature neutrophil ratio"])
        pct = patient.get("pct_level")
        if pct is not None:
            parts.extend(["procalcitonin", "PCT"])
        platelets = patient.get("platelet_count")
        if platelets is not None and float(platelets) < 150000:
            parts.extend(["thrombocytopenia", "platelet count"])
        if patient.get("penicillin_allergy"):
            parts.extend(["penicillin allergy", "alternative antibiotics"])

    layer1 = risk_payload.get("layer1_score", 0)
    layer2 = risk_payload.get("layer2_score", 0)
    layer3 = risk_payload.get("layer3_score", 0)
    if layer2 and int(layer2) >= 3:
        parts.append("clinical illness")
    if layer3 and int(layer3) >= 2:
        parts.append("laboratory evidence sepsis")

    total = risk_payload.get("total_score", risk_payload.get("combined_score", 0))
    if total and int(total) >= 7:
        parts.extend(["blood culture", "empirical antibiotics", "senior review"])

    # ── Trend terms — prefer the streak-aware PatientContext descriptors
    # when available; fall back to the single-delta-row check otherwise so
    # this function still works for callers that only have `deltas` (e.g.
    # existing tests, or a caller that hasn't been updated to build a full
    # PatientContext yet). ───────────────────────────────────────────────
    if context is not None and hasattr(context, "query_context_terms"):
        parts.extend(context.query_context_terms())
    elif deltas:
        settings = get_settings()
        crp_delta = deltas.get("crp_delta")
        if crp_delta is not None and float(crp_delta) > 0:
            if float(crp_delta) >= settings.crp_delta_threshold:
                parts.extend(["rising CRP", "worsening inflammatory markers"])
            else:
                parts.extend(["CRP trend", "serial CRP monitoring"])
        score_delta = deltas.get("score_delta")
        if score_delta is not None:
            if float(score_delta) > 0:
                parts.extend(["deteriorating", "escalating risk"])
            elif float(score_delta) < 0:
                parts.extend(["improving", "resolving"])
        aki_stage = deltas.get("aki_stage")
        if aki_stage and int(aki_stage) >= 1:
            parts.append(f"AKI stage {int(aki_stage)}")

    return " ".join(dict.fromkeys(p.strip() for p in parts if p.strip()))