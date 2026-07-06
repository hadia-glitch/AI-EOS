"""
Retrieval pipeline — hybrid search (semantic + BM25) → RRF → cross-encoder rerank.

Production improvements:
  - Noise filtering at retrieval time (discard chunks that survived bad ingestion)
  - Deduplication by chunk_id before reranking
  - Score normalisation
  - Detailed logging for debugging
"""

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


# ── Noise filter applied at retrieval time ─────────────────────────────────────
# Catches chunks that slipped through ingestion cleaning

_RETRIEVAL_NOISE_PATTERNS = [
    re.compile(r"(author\s+manuscript\s*){2,}", re.I),
    re.compile(r"^(Author Manuscript\s*){1,}", re.I),
    re.compile(r"HHS\s+Public\s+Access", re.I),
    re.compile(r"^\s*(\d+\s*){6,}\s*$"),  # page number soup
]

_MIN_RETRIEVAL_CHUNK_CHARS = 100


def _is_noise_chunk(text: str) -> bool:
    if not text or len(text.strip()) < _MIN_RETRIEVAL_CHUNK_CHARS:
        return True
    for p in _RETRIEVAL_NOISE_PATTERNS:
        if p.search(text):
            return True
    words = text.lower().split()
    if words:
        noise = sum(1 for w in words if w in ("author", "manuscript", "hhs", "pmc"))
        if noise / len(words) > 0.25:
            return True
    return False


# ── Data classes ───────────────────────────────────────────────────────────────

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


# ── Chunk store ────────────────────────────────────────────────────────────────

class ChunkStore:
    def __init__(self):
        self.chunks: list[dict] = []
        self.embeddings: np.ndarray | None = None
        self.bm25: BM25Okapi | None = None
        self._tokenized: list[list[str]] = []
        self.last_loaded: datetime | None = None

    def reload(self, source_filter: list[str] | None = None) -> int:
        supabase = get_supabase()
        response = supabase.table("evidence_chunks").select("*").limit(10_000).execute()
        rows = response.data or []

        # Source filter
        if source_filter:
            normalized = {s.upper() for s in source_filter}
            filtered = [
                r for r in rows
                if any(
                    n in (r.get("source_name") or "").upper()
                    or n in ((r.get("metadata") or {}).get("source") or "").upper()
                    or n == "ALL"
                    for n in normalized
                )
            ]
            rows = filtered if filtered else rows

        # Deduplicate by id
        seen: set[str] = set()
        deduped: list[dict] = []
        for row in rows:
            rid = str(row.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                deduped.append(row)

        # Filter noise that survived ingestion
        clean = [r for r in deduped if not _is_noise_chunk(r.get("chunk_text", ""))]
        noise_count = len(deduped) - len(clean)
        if noise_count > 0:
            print(f"[ChunkStore] Filtered {noise_count} noise chunks at load time")

        self.chunks = clean
        self._build_indexes()
        self.last_loaded = datetime.now(timezone.utc)
        print(f"[ChunkStore] Loaded {len(self.chunks)} clean chunks (of {len(rows)} total)")
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

        vectors: list[np.ndarray] = []
        for chunk in self.chunks:
            emb = chunk.get("embedding")
            if emb is None:
                vectors.append(np.zeros(384, dtype=np.float32))
            elif isinstance(emb, str):
                parsed = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
                vectors.append(np.array(parsed, dtype=np.float32))
            else:
                vectors.append(np.array(emb, dtype=np.float32))
        self.embeddings = np.vstack(vectors).astype(np.float32) if vectors else None

    def semantic_search(self, query: str, embed_model, top_k: int = 10) -> list[tuple[dict, float]]:
        if not self.chunks or self.embeddings is None:
            return []
        query_vec = np.array(embed_model.get_text_embedding(query), dtype=np.float32)
        emb_norm = self.embeddings / (np.linalg.norm(self.embeddings, axis=1, keepdims=True) + 1e-10)
        q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        sims = emb_norm @ q_norm
        top_idx = np.argsort(sims)[::-1][:top_k]
        return [(self.chunks[i], float(sims[i])) for i in top_idx]

    def bm25_search(self, query: str, top_k: int = 10) -> list[tuple[dict, float]]:
        if not self.bm25 or not self.chunks:
            return []
        tokens = self._tokenize(query)
        scores = self.bm25.get_scores(tokens)
        top_idx = np.argsort(scores)[::-1][:top_k]
        max_score = float(scores[top_idx[0]]) if len(top_idx) > 0 and scores[top_idx[0]] > 0 else 1.0
        return [
            (self.chunks[i], float(scores[i]) / max(max_score, 1e-10))
            for i in top_idx
            if scores[i] > 0
        ]


_store = ChunkStore()


def get_chunk_store() -> ChunkStore:
    return _store


@lru_cache(maxsize=1)
def get_embed_model():
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    settings = get_settings()
    return HuggingFaceEmbedding(model_name=settings.embedding_model)


@lru_cache(maxsize=1)
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
    settings = get_settings()
    store = get_chunk_store()

    if not store.chunks or store.last_loaded is None:
        try:
            store.reload(source_filters)
        except Exception as e:
            print(f"[Retrieval] Supabase load failed: {e}")

    if not store.chunks:
        print("[Retrieval] No chunks in store — using seed fallback")
        return _fallback_chunks(query or "", active_guideline, top_k or settings.rerank_top_k)

    if query is None and risk_payload:
        query = build_clinical_query(risk_payload, active_guideline)
    if not query:
        query = "EOS neonatal sepsis management guidelines"

    print(f"[Retrieval] Query: '{query[:100]}' guideline={active_guideline} store={len(store.chunks)} chunks")

    embed_model = get_embed_model()
    retrieve_k = settings.retrieval_top_k

    semantic_hits = store.semantic_search(query, embed_model, top_k=retrieve_k)
    bm25_hits = store.bm25_search(query, top_k=retrieve_k)

    merged = reciprocal_rank_fusion(
        [[c for c, _ in semantic_hits], [c for c, _ in bm25_hits]],
        k=settings.rrf_k,
        id_fn=_chunk_id,
    )[:retrieve_k]

    if not merged:
        print("[Retrieval] RRF returned nothing — seed fallback")
        return _fallback_chunks(query, active_guideline, top_k or settings.rerank_top_k)

    # Deduplicate
    seen: set[str] = set()
    candidates = []
    for item, _ in merged:
        cid = _chunk_id(item)
        if cid not in seen:
            seen.add(cid)
            candidates.append(item)

    # Rerank
    reranker = get_reranker()
    pairs = [(query, c.get("chunk_text", "")) for c in candidates]
    rerank_scores = reranker.predict(pairs)

    scored = sorted(zip(candidates, rerank_scores), key=lambda x: float(x[1]), reverse=True)
    final_k = top_k or settings.rerank_top_k
    results = [_to_result(chunk, float(score)) for chunk, score in scored[:final_k]]

    print(f"[Retrieval] Returning {len(results)} chunks, top score={results[0].similarity_score if results else 'N/A'}")
    return results


def _fallback_chunks(query: str, active_guideline: str, limit: int) -> list[EvidenceChunkResult]:
    """High-quality seed chunks — used ONLY when Supabase has no ingested PDFs."""
    from rag.seed_chunks import SEED_CHUNKS

    query_lower = query.lower()
    scored: list[tuple[dict, float]] = []

    for chunk in SEED_CHUNKS:
        # Skip noise even in seeds
        if _is_noise_chunk(chunk.get("chunk_text", "")):
            continue
        score = 0.0
        text = chunk["chunk_text"].lower()
        for word in query_lower.split():
            if len(word) > 3 and word in text:
                score += 1.0
        if chunk.get("source", "").upper() == active_guideline.upper():
            score += 2.0
        scored.append((chunk, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Deduplicate seeds
    seen: set[str] = set()
    deduped = []
    for chunk, sc in scored:
        cid = chunk.get("id", "")
        if cid not in seen:
            seen.add(cid)
            deduped.append((chunk, sc))

    top = deduped[:limit] if deduped else [(c, 0.5) for c in SEED_CHUNKS[:limit]]

    return [
        EvidenceChunkResult(
            chunk_id=chunk.get("id", f"seed-{i}"),
            source=chunk.get("source", "NICE"),
            source_name=chunk.get("source_name", "Seed Guideline"),
            section=chunk.get("section", "General"),
            chunk_text=chunk.get("chunk_text", ""),
            similarity_score=round(min(sc / max(10, 1), 1.0), 4),
            region_tag=chunk.get("region_tag", "GLOBAL"),
            version=chunk.get("version", "2023"),
            chunk_index=i,
        )
        for i, (chunk, sc) in enumerate(top)
    ]


def get_rag_health() -> dict[str, Any]:
    store = get_chunk_store()
    chunk_count = len(store.chunks)
    last_ingestion: str | None = None

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
    except Exception as e:
        print(f"[RAG Health] {e}")

    settings = get_settings()
    return {
        "status": "healthy" if chunk_count > 0 else "empty",
        "total_chunks": chunk_count,
        "last_ingestion": last_ingestion or (store.last_loaded.isoformat() if store.last_loaded else None),
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
        "vector_index": "ivfflat" if chunk_count >= 100 else "pending",
    }