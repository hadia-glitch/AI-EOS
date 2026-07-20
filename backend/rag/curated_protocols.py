"""
Curated fallback protocols — evidence-based, per (guideline, risk category).

These are the last-resort content for guideline_knowledge_cache when no LLM
provider is reachable at cache-build time (e.g. both Gemini and Groq keys
missing/rate-limited during a rebuild). They are hand-written directly from
the same NICE NG195 / AAP 2023 / WHO newborn sepsis guideline text already
curated elsewhere in this codebase (lib/data/guidelines_data.dart and
backend/rag/seed_chunks.py) — not new medical claims, just restructured into
the per-category shape the offline care plan needs.

This is intentionally the FLOOR, not the target: whenever an LLM provider is
available, rag/guideline_cache.py prefers a real synthesis grounded in the
actual retrieved chunks for that guideline (which also picks up any locally
uploaded institutional protocol). This module only fires when that's not
possible, so the app is never left with zero offline care-plan content.

Clinical basis (each category maps to source guideline sections):
  NICE NG195  — risk factor assessment, red-flag clinical indicators,
                empirical antibiotics (benzylpenicillin + gentamicin),
                CRP at 18-24h, stop at 36h if culture-negative/well/CRP<10.
  AAP 2023    — clinical categorisation (well/equivocal/ill), adequate IAP
                thresholds, ampicillin + gentamicin, CBC/CRP timing caveats.
  WHO         — danger-sign based assessment for resource-limited settings,
                ampicillin/benzylpenicillin + gentamicin, 7-10 day course,
                pre-referral IM dosing.
"""

from __future__ import annotations

CATEGORIES = ["LOW", "INTERMEDIATE", "HIGH", "CRITICAL"]

_NICE_ANTIBIOTIC = {
    "regimen": ["IV benzylpenicillin", "IV gentamicin"],
    "duration": "Reassess at 36-48h; stop if culture-negative, clinically well, and CRP < 10 mg/L",
    "stop_criteria": "Blood culture negative at 36-48h AND clinically well AND CRP < 10 mg/L",
}

_AAP_ANTIBIOTIC = {
    "regimen": ["IV ampicillin", "IV gentamicin"],
    "duration": "48-72 hours pending culture; extend per sensitivities if culture-positive",
    "stop_criteria": "Blood culture negative AND clinically well AND CBC/CRP reassuring at 24-48h",
}

_WHO_ANTIBIOTIC = {
    "regimen": ["Injectable ampicillin (or benzylpenicillin)", "Injectable gentamicin"],
    "duration": "At least 7-10 days if confirmed; pre-referral IM dose if transfer needed",
    "stop_criteria": "Only after completing the full course in confirmed cases — WHO protocol does "
                      "not support early stopping in resource-limited settings without lab confirmation",
}

CURATED_PROTOCOLS: dict[str, dict[str, dict]] = {
    "NICE": {
        "LOW": {
            "clinical_summary_template": (
                "{patient_ref} has a LOW EOSCAL risk under NICE NG195. No red-flag clinical "
                "indicators or significant maternal risk factors were identified."
            ),
            "risk_analysis_template": (
                "Score {total_score}. Layer contributions: maternal {layer1_score}, "
                "clinical {layer2_score}, laboratory {layer3_score}."
            ),
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Routine postnatal observation per unit protocol",
                "No blood culture or empirical antibiotics indicated at this time",
                "Educate parents on signs to prompt reassessment (feeding, breathing, temperature, tone)",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Not indicated", **{}},
            "monitoring_plan": "Routine observations per postnatal ward schedule. Reassess if any new clinical sign develops.",
            "escalation_criteria": "Any red-flag sign (respiratory distress >4h after birth, seizures, "
                                    "signs of shock, need for CPR) — reassess immediately and escalate.",
        },
        "INTERMEDIATE": {
            "clinical_summary_template": (
                "{patient_ref} has an INTERMEDIATE EOSCAL risk under NICE NG195, driven by "
                "maternal and/or non-red-flag clinical factors requiring closer observation."
            ),
            "risk_analysis_template": (
                "Score {total_score}. Layer contributions: maternal {layer1_score}, "
                "clinical {layer2_score}, laboratory {layer3_score}."
            ),
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Enhanced clinical observation (minimum 4-hourly for first 12h) per NICE NG195",
                "Consider blood culture and CRP if any additional risk factor or non-red-flag sign emerges",
                "Review at 12h, 24h, and prior to discharge",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Reassess if signs progress",
                                 "regimen": [], "duration": "", "stop_criteria": ""},
            "monitoring_plan": "4-hourly observations for at least 12 hours; extend if any new sign or "
                                "risk factor identified. CRP at 18-24h if antibiotics are started.",
            "escalation_criteria": "Progression to any red-flag clinical indicator, or two or more "
                                    "non-red-flag signs — escalate to senior review and treat as HIGH risk.",
        },
        "HIGH": {
            "clinical_summary_template": (
                "{patient_ref} has a HIGH EOSCAL risk under NICE NG195. Current evidence "
                "supports blood culture sampling and empirical antibiotic therapy."
            ),
            "risk_analysis_template": (
                "Score {total_score}. Layer contributions: maternal {layer1_score}, "
                "clinical {layer2_score}, laboratory {layer3_score}."
            ),
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Obtain blood culture before first antibiotic dose — do not delay treatment to do so",
                "Start empirical IV antibiotics within 1 hour of the decision to treat",
                "Measure CRP at 18-24h after birth or after starting antibiotics; repeat at 36-48h",
                "Senior clinician review within 1 hour",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Within 1 hour", **_NICE_ANTIBIOTIC},
            "monitoring_plan": "Continuous/hourly observations for the first 4 hours, then per unit "
                                "escalation protocol. CRP at 18-24h and 36-48h.",
            "escalation_criteria": "Clinical deterioration, positive blood culture, or CRP rising — "
                                    "escalate to CRITICAL pathway and involve NICU/senior neonatology.",
        },
        "CRITICAL": {
            "clinical_summary_template": (
                "{patient_ref} has a CRITICAL EOSCAL risk under NICE NG195. Immediate empirical "
                "treatment and senior escalation are indicated."
            ),
            "risk_analysis_template": (
                "Score {total_score}. Layer contributions: maternal {layer1_score}, "
                "clinical {layer2_score}, laboratory {layer3_score}."
            ),
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Obtain blood culture and start empirical IV antibiotics IMMEDIATELY — do not wait for culture",
                "Immediate senior neonatology review and consider NICU admission",
                "Full septic screen per unit protocol (CRP, FBC, blood gas as clinically indicated)",
                "Continuous cardiorespiratory monitoring",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Immediate", **_NICE_ANTIBIOTIC},
            "monitoring_plan": "Continuous cardiorespiratory monitoring in a NICU/HDU setting. "
                                "Serial CRP and clinical review at least every 4 hours.",
            "escalation_criteria": "Any further deterioration — escalate per local critical-illness "
                                    "pathway; involve consultant neonatologist without delay.",
        },
    },

    "AAP": {
        "LOW": {
            "clinical_summary_template": (
                "{patient_ref} is well-appearing with LOW EOSCAL risk under AAP 2023 categorisation."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Routine care — no laboratory evaluation or antibiotics indicated",
                "Standard newborn observation per unit protocol",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Not indicated", "regimen": [], "duration": "", "stop_criteria": ""},
            "monitoring_plan": "Routine observation per postnatal protocol.",
            "escalation_criteria": "Any change to equivocal or clinically-ill appearance — reassess and move to enhanced observation.",
        },
        "INTERMEDIATE": {
            "clinical_summary_template": (
                "{patient_ref} is categorised as equivocal under AAP 2023, warranting enhanced observation."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Enhanced observation with vital signs at least every 4 hours",
                "Consider CBC (WBC, I:T ratio) and CRP if drawn 6-12h after birth for best sensitivity",
                "Reassess clinical appearance at each observation",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Reassess if signs progress", "regimen": [], "duration": "", "stop_criteria": ""},
            "monitoring_plan": "4-hourly vital signs; CBC/CRP most informative at 6-12h of age per AAP 2023.",
            "escalation_criteria": "Respiratory distress, temperature instability, lethargy, or poor perfusion developing — treat as clinically ill (HIGH).",
        },
        "HIGH": {
            "clinical_summary_template": (
                "{patient_ref} is categorised as clinically ill under AAP 2023 — immediate evaluation and empirical therapy are indicated."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Blood culture and CBC with differential (WBC, I:T ratio) and CRP",
                "Start empirical ampicillin and gentamicin without delay",
                "Assess adequacy of maternal intrapartum antibiotic prophylaxis (IAP) if GBS-positive",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Immediate", **_AAP_ANTIBIOTIC},
            "monitoring_plan": "Continuous monitoring; repeat CBC/CRP at 24h if initial values equivocal.",
            "escalation_criteria": "Any further clinical decline, or blood culture turns positive — escalate to CRITICAL/NICU pathway.",
        },
        "CRITICAL": {
            "clinical_summary_template": (
                "{patient_ref} meets AAP 2023 criteria for critically ill — immediate treatment and NICU-level care are indicated."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Immediate blood culture and empirical ampicillin + gentamicin — do not delay for culture",
                "NICU admission and consultant neonatology review",
                "Full sepsis workup (CBC, CRP, blood gas) and continuous monitoring",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Immediate", **_AAP_ANTIBIOTIC},
            "monitoring_plan": "Continuous cardiorespiratory monitoring in NICU. Serial labs per consultant direction.",
            "escalation_criteria": "Ongoing instability — escalate per local critical-illness pathway without delay.",
        },
    },

    "WHO": {
        "LOW": {
            "clinical_summary_template": (
                "{patient_ref} shows no WHO danger signs. LOW risk — essential newborn care applies."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Essential newborn care: skin-to-skin, early exclusive breastfeeding, hygienic cord care",
                "Educate caregiver on danger signs requiring urgent return",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Not indicated", "regimen": [], "duration": "", "stop_criteria": ""},
            "monitoring_plan": "Routine community/facility follow-up per WHO schedule.",
            "escalation_criteria": "Any WHO danger sign appearing (poor feeding, convulsions, fast breathing >=60/min, "
                                    "severe chest indrawing, temperature extremes, severe lethargy) — refer urgently.",
        },
        "INTERMEDIATE": {
            "clinical_summary_template": (
                "{patient_ref} has emerging risk factors without confirmed WHO danger signs — close observation indicated."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Close observation for danger signs; reassess frequently",
                "Ensure adequate thermal care and support breastfeeding/feeding tolerance",
                "Arrange follow-up before discharge",
            ],
            "antibiotic_plan": {"required": False, "urgency": "Reassess if danger signs emerge", "regimen": [], "duration": "", "stop_criteria": ""},
            "monitoring_plan": "Frequent reassessment for danger signs; supportive care (thermal, feeding).",
            "escalation_criteria": "Any single WHO danger sign — treat as serious bacterial infection and refer/treat per HIGH pathway.",
        },
        "HIGH": {
            "clinical_summary_template": (
                "{patient_ref} shows WHO danger sign(s) of possible serious bacterial infection — treatment indicated."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Start first-line injectable antibiotics (ampicillin/benzylpenicillin + gentamicin) without delay",
                "Arrange urgent referral to a facility with neonatal care capability if not already there",
                "Support oxygenation, hydration, glucose, and thermal control",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Immediate", **_WHO_ANTIBIOTIC},
            "monitoring_plan": "Close monitoring of oxygenation, hydration, glucose, and temperature during treatment/transfer.",
            "escalation_criteria": "Any deterioration during treatment or transfer — treat as CRITICAL and expedite referral.",
        },
        "CRITICAL": {
            "clinical_summary_template": (
                "{patient_ref} shows multiple/severe WHO danger signs — treat as critical serious bacterial infection."
            ),
            "risk_analysis_template": "Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.",
            "driver_breakdown_template": "See individual risk drivers below.",
            "recommended_actions": [
                "Give pre-referral IM dose of ampicillin/benzylpenicillin and gentamicin if transfer is needed",
                "Urgent referral/transfer to a facility with NICU-level capability",
                "Aggressive supportive care: oxygen if saturation <90%, IV/NG fluids, dextrose for hypoglycaemia, thermal control",
            ],
            "antibiotic_plan": {"required": True, "urgency": "Immediate (pre-referral IM dose if transferring)", **_WHO_ANTIBIOTIC},
            "monitoring_plan": "Continuous monitoring during stabilisation and transfer; full course to be completed at receiving facility.",
            "escalation_criteria": "This is already the most urgent WHO pathway — ensure transfer/escalation is not delayed.",
        },
    },
}


def get_curated_protocol(source: str, category: str) -> dict:
    """
    Returns the curated template for (source, category), falling back to the
    NICE template for unrecognised sources (e.g. local guidelines before
    their first LLM synthesis has run) so the offline cache is never empty.
    """
    source_key = source.upper() if source.upper() in CURATED_PROTOCOLS else "NICE"
    category_key = category.upper() if category.upper() in CATEGORIES else "INTERMEDIATE"
    return CURATED_PROTOCOLS[source_key][category_key]