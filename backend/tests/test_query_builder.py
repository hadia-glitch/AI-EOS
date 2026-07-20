"""Tests for clinical query construction."""

from rag.query_builder import build_clinical_query


def test_query_builder_high_risk():
    payload = {
        "category": "HIGH",
        "total_score": 9,
        "layer2_score": 4,
        "layer3_score": 2,
        "drivers": [{"name": "Respiratory distress", "reason": "CPAP required"}],
        "patient": {
            "respiratory_distress": "Severe",
            "rom_hours": 22,
            "maternal_temperature": 38.4,
        },
    }
    query = build_clinical_query(payload, "NICE")
    assert "HIGH" in query
    assert "respiratory" in query.lower() or "Respiratory" in query


def test_query_builder_trend_deltas():
    payload = {"category": "HIGH", "total_score": 8}
    rising = build_clinical_query(payload, "NICE", deltas={"crp_delta": 3.0, "score_delta": 2})
    assert "rising CRP" in rising
    assert "deteriorating" in rising

    improving = build_clinical_query(payload, "NICE", deltas={"score_delta": -1})
    assert "improving" in improving
