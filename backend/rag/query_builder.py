"""Construct clinical queries from risk assessment payloads (Section 7.3.2)."""

from typing import Any


def build_clinical_query(
    risk_payload: dict[str, Any],
    active_guideline: str = "NICE",
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

    # Layer-specific terms from patient snapshot
    patient = risk_payload.get("patient", {})
    if isinstance(patient, dict):
        if patient.get("respiratory_distress") not in (None, "None", "Normal"):
            parts.extend(["respiratory distress", "tachypnoea", "grunting"])
        if patient.get("rom_hours", 0) >= 18:
            parts.extend(["prolonged ROM", "rupture of membranes"])
        if patient.get("maternal_temperature", 0) >= 38.0:
            parts.extend(["maternal fever", "intrapartum fever"])
        if patient.get("gbs_positive"):
            parts.extend(["GBS", "group B streptococcus", "intrapartum antibiotics"])
        if patient.get("crp_level") and float(patient["crp_level"]) >= 10:
            parts.extend(["CRP", "inflammatory markers"])
        if patient.get("blood_culture_positive"):
            parts.extend(["blood culture", "bacteremia", "empirical antibiotics"])

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

    return " ".join(dict.fromkeys(p.strip() for p in parts if p.strip()))
