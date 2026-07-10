"""Pydantic request/response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class EvidenceSearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    source_filters: list[str] = Field(default_factory=list)
    active_guideline: str = "NICE"
    limit: int = Field(default=5, ge=1, le=20)


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


class PatientSnapshot(BaseModel):
    gestational_age_weeks: float | None = None
    maternal_temperature: float | None = None
    rom_hours: float | None = None
    gbs_positive: bool | None = None
    respiratory_distress: str | None = None
    crp_level: float | None = None
    blood_culture_positive: bool | None = None


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