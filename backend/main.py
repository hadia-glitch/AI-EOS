"""NeoGuard AI FastAPI backend — RAG + Gemini explanation engine."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from rag.ingest import ingest_directory
from rag.retrieve import get_chunk_store, get_rag_health, retrieve_evidence
from schemas import (
    EncounterEvidenceRequest,
    EvidenceChunkResponse,
    EvidenceSearchRequest,
    ExplanationResponse,
    RagHealthResponse,
    RiskPayload,
)
from services.audit import log_audit_event
from services.gemini_service import generate_explanation


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = get_chunk_store()
    try:
        count = store.reload()
        print(f"Loaded {count} evidence chunks from Supabase")
    except Exception as e:
        print(f"Supabase unavailable, using seed fallback: {e}")
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "neoguard-ai"}


@app.get("/api/v1/admin/rag/health", response_model=RagHealthResponse)
def rag_health():
    return RagHealthResponse(**get_rag_health())


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


@app.post("/api/v1/encounters/{encounter_id}/explanation", response_model=ExplanationResponse)
def encounter_explanation(encounter_id: str, body: EncounterEvidenceRequest):
    payload = body.risk_result.model_dump()
    chunks = retrieve_evidence(
        risk_payload=payload,
        active_guideline=body.active_guideline,
    )
    explanation = generate_explanation(payload, body.active_guideline, chunks)

    log_audit_event(
        action_type="LLM_CALL",
        resource_id=encounter_id,
        change_summary={"category": payload.get("category"), "score": payload.get("total_score")},
        rag_chunks_used=[c.to_dict() for c in chunks],
        gemini_model_version=explanation.model_version,
        outcome="PARTIAL" if explanation.fallback_used else "SUCCESS",
    )
    return explanation


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

    return {"uploaded": file.filename, "ingestion": result}


@app.post("/api/v1/admin/guidelines/ingest")
def trigger_ingestion(clear: bool = False):
    result = ingest_directory(clear_existing=clear)
    get_chunk_store().reload()
    return result


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("main:app", host=s.api_host, port=s.api_port, reload=True)
