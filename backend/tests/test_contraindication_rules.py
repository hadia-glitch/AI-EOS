"""Tests for deterministic contraindication checks."""

from domain.contraindication_rules import check_contraindications, flags_to_strings


def test_gentamicin_flags_low_urine_output():
    flags = check_contraindications(
        {"urine_output_ml_kg_hr": 0.5},
        ["Gentamicin 5 mg/kg IV"],
    )
    assert len(flags) == 1
    assert "Nephrotoxicity" in flags[0].reason


def test_gentamicin_flags_elevated_creatinine():
    flags = check_contraindications(
        {"creatinine_mg_dl": 2.0},
        ["gentamicin"],
    )
    assert len(flags) == 1
    assert flags[0].severity == "HIGH"


def test_penicillin_allergy_flags_ampicillin():
    flags = check_contraindications(
        {"penicillin_allergy": True},
        ["Ampicillin 50 mg/kg IV"],
    )
    assert len(flags) == 1
    assert flags[0].severity == "CRITICAL"


def test_missing_data_never_guesses():
    flags = check_contraindications({}, ["Gentamicin", "Ampicillin"])
    assert flags == []


def test_flags_to_strings_format():
    flags = check_contraindications(
        {"penicillin_allergy": True},
        ["penicillin"],
    )
    strings = flags_to_strings(flags)
    assert strings[0].startswith("[CRITICAL]")
