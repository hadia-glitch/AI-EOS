"""Tests for deterministic contraindication checks."""

from domain.contraindication_rules import check_contraindications, flags_to_strings


def test_gentamicin_unstaged_fallback_low_urine_output():
    # No `deltas` passed -> falls back to single-reading check, explicitly
    # labeled unstaged (not a KDIGO-confirmed diagnosis from one reading).
    flags = check_contraindications(
        {"urine_output_ml_kg_hr": 0.4},
        ["Gentamicin 5 mg/kg IV"],
    )
    assert len(flags) == 1
    assert "UNSTAGED" in flags[0].reason
    assert flags[0].severity == "HIGH"


def test_gentamicin_unstaged_fallback_elevated_creatinine():
    flags = check_contraindications(
        {"creatinine_mg_dl": 2.0},
        ["gentamicin"],
    )
    assert len(flags) == 1
    assert "UNSTAGED" in flags[0].reason
    assert flags[0].severity == "HIGH"


def test_gentamicin_staged_kdigo_stage1_creatinine_rise():
    deltas = {
        "aki_stage": 1,
        "creatinine_aki_stage": 1,
        "urine_aki_stage": 0,
        "creatinine_rise_48h": 0.4,
        "creatinine_ratio_to_baseline": None,
    }
    flags = check_contraindications({}, ["gentamicin"], deltas=deltas)
    assert len(flags) == 1
    assert "KDIGO stage 1" in flags[0].reason
    assert "UNSTAGED" not in flags[0].reason
    assert flags[0].severity == "HIGH"


def test_gentamicin_staged_kdigo_stage2_is_critical():
    deltas = {
        "aki_stage": 2,
        "creatinine_aki_stage": 2,
        "urine_aki_stage": 0,
        "creatinine_rise_48h": None,
        "creatinine_ratio_to_baseline": 2.1,
    }
    flags = check_contraindications({}, ["gentamicin"], deltas=deltas)
    assert len(flags) == 1
    assert flags[0].severity == "CRITICAL"


def test_gentamicin_staged_urine_criteria():
    deltas = {
        "aki_stage": 2,
        "creatinine_aki_stage": 0,
        "urine_aki_stage": 2,
    }
    flags = check_contraindications({}, ["gentamicin"], deltas=deltas)
    assert len(flags) == 1
    assert "oliguria" in flags[0].reason
    assert flags[0].severity == "CRITICAL"


def test_no_deltas_no_stage_falls_back_cleanly():
    # deltas present (e.g. has crp_delta from an earlier assessment) but no
    # aki_stage yet -- must fall back, not silently produce no flag despite
    # a concerning snapshot reading.
    deltas = {"crp_delta": 1.0, "aki_stage": 0}
    flags = check_contraindications(
        {"creatinine_mg_dl": 1.8}, ["gentamicin"], deltas=deltas,
    )
    assert len(flags) == 1
    assert "UNSTAGED" in flags[0].reason


def test_first_assessment_no_history_no_false_kdigo_claim():
    # aki_stage=0 and no snapshot concern either -> no flag, no guessing.
    deltas = {"aki_stage": 0, "creatinine_aki_stage": 0, "urine_aki_stage": 0}
    flags = check_contraindications({}, ["gentamicin"], deltas=deltas)
    assert flags == []


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