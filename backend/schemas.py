"""Pydantic request/response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class EvidenceSearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    source_filters: list[str] = Field(default_factory=list)
    active_guideline: str = "NICE"
    limit: int = Field(default=5, ge=1, le=20)
    # When set, retrieval is biased by this patient's current symptoms and
    # sustained trends (see rag/patient_context_builder.py) -- "load this
    # patient as context" for evidence search. Optional and additive: a
    # plain free-text search with no encounter_id behaves exactly as before.
    encounter_id: str | None = None


class EvidenceChunkResponse(BaseModel):
    chunk_id: str
    source: str
    source_name: str
    section: str
    chunk_text: str
    similarity_score: float
    region_tag: str
    version: str
    chunk_index: int | None = None
    page_number: int | None = None
    file_name: str = ""


class PatientSnapshot(BaseModel):
    gestational_age_weeks: float | None = None
    maternal_temperature: float | None = None
    rom_hours: float | None = None
    gbs_positive: bool | None = None
    respiratory_distress: str | None = None
    crp_level: float | None = None
    blood_culture_positive: bool | None = None
    urine_output_ml_kg_hr: float | None = None
    creatinine_mg_dl: float | None = None
    penicillin_allergy: bool | None = None
    # Previously captured in clinical_assessments (maternal_data/neonatal_data/
    # lab_data) but never exposed to the backend risk payload -- query
    # construction and generation prompts could not see these even though
    # they were sitting in the DB the whole time. See patient_state.dart's
    # _upsertClinicalTables for the exact field names these mirror.
    adequate_intrapartum_antibiotics: bool | None = None
    clinical_chorioamnionitis: bool | None = None
    delivery_mode: str | None = None
    oxygen_need: str | None = None
    apgar_5_min: int | None = None
    poor_perfusion: bool | None = None
    neonatal_temperature: float | None = None
    neurological_status: str | None = None
    wbc_count: float | None = None
    it_ratio: float | None = None
    platelet_count: float | None = None
    pct_level: float | None = None
    # Forward-compatible additions -- NOT yet populated by any current UI
    # flow (see new_patient_screen.dart's _birthWeight, which is captured
    # in local widget state but never actually passed into PatientParameters
    # today -- a pre-existing gap, unrelated to this change) or by
    # deidentify.dart's DeidentifiedPatient. Present here so
    # rag/query_builder.py's low-birth-weight query terms and
    # domain.contraindication_rules.check_who_outpatient_exclusions have
    # somewhere real to read from once that plumbing is added; both
    # currently see None for every request and are no-ops.
    birth_weight_g: float | None = None
    hospitalized_within_prior_14_days: bool | None = None


class RiskPayload(BaseModel):
    total_score: int = 0
    combined_score: int | None = None
    layer1_score: int = 0
    layer2_score: int = 0
    layer3_score: int = 0
    category: str = "LOW"
    risk_category: str | None = None
    probability_per_1000: float | None = None
    drivers: list[dict[str, Any]] = Field(default_factory=list)
    patient: PatientSnapshot | dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class EncounterEvidenceRequest(BaseModel):
    risk_result: RiskPayload
    active_guideline: str = "NICE"


class CitationItem(BaseModel):
    source: str
    section: str
    chunk_id: str
    similarity_score: float
    # Added so a citation can be resolved to an exact PDF location (see
    # GuidelinePdfViewerScreen, which already accepts pageNumber+searchText
    # for chunk-level jump-to-source — citations previously had nothing to
    # give it). Optional/default-empty so this is additive: any existing
    # caller building a CitationItem without these still works.
    page_number: int | None = None
    file_name: str = ""


class EvidenceLabelRef(BaseModel):
    """
    One retrieved chunk, addressable by a short inline label (e.g. "E1")
    instead of its raw UUID. Every [E#] a clinician sees in prose anywhere
    in a response (driver_breakdown, fact_check.flagged_claims, any
    [DETERMINISTIC.../FROM RETRIEVED EVIDENCE] correction note) resolves to
    exactly one of these -- see rag/fact_check.label_evidence_chunks, the
    single place that assigns E1/E2/... so the numbering is identical
    everywhere it's used within one response. The client renders each [E#]
    as a tappable/hoverable citation chip using this data rather than the
    old behaviour of the LLM printing a raw chunk_id UUID directly into
    prose (see driver_breakdown's old failure mode).
    """
    label: str
    chunk_id: str
    source: str
    source_name: str
    section: str
    # Trimmed excerpt of the chunk's actual text (not the full chunk) --
    # enough for a clinician to verify the citation without re-fetching
    # anything; see gemini_service._build_evidence_label_refs for the cap.
    snippet: str = ""
    page_number: int | None = None
    file_name: str = ""


class FactCheckResult(BaseModel):
    """
    Output of the Phase-3 judge agent (rag/fact_check.py). `performed=False`
    means the check was skipped (e.g. LOW/INTERMEDIATE risk — see
    config.fact_check_categories) or both LLM providers were unavailable —
    in either case the draft is served as-is and the UI should treat it the
    same as an unverified rule-based/AI answer, not as "verified".
    """
    performed: bool = False
    verified: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    flagged_claims: list[str] = Field(default_factory=list)
    notes: str = ""
    judge_model: str = ""


class ExplanationResponse(BaseModel):
    clinical_summary: str
    per_driver_explanations: list[dict[str, str]]
    recommended_actions: list[str]
    evidence_summary: str
    citation_list: list[CitationItem]
    confidence_disclaimer: str
    rag_chunks: list[EvidenceChunkResponse]
    model_version: str
    generated_offline: bool = False
    fallback_used: bool = False
    fact_check: FactCheckResult = Field(default_factory=FactCheckResult)
    # Label -> chunk lookup for every [E#] citation that may appear in this
    # response's free-text fields or in fact_check.flagged_claims. See
    # EvidenceLabelRef. Empty for rule-based/cached-legacy responses that
    # predate this field -- the client treats a missing/absent label as
    # plain text, never a broken-looking citation chip.
    evidence_labels: list[EvidenceLabelRef] = Field(default_factory=list)


class AntibioticPlanSchema(BaseModel):
    required: bool = False
    urgency: str = "Within 1 hour"
    regimen: list[str] = Field(default_factory=list)
    duration: str = ""
    stop_criteria: str = ""


class CarePlanRequest(BaseModel):
    risk_result: RiskPayload
    active_guideline: str = "NICE"
    # Chronological list of prior clinical_assessments + risk_results rows for
    # this encounter, oldest first. Each item is a free-form dict mirroring
    # the Supabase row shape (created_at, combined_score, category, ...).
    # Used only to let the LLM narrate change over time in clinical_summary /
    # trend_narrative — the trend chip shown in the UI is computed
    # deterministically on the Flutter side from the same data, never by the LLM.
    previous_assessments: list[dict[str, Any]] = Field(default_factory=list)
    # "hospital" (default) | "outpatient_no_referral". Gates
    # domain.contraindication_rules.check_who_outpatient_exclusions (WHO PSBI
    # birth-weight/recent-hospitalization exclusions) -- a no-op at
    # "hospital", which is every existing caller until the app actually
    # exposes an outpatient-triage entry point in the UI.
    care_setting: str = "hospital"


class ClinicalCarePlanResponse(BaseModel):
    clinical_summary: str
    risk_analysis: str
    trend_narrative: str = ""
    driver_breakdown: str
    recommended_actions: list[str]
    antibiotic_plan: AntibioticPlanSchema
    monitoring_plan: str
    escalation_criteria: str
    citation_list: list[CitationItem]
    confidence_disclaimer: str
    rag_chunks: list[EvidenceChunkResponse]
    model_version: str
    generated_offline: bool = False
    fallback_used: bool = False
    fact_check: FactCheckResult = Field(default_factory=FactCheckResult)
    nutrition_fluid_plan: str = ""
    parent_communication_notes: str = ""
    disambiguation_block: str = ""
    contraindication_flags: list[str] = Field(default_factory=list)
    trend_state_change: str = ""
    # Same label scheme as ExplanationResponse.evidence_labels -- see
    # EvidenceLabelRef's docstring.
    evidence_labels: list[EvidenceLabelRef] = Field(default_factory=list)
    # Transparency trail for the Phase-3 fact-check RESOLUTION agent (see
    # gemini_service._resolve_flagged_claims): one line per action taken
    # while trying to clear every claim the judge flagged, in order --
    # "grounded X from wider retrieval", "no corpus support for X --
    # applied [DETERMINISTIC DEFAULT]", or "could not auto-resolve: ...".
    # Empty when fact-check didn't run (see FactCheckResult.performed) or
    # ran clean with nothing to resolve. This is deliberately visible to
    # the clinician, not just a server log -- a plan that was silently
    # "fixed" without any trace of what changed is worse than one that
    # still shows an open flag.
    resolution_log: list[str] = Field(default_factory=list)


class RagHealthResponse(BaseModel):
    status: str
    total_chunks: int
    last_ingestion: str | None
    embedding_model: str
    reranker_model: str
    vector_index: str


class ConfigHealthResponse(BaseModel):
    supabase_configured: bool
    gemini_configured: bool
    gemini_model: str
    guidelines_dir: str
    rag_status: str


class MobileConfigResponse(BaseModel):
    supabase_url: str
    supabase_anon_key: str