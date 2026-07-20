"""NeoGuard AI FastAPI backend — RAG + Gemini explanation + evidence AI engine."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import get_settings
from rag.guideline_cache import rebuild_all as rebuild_guideline_cache
from rag.guideline_cache import get_sync_payload as get_guideline_cache_sync_payload
from rag.ingest import ingest_directory
from rag.retrieve import get_chunk_store, get_rag_health, retrieve_evidence
from schemas import (
    CarePlanRequest,
    ClinicalCarePlanResponse,
    EncounterEvidenceRequest,
    EvidenceChunkResponse,
    EvidenceSearchRequest,
    ExplanationResponse,
    ConfigHealthResponse,
    MobileConfigResponse,
    RagHealthResponse,
    RiskPayload,
)
from services.audit import log_audit_event
from services.gemini_service import (
    generate_explanation,
    generate_care_plan,
    generate_evidence_overview,
    generate_evidence_cards,
)


# ── New request/response schemas for evidence AI endpoints ────────────────────

class EvidenceAiRequest(BaseModel):
    query: str = Field(..., min_length=2)
    active_guideline: str = "NICE"
    chunks: list[dict[str, Any]] = Field(default_factory=list)
    # Feature 2: optional de-identified snapshot of a clinician-selected
    # patient, built client-side from EoscalCalculator. When present, the
    # evidence AI overview/cards answer the query as it applies to this
    # specific patient rather than generically.
    patient_context: dict[str, Any] | None = None


class EvidenceCardItem(BaseModel):
    index: int
    headline: str
    processed_answer: str
    exact_excerpt: str
    source: str
    section: str
    document_url: str = ""


class EvidenceOverviewResponse(BaseModel):
    overview: str
    generated_by_ai: bool


class EvidenceCardsResponse(BaseModel):
    cards: list[EvidenceCardItem]
    generated_by_ai: bool


# ── Source URL resolver ───────────────────────────────────────────────────────

_SOURCE_URLS: dict[str, str] = {
    "NICE":       "https://www.nice.org.uk/guidance/ng195",
    "NG195":      "https://www.nice.org.uk/guidance/ng195",
    "AAP":        "https://publications.aap.org/pediatrics/article/150/6/e2022057091/190641",
    "WHO":        "https://www.who.int/publications/i/item/9789240058521",
    "KUZNIEWICZ": "https://doi.org/10.1542/peds.2023-065267",
    "EOSCAL":     "https://doi.org/10.1542/peds.2023-065267",
    "2011":       "https://doi.org/10.1542/peds.2011-1572",
    "2019":       "https://doi.org/10.1542/peds.2018-3090",
    "QATAR":      "https://bmjopen.bmj.com/content/13/9/e073216",
}

def _resolve_url(source_name: str) -> str:
    upper = source_name.upper()
    for key, url in _SOURCE_URLS.items():
        if key in upper:
            return url
    return ""


def _clean_truncate(text: str, max_len: int) -> str:
    """
    Truncate at a word boundary and append an ellipsis when cut — never
    slice mid-word like the old `text[:120]` did (which produced headlines
    like "...counselling and mot" instead of "...counselling and mother").
    """
    text = text.strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0].rstrip(" .,;:")
    return f"{cut}…" if cut else f"{text[:max_len].rstrip()}…"


def _fallback_cards(chunks: list[dict]) -> list[dict]:
    """
    Rule-based card generation when no LLM provider is available (fully
    offline / both Gemini and Groq unreachable).

    Headline is the DOCUMENT title, not a slice of body text — this mirrors
    how a search engine result shows the page title rather than a raw text
    fragment. A coherent AI-written topic sentence (see
    generate_evidence_cards's prompt) is still what renders whenever an LLM
    is reachable; this is strictly the last-resort path.
    """
    result = []
    for i, c in enumerate(chunks):
        content = c.get("chunk_text", c.get("content", ""))
        source_name = c.get("source_name", c.get("source", "")).strip()
        section = c.get("section", "").strip()

        if source_name and section:
            headline = f"{source_name} — {section}"
        elif source_name:
            headline = source_name
        elif section:
            headline = section
        else:
            headline = "Guideline excerpt"

        sentences = [s.strip() for s in content.replace("\n", " ").split(". ") if s.strip()]
        first_sentence = sentences[0] if sentences else content

        result.append({
            "index": i,
            "headline": _clean_truncate(headline, 90),
            "processed_answer": _clean_truncate(content, 400),
            "exact_excerpt": first_sentence,
        })
    return result


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = get_chunk_store()
    try:
        count = store.reload()
        print(f"[Startup] Loaded {count} evidence chunks from Supabase")
    except Exception as e:
        print(f"[Startup] Supabase unavailable, using seed fallback: {e}")
    yield


app = FastAPI(
    title="NeoGuard AI API",
    version="1.0.0",
    description="EOS CDSS — RAG evidence + Gemini explanations",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "neoguard-ai"}


@app.get("/api/v1/admin/rag/health", response_model=RagHealthResponse)
def rag_health():
    return RagHealthResponse(**get_rag_health())


@app.get("/api/v1/guidelines/documents")
def list_guideline_documents():
    """
    Pulled by the mobile app (see lib/data/offline_pdf_cache.dart) to know
    which guideline PDFs exist and where to download them from Supabase
    Storage for local offline caching. Small, infrequently-changing list —
    safe to pull in full on every sync.
    """
    from db import get_supabase
    supabase = get_supabase()
    resp = (
        supabase.table("guideline_documents")
        .select("id,name,source,version,region_tag,storage_path,storage_url,active,ingested_at")
        .eq("active", True)
        .execute()
    )
    return {"documents": resp.data or []}


@app.get("/api/v1/admin/config/health", response_model=ConfigHealthResponse)
def config_health():
    settings = get_settings()
    rag = get_rag_health()
    return ConfigHealthResponse(
        supabase_configured=bool(settings.supabase_url and settings.supabase_service_key),
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        guidelines_dir=settings.guidelines_dir,
        rag_status=rag["status"],
    )


@app.get("/api/v1/admin/fact-check/health")
def fact_check_health():
    """Diagnostic endpoint — shows whether/when the Phase-3 judge agent runs."""
    settings = get_settings()
    return {
        "enabled": settings.enable_fact_check,
        "gated_categories": sorted(settings.fact_check_category_set),
        "judge_provider_available": bool(settings.gemini_api_key or settings.groq_api_key),
    }


@app.get("/api/v1/mobile/config", response_model=MobileConfigResponse)
def mobile_config():
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_URL and SUPABASE_ANON_KEY must be set in backend/.env",
        )
    return MobileConfigResponse(
        supabase_url=settings.supabase_url,
        supabase_anon_key=settings.supabase_anon_key,
    )


# ── Evidence search ───────────────────────────────────────────────────────────

@app.post("/api/v1/evidence/search", response_model=list[EvidenceChunkResponse])
def evidence_search(body: EvidenceSearchRequest):
    chunks = retrieve_evidence(
        query=body.query,
        active_guideline=body.active_guideline,
        source_filters=body.source_filters or None,
        top_k=body.limit,
    )
    log_audit_event(
        action_type="RAG_SEARCH",
        change_summary={"query": body.query, "results": len(chunks)},
        rag_chunks_used=[c.to_dict() for c in chunks],
    )
    return [EvidenceChunkResponse(**c.to_dict()) for c in chunks]


# ── Evidence AI: overview ─────────────────────────────────────────────────────
# Flutter calls this after getting search results — backend uses its .env key

@app.post("/api/v1/evidence/overview", response_model=EvidenceOverviewResponse)
def evidence_overview(body: EvidenceAiRequest):
    """
    Generate a 3-4 sentence AI overview paragraph for a set of evidence chunks.
    Uses backend GEMINI_API_KEY from .env — Flutter needs no client-side key.
    """
    settings = get_settings()
    chunks_dicts = body.chunks  # already serialised by the Flutter client

    # If no chunks provided, retrieve them fresh
    if not chunks_dicts:
        raw_chunks = retrieve_evidence(
            query=body.query,
            active_guideline=body.active_guideline,
            top_k=8,
        )
        chunks_dicts = [c.to_dict() for c in raw_chunks]

    if not settings.gemini_api_key:
        return EvidenceOverviewResponse(overview="", generated_by_ai=False)

    overview, generated = generate_evidence_overview(
        body.query, body.active_guideline, chunks_dicts, patient_context=body.patient_context,
    )
    return EvidenceOverviewResponse(
        overview=overview,
        generated_by_ai=generated,
    )


# ── Evidence AI: per-card summaries ──────────────────────────────────────────

@app.post("/api/v1/evidence/cards", response_model=EvidenceCardsResponse)
def evidence_cards(body: EvidenceAiRequest):
    """
    Generate AI-processed headline + answer + excerpt for each evidence chunk.
    Uses backend GEMINI_API_KEY from .env — Flutter needs no client-side key.
    """
    settings = get_settings()
    chunks_dicts = body.chunks

    if not chunks_dicts:
        raw_chunks = retrieve_evidence(
            query=body.query,
            active_guideline=body.active_guideline,
            top_k=8,
        )
        chunks_dicts = [c.to_dict() for c in raw_chunks]

    ai_cards = []
    generated_by_ai = False

    if settings.gemini_api_key:
        ai_cards, generated_by_ai = generate_evidence_cards(
            body.query, body.active_guideline, chunks_dicts, patient_context=body.patient_context,
        )

    if not ai_cards:
        ai_cards = _fallback_cards(chunks_dicts)

    cards = []
    for item in ai_cards:
        idx = int(item.get("index", 0))
        chunk = chunks_dicts[idx] if idx < len(chunks_dicts) else (chunks_dicts[0] if chunks_dicts else {})
        source = chunk.get("source_name", chunk.get("source", ""))
        cards.append(EvidenceCardItem(
            index=idx,
            headline=str(item.get("headline", chunk.get("section", ""))),
            processed_answer=str(item.get("processed_answer", "")),
            exact_excerpt=str(item.get("exact_excerpt", "")),
            source=source,
            section=str(chunk.get("section", "")),
            document_url=_resolve_url(source),
        ))

    return EvidenceCardsResponse(cards=cards, generated_by_ai=generated_by_ai)


# ── Encounter endpoints ───────────────────────────────────────────────────────

@app.post("/api/v1/encounters/{encounter_id}/evidence", response_model=list[EvidenceChunkResponse])
def encounter_evidence(encounter_id: str, body: EncounterEvidenceRequest):
    payload = body.risk_result.model_dump()
    chunks = retrieve_evidence(
        risk_payload=payload,
        active_guideline=body.active_guideline,
    )
    log_audit_event(
        action_type="RAG_RETRIEVAL",
        resource_id=encounter_id,
        change_summary={"category": payload.get("category")},
        rag_chunks_used=[c.to_dict() for c in chunks],
    )
    return [EvidenceChunkResponse(**c.to_dict()) for c in chunks]


def _safe_retrieve_evidence(risk_payload: dict, active_guideline: str, query: str):
    """
    retrieve_evidence() already degrades gracefully internally (seed
    fallback if the model/store can't load, unreranked order if the
    reranker can't load) — this is one more layer for anything genuinely
    unforeseen, so a retrieval problem NEVER blocks explanation/care-plan
    generation. generate_explanation()/generate_care_plan() both work fine
    with an empty chunk list (they still produce a rule-based fallback
    answer, just without citations) — an empty list here is a degraded
    result, not a failure.
    """
    try:
        return retrieve_evidence(risk_payload=risk_payload, active_guideline=active_guideline)
    except Exception as e:
        print(f"[API] retrieve_evidence failed unexpectedly for query='{query[:80]}': {e}")
        return []


@app.post("/api/v1/encounters/{encounter_id}/explanation", response_model=ExplanationResponse)
def encounter_explanation(encounter_id: str, body: EncounterEvidenceRequest):
    payload = body.risk_result.model_dump()
    chunks = _safe_retrieve_evidence(
        payload, body.active_guideline,
        query=f"EOS {body.active_guideline} {payload.get('category', '')}",
    )
    try:
        explanation = generate_explanation(
            payload,
            body.active_guideline,
            chunks,
            query=f"EOS {body.active_guideline} {payload.get('category', '')}",
        )
    except Exception as e:
        # generate_explanation() already has its own Gemini -> Groq ->
        # rule-based fallback chain internally, so reaching this means
        # something outside that chain broke (e.g. building the prompt
        # itself). Return a clean 503 rather than an opaque 500 so the
        # mobile app's error handling (which distinguishes network/timeout
        # issues from real server errors) can react sensibly.
        print(f"[API] generate_explanation failed unexpectedly: {e}")
        raise HTTPException(status_code=503, detail=f"Explanation generation failed: {e}")

    log_audit_event(
        action_type="LLM_CALL",
        resource_id=encounter_id,
        change_summary={"category": payload.get("category"), "score": payload.get("total_score")},
        rag_chunks_used=[c.to_dict() for c in chunks],
        gemini_model_version=explanation.model_version,
        outcome="PARTIAL" if explanation.fallback_used else "SUCCESS",
    )
    return explanation


# ── Care plan (Feature 1) ──────────────────────────────────────────────────────
# Structured 7-section clinical care plan. Separate endpoint from
# /explanation above so the legacy ExplanationScreen keeps working
# unmodified while CarePlanScreen uses this richer response shape.

@app.post("/api/v1/encounters/{encounter_id}/care-plan", response_model=ClinicalCarePlanResponse)
def encounter_care_plan(encounter_id: str, body: CarePlanRequest):
    payload = body.risk_result.model_dump()
    chunks = _safe_retrieve_evidence(
        payload, body.active_guideline,
        query=f"EOS care plan {body.active_guideline} {payload.get('category', '')}",
    )
    try:
        care_plan = generate_care_plan(
            payload,
            body.active_guideline,
            chunks,
            previous_assessments=body.previous_assessments,
            query=f"EOS care plan {body.active_guideline} {payload.get('category', '')}",
        )
    except Exception as e:
        print(f"[API] generate_care_plan failed unexpectedly: {e}")
        raise HTTPException(status_code=503, detail=f"Care plan generation failed: {e}")

    log_audit_event(
        action_type="LLM_CALL",
        resource_id=encounter_id,
        change_summary={
            "category": payload.get("category"),
            "score": payload.get("total_score"),
            "previous_assessment_count": len(body.previous_assessments),
            "endpoint": "care-plan",
        },
        rag_chunks_used=[c.to_dict() for c in chunks],
        gemini_model_version=care_plan.model_version,
        outcome="PARTIAL" if care_plan.fallback_used else "SUCCESS",
    )
    return care_plan


# ── Gemini key diagnostic ─────────────────────────────────────────────────────

@app.get("/api/v1/admin/gemini/test")
def test_gemini():
    """
    Tests both Gemini and Groq connectivity.
    Shows which provider is active and which is configured as fallback.
    """
    settings = get_settings()
    results = {}

    # Test Gemini
    if settings.gemini_api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel(model_name=settings.gemini_model)
            response = model.generate_content(
                "Reply with exactly one word: working",
                generation_config={"max_output_tokens": 10, "temperature": 0},
            )
            results["gemini"] = {
                "status": "ok",
                "model": settings.gemini_model,
                "response": (response.text or "").strip(),
                "key_prefix": settings.gemini_api_key[:8] + "...",
            }
        except Exception as e:
            results["gemini"] = {
                "status": "error",
                "model": settings.gemini_model,
                "reason": str(e),
                "key_prefix": settings.gemini_api_key[:8] + "...",
            }
    else:
        results["gemini"] = {"status": "not_configured", "reason": "GEMINI_API_KEY not set in .env"}

    # Test Groq
    if settings.groq_api_key:
        try:
            from groq import Groq
            client = Groq(api_key=settings.groq_api_key)
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": "Reply with exactly one word: working"}],
                max_tokens=10,
                temperature=0,
            )
            results["groq"] = {
                "status": "ok",
                "model": settings.groq_model,
                "response": (response.choices[0].message.content or "").strip(),
                "key_prefix": settings.groq_api_key[:8] + "...",
            }
        except Exception as e:
            results["groq"] = {
                "status": "error",
                "model": settings.groq_model,
                "reason": str(e),
                "key_prefix": settings.groq_api_key[:8] + "...",
            }
    else:
        results["groq"] = {
            "status": "not_configured",
            "reason": "GROQ_API_KEY not set in .env — get free key at https://console.groq.com/keys",
        }

    # Summarise active provider
    gemini_ok = results["gemini"]["status"] == "ok"
    groq_ok = results["groq"]["status"] == "ok"
    active = (
        f"gemini ({settings.gemini_model})" if gemini_ok
        else f"groq/{settings.groq_model}" if groq_ok
        else "rule-based-fallback (no LLM available)"
    )

    return {
        "active_provider": active,
        "gemini": results["gemini"],
        "groq": results["groq"],
    }


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.post("/api/v1/admin/guidelines")
async def upload_guideline(file: UploadFile = File(...)):
    settings = get_settings()
    from pathlib import Path
    guidelines_dir = Path(settings.guidelines_dir)
    guidelines_dir.mkdir(parents=True, exist_ok=True)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")
    dest = guidelines_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    result = ingest_directory(guidelines_dir)
    get_chunk_store().reload()

    # Rebuild the offline guideline knowledge cache so this newly-uploaded
    # (possibly local/institutional) protocol is reflected in the mobile
    # app's offline care plan next time it syncs. Non-fatal: a rebuild
    # failure shouldn't fail the upload itself — the admin can retry via
    # POST /api/v1/admin/guidelines/rebuild-cache.
    cache_result = {"status": "skipped"}
    try:
        cache_result = rebuild_guideline_cache()
    except Exception as e:
        print(f"[Upload] Guideline cache rebuild failed (non-fatal): {e}")
        cache_result = {"status": "failed", "error": str(e)}

    return {"uploaded": file.filename, "ingestion": result, "guideline_cache": cache_result}


@app.post("/api/v1/admin/guidelines/ingest")
def trigger_ingestion(clear: bool = False):
    result = ingest_directory(clear_existing=clear)
    get_chunk_store().reload()

    cache_result = {"status": "skipped"}
    try:
        cache_result = rebuild_guideline_cache()
    except Exception as e:
        print(f"[Ingest] Guideline cache rebuild failed (non-fatal): {e}")
        cache_result = {"status": "failed", "error": str(e)}

    result["guideline_cache"] = cache_result
    return result


@app.post("/api/v1/admin/guidelines/rebuild-cache")
def rebuild_cache_endpoint(sources: str = ""):
    """
    Manually rebuild the offline guideline knowledge cache. Pass
    ?sources=NICE,LOCAL to limit to specific guidelines, or omit to rebuild
    every active guideline (defaults + anything in guideline_documents).
    """
    source_list = [s.strip().upper() for s in sources.split(",") if s.strip()] or None
    result = rebuild_guideline_cache(source_list)
    return {"status": "success", **result}


@app.get("/api/v1/guideline-cache/sync")
def sync_guideline_cache(since: str | None = None):
    """
    Pulled by the mobile app whenever it's online (see
    lib/data/offline_guideline_cache.dart) to refresh its local offline
    care-plan cache. Pass ?since=<ISO timestamp> for an incremental sync;
    omit for a full sync (small table — guidelines x 4 categories — so a
    full sync is cheap and is what the app does on first run).
    """
    rows = get_guideline_cache_sync_payload(since)
    return {"rows": rows, "synced_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run("main:app", host=s.api_host, port=s.api_port, reload=True)