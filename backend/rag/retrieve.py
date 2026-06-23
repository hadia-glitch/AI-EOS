"""Retrieval pipeline 7.3.2 — hybrid search, RRF, cross-encoder rerank."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

from config import get_settings
from db import get_supabase
from rag.query_builder import build_clinical_query
from rag.rrf import reciprocal_rank_fusion


@dataclass
class EvidenceChunkResult:
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChunkStore:
    """In-memory chunk cache with BM25 index, synced from Supabase."""

    def __init__(self):
        self.chunks: list[dict] = []
        self.embeddings: np.ndarray | None = None
        self.bm25: BM25Okapi | None = None
        self._tokenized: list[list[str]] = []
        self.last_loaded: datetime | None = None

    def reload(self, source_filter: list[str] | None = None) -> int:
        settings = get_settings()
        supabase = get_supabase()

        query = supabase.table("evidence_chunks").select("*")
        if source_filter:
            # Filter by source_name prefix or metadata source
            pass  # filtered in Python below

        response = query.limit(10000).execute()
        rows = response.data or []

        if source_filter:
            normalized = {s.upper() for s in source_filter}
            filtered = []
            for row in rows:
                src = (row.get("source_name") or "").upper()
                meta = row.get("metadata") or {}
                meta_src = (meta.get("source") or "").upper()
                if any(n in src or n in meta_src or n == "ALL" for n in normalized):
                    filtered.append(row)
            rows = filtered if filtered else rows

        self.chunks = rows
        self._build_indexes()
        self.last_loaded = datetime.now(timezone.utc)
        return len(self.chunks)

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9]+", text.lower())

    def _build_indexes(self):
        if not self.chunks:
            self.embeddings = None
            self.bm25 = None
            self._tokenized = []
            return

        self._tokenized = [self._tokenize(c.get("chunk_text", "")) for c in self.chunks]
        self.bm25 = BM25Okapi(self._tokenized)

        vectors = []
        for chunk in self.chunks:
            emb = chunk.get("embedding")
            if emb is None:
                vectors.append(np.zeros(384))
            elif isinstance(emb, str):
                # pgvector string format "[0.1,0.2,...]"
                emb = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
                vectors.append(np.array(emb))
            else:
                vectors.append(np.array(emb))
        self.embeddings = np.vstack(vectors) if vectors else None

    def semantic_search(self, query: str, embed_model, top_k: int = 10) -> list[tuple[dict, float]]:
        if not self.chunks or self.embeddings is None:
            return []

        query_vec = np.array(embed_model.get_text_embedding(query))
        norms = np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_vec)
        norms = np.where(norms == 0, 1e-10, norms)
        similarities = self.embeddings @ query_vec / norms
        top_indices = np.argsort(similarities)[::-1][:top_k]
        return [(self.chunks[i], float(similarities[i])) for i in top_indices]

    def bm25_search(self, query: str, top_k: int = 10) -> list[tuple[dict, float]]:
        if not self.bm25 or not self.chunks:
            return []

        tokens = self._tokenize(query)
        scores = self.bm25.get_scores(tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        max_score = float(scores[top_indices[0]]) if len(top_indices) > 0 and scores[top_indices[0]] > 0 else 1.0
        return [
            (self.chunks[i], float(scores[i]) / max_score)
            for i in top_indices
            if scores[i] > 0
        ]


_store = ChunkStore()


def get_chunk_store() -> ChunkStore:
    return _store


@lru_cache
def get_embed_model():
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    settings = get_settings()
    return HuggingFaceEmbedding(model_name=settings.embedding_model)


@lru_cache
def get_reranker() -> CrossEncoder:
    settings = get_settings()
    return CrossEncoder(settings.reranker_model)


def _chunk_id(chunk: dict) -> str:
    return str(chunk.get("id", ""))


def _to_result(chunk: dict, score: float) -> EvidenceChunkResult:
    meta = chunk.get("metadata") or {}
    return EvidenceChunkResult(
        chunk_id=str(chunk.get("id", "")),
        source=meta.get("source", chunk.get("source_name", "UNKNOWN")[:10]),
        source_name=chunk.get("source_name", "Unknown"),
        section=chunk.get("section", "General"),
        chunk_text=chunk.get("chunk_text", ""),
        similarity_score=round(float(score), 4),
        region_tag=chunk.get("region_tag", "GLOBAL"),
        version=chunk.get("version", "1.0"),
        chunk_index=chunk.get("chunk_index"),
        page_number=chunk.get("page_number"),
    )


def retrieve_evidence(
    query: str | None = None,
    risk_payload: dict | None = None,
    active_guideline: str = "NICE",
    source_filters: list[str] | None = None,
    top_k: int | None = None,
) -> list[EvidenceChunkResult]:
    """Full retrieval pipeline: query build → hybrid → RRF → rerank."""
    settings = get_settings()
    store = get_chunk_store()

    if not store.chunks or store.last_loaded is None:
        try:
            store.reload(source_filters)
        except Exception:
            pass

    if not store.chunks:
        return _fallback_chunks(query or "", active_guideline, top_k or settings.rerank_top_k)

    if query is None and risk_payload:
        query = build_clinical_query(risk_payload, active_guideline)
    if not query:
        query = "EOS neonatal sepsis management guidelines"

    embed_model = get_embed_model()
    retrieve_k = settings.retrieval_top_k

    semantic_hits = store.semantic_search(query, embed_model, top_k=retrieve_k)
    bm25_hits = store.bm25_search(query, top_k=retrieve_k)

    semantic_ranked = [c for c, _ in semantic_hits]
    bm25_ranked = [c for c, _ in bm25_hits]

    merged = reciprocal_rank_fusion(
        [semantic_ranked, bm25_ranked],
        k=settings.rrf_k,
        id_fn=_chunk_id,
    )[:retrieve_k]

    if not merged:
        return _fallback_chunks(query, active_guideline, top_k or settings.rerank_top_k)

    candidates = [item for item, _ in merged]
    reranker = get_reranker()
    pairs = [(query, c.get("chunk_text", "")) for c in candidates]
    rerank_scores = reranker.predict(pairs)

    scored = sorted(
        zip(candidates, rerank_scores),
        key=lambda x: float(x[1]),
        reverse=True,
    )

    final_k = top_k or settings.rerank_top_k
    results = [_to_result(chunk, float(score)) for chunk, score in scored[:final_k]]
    return results


def _fallback_chunks(query: str, active_guideline: str, limit: int) -> list[EvidenceChunkResult]:
    """Built-in fallback when Supabase has no ingested chunks."""
    from rag.seed_chunks import SEED_CHUNKS

    query_lower = query.lower()
    scored: list[tuple[dict, float]] = []
    for chunk in SEED_CHUNKS:
        score = 0.0
        text = chunk["chunk_text"].lower()
        for word in query_lower.split():
            if len(word) > 3 and word in text:
                score += 1.0
        if chunk.get("source", "").upper() == active_guideline.upper():
            score += 2.0
        if score > 0:
            scored.append((chunk, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        scored = [(c, 0.5) for c in SEED_CHUNKS[:limit]]

    return [
        EvidenceChunkResult(
            chunk_id=chunk.get("id", f"seed-{i}"),
            source=chunk.get("source", "NICE"),
            source_name=chunk.get("source_name", "Seed Guideline"),
            section=chunk.get("section", "General"),
            chunk_text=chunk.get("chunk_text", ""),
            similarity_score=round(score / 10, 4),
            region_tag=chunk.get("region_tag", "GLOBAL"),
            version=chunk.get("version", "2023"),
            chunk_index=i,
        )
        for i, (chunk, score) in enumerate(scored[:limit])
    ]


def get_rag_health() -> dict[str, Any]:
    store = get_chunk_store()
    chunk_count = len(store.chunks)
    last_ingestion = None

    try:
        supabase = get_supabase()
        doc_resp = (
            supabase.table("guideline_documents")
            .select("ingested_at")
            .order("ingested_at", desc=True)
            .limit(1)
            .execute()
        )
        if doc_resp.data:
            last_ingestion = doc_resp.data[0].get("ingested_at")
        count_resp = supabase.table("evidence_chunks").select("id", count="exact").execute()
        chunk_count = count_resp.count or chunk_count
    except Exception:
        pass

    settings = get_settings()
    return {
        "status": "healthy" if chunk_count > 0 else "empty",
        "total_chunks": chunk_count,
        "last_ingestion": last_ingestion or (store.last_loaded.isoformat() if store.last_loaded else None),
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
        "vector_index": "ivfflat" if chunk_count >= 100 else "pending",
    }
