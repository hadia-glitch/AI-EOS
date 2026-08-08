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
from rag.clinical_terms import expand_query
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


# ── Metadata-aware reranking (ported from the offline eval harness's
# Priority-1 fixes: the cross-encoder only sees (query, chunk_text) and is
# blind to whether a chunk is a *recommendation* vs *background*, and
# whether its age band matches the query's — both measured, real failure
# modes on the golden vignette set). Combined into the FinalScore formula
# used inside retrieve_evidence() below:
#
#   FinalScore = 0.80 * CrossEncoder
#              + 0.10 * SectionWeight(chunk_type, intent)
#              + 0.05 * AgeMatch(query, chunk_text)
#              + 0.05 * GuidelineBoost   (settings.guideline_nudge_weight)
#
# Weights are absolute boosts, not percentages, since the cross-encoder's
# raw output isn't bounded to [0, 1] — multiplicative scaling would skew
# toward already-high-scoring chunks regardless of section type. ─────────

_INTENT_PATTERNS: dict[str, list[str]] = {
    "treatment": [
        "antibiotic", "treat", "regimen", "dose", "dosing", "ampicillin",
        "gentamicin", "benzylpenicillin", "amoxicillin", "cloxacillin",
        "give", "administer", "prescrib", "start", "initiat", "manage",
        "recommend", "should", "what to do", "how to treat",
    ],
    "diagnosis": [
        "diagnos", "is this", "is it", "criteria", "confirm", "blood culture",
        "identify", "detect", "screen",
    ],
    "risk_assessment": [
        "risk factor", "at risk", "risk of", "likelihood", "probability",
        "prom", "gbs", "chorioamnionitis", "rom", "rupture of membrane",
        "iap", "intrapartum",
    ],
    "monitoring": [
        "monitor", "follow up", "follow-up", "review", "check", "reassess",
        "serial", "watch", "crp", "repeat", "interval",
    ],
    "definition": [
        "what is", "define", "definition", "explain", "overview",
        "background", "epidemiology",
    ],
}

# Section (chunk_type) -> absolute score adjustment. Our chunk.py pipeline
# (rag/chunk.py) currently tags chunk_type as one of "recommendation",
# "table", or "paragraph" — narrower than the harness's original corpus,
# which also had "algorithm"/"risk_factor"/"remarks"/"background"/
# "overview". Those five keys are kept here (harmless — they just never
# match today) so this stays a straight drop-in if chunk.py's typing is
# ever extended; "paragraph" chunks currently get a neutral 0.0 base
# rather than a wrong guess.
_SECTION_SCORE: dict[str, float] = {
    "recommendation": +0.15,
    "algorithm":      +0.12,
    "risk_factor":    +0.08,
    "table":          +0.05,
    "remarks":        -0.02,
    "background":     -0.05,
    "overview":       -0.08,
}

_INTENT_SECTION_MULT: dict[str, dict[str, float]] = {
    "treatment":       {"recommendation": 1.20, "algorithm": 1.10,
                        "background": 0.90, "overview": 0.80, "remarks": 0.95},
    "diagnosis":       {"recommendation": 1.10, "background": 1.00,
                        "overview": 0.95},
    "risk_assessment": {"risk_factor": 1.20, "background": 1.05,
                        "recommendation": 1.10},
    "monitoring":      {"recommendation": 1.10, "remarks": 1.05,
                        "background": 0.95},
    "definition":      {"background": 1.10, "overview": 1.05,
                        "recommendation": 0.90},
}

_AGE_BAND_0_6: frozenset[str] = frozenset([
    "0-6 day", "0 to 6 day", "day 0", "day 1", "day 2", "day 3",
    "day 4", "day 5", "day 6", "first day", "first week",
    "newborn", "neonate", "age < 7",
])
_AGE_BAND_7_59: frozenset[str] = frozenset([
    "7-59 day", "7 to 59 day", "week 1", "week 2", "week 3",
    "day 7", "day 14", "day 21", "day 28", "young infant",
    "1 month", "2 month", "1-2 month",
])


def _detect_query_intent(query: str) -> str:
    """Dominant clinical intent of *query*: one of 'treatment', 'diagnosis',
    'risk_assessment', 'monitoring', 'definition', 'other'. Pure keyword
    overlap scoring — cheap, deterministic, no model call."""
    q_low = query.lower()
    best_label, best_count = "other", 0
    for label, patterns in _INTENT_PATTERNS.items():
        count = sum(1 for p in patterns if p in q_low)
        if count > best_count:
            best_count, best_label = count, label
    return best_label


def _section_weight(chunk_type: str, intent: str) -> float:
    ct = (chunk_type or "").lower().strip()
    for key in _SECTION_SCORE:
        if key in ct:
            ct = key
            break
    base = _SECTION_SCORE.get(ct, 0.0)
    mult = _INTENT_SECTION_MULT.get(intent, {}).get(ct, 1.0)
    return base * mult


def _age_band_match(query: str, chunk_text: str) -> float:
    """+0.12 when query and chunk share an age band, -0.04 when they belong
    to clinically DIFFERENT age bands (flagged as dangerous during eval —
    e.g. serving 7-59-day dosing for a day-2 newborn), 0.0 when the query
    carries no age signal at all."""
    q_low = query.lower()
    c_low = chunk_text.lower()

    q_has_0_6 = any(p in q_low for p in _AGE_BAND_0_6)
    q_has_7_59 = any(p in q_low for p in _AGE_BAND_7_59)
    if not q_has_0_6 and not q_has_7_59:
        return 0.0

    c_has_0_6 = any(p in c_low for p in _AGE_BAND_0_6)
    c_has_7_59 = any(p in c_low for p in _AGE_BAND_7_59)

    if q_has_0_6 and c_has_0_6 and not c_has_7_59:
        return +0.12
    if q_has_7_59 and c_has_7_59 and not c_has_0_6:
        return +0.12
    if (q_has_0_6 and c_has_7_59 and not c_has_0_6) or \
       (q_has_7_59 and c_has_0_6 and not c_has_7_59):
        return -0.04
    return 0.0


def _adaptive_retrieval_top_k(query: str, intent: str, base_top_k: int) -> int:
    """Scale retrieval_top_k up for harder queries (multi-signal,
    cross-guideline) so the reranker has more candidates to work with, and
    leave simple single-sign queries at the base value to keep latency down.
    """
    q_low = query.lower()
    cross_terms = ["who", "aap", "nice", "compare", "versus", "vs", "both guidelines"]
    multi_terms = ["and", "also", "plus", "as well", "additionally", "combination"]

    n_cross = sum(1 for t in cross_terms if t in q_low)
    n_multi = sum(1 for t in multi_terms if t in q_low)

    if n_cross >= 2:
        return int(base_top_k * 2.5)
    if n_multi >= 2 or intent in ("risk_assessment", "monitoring"):
        return int(base_top_k * 2.0)
    if intent == "treatment":
        return int(base_top_k * 1.5)
    return base_top_k


def _build_retrieval_groups(chunks: list[dict]) -> dict[str, str]:
    """Retrieval-time analogue of chunk.py's own boundary logic — groups
    chunks WITHOUT touching the underlying ingested data. Returns
    {chunk_id: group_key}; chunks sharing a group_key are returned together
    by retrieve_evidence() but occupy exactly ONE position in top_k, so a
    recommendation and its same-page companion paragraph can't both eat
    separate slots.

    A recommendation (all chunks sharing its metadata.recommendation_id)
    absorbs its page's un-anchored supporting/definitional chunks. If a
    page has more than one distinct recommendation, a supporting chunk only
    joins one of their groups when its metadata.subsection unambiguously
    matches exactly one of them; otherwise it stays its own singleton
    group. Chunks with no page/recommendation metadata (seed fallback
    chunks, anything ingested before rag/chunk.py existed) each get a
    singleton group keyed to their own chunk_id.
    """
    def cid(c: dict) -> str:
        return str(c.get("id", ""))

    def meta(c: dict) -> dict:
        return c.get("metadata") or {}

    group_of: dict[str, str] = {cid(c): cid(c) for c in chunks}

    by_page: dict[tuple[str, int], list[dict]] = {}
    for c in chunks:
        page = c.get("page_number")
        source = c.get("source_name", "")
        if page is not None and int(page) >= 0:
            by_page.setdefault((source, page), []).append(c)

    for (source, page), page_chunks in by_page.items():
        rec_ids = {meta(c).get("recommendation_id") for c in page_chunks if meta(c).get("recommendation_id")}
        if not rec_ids:
            continue

        anchor_key: dict[str, str] = {}
        for c in page_chunks:
            rid = meta(c).get("recommendation_id")
            if rid:
                key = f"{source}_pg{page}_{rid}"
                anchor_key[rid] = key
                group_of[cid(c)] = key

        single_rec = len(rec_ids) == 1
        for c in page_chunks:
            m = meta(c)
            if m.get("recommendation_id") or m.get("chunk_type") == "table":
                continue  # rec pieces already keyed above; tables never absorbed

            target_rid: str | None = None
            if single_rec:
                target_rid = next(iter(rec_ids))
            elif m.get("subsection"):
                matches = {
                    rid for rid in rec_ids
                    if any(meta(pc).get("recommendation_id") == rid and meta(pc).get("subsection") == m.get("subsection")
                           for pc in page_chunks)
                }
                if len(matches) == 1:
                    target_rid = next(iter(matches))

            if target_rid is not None:
                group_of[cid(c)] = anchor_key[target_rid]

    return group_of


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
    file_name: str = ""
    # Set when a retrieval GROUP (a recommendation chunk + its same-page/
    # same-recommendation_id supporting chunks — see _build_retrieval_groups)
    # is expanded: every member of the same group shares the same
    # group_rank, meaning together they occupied exactly ONE of the top_k
    # slots. 0 (default) means "ungrouped" or "sibling context appended
    # after selection" (see _context_expand_siblings) — i.e. it did not
    # consume a top_k slot of its own. Purely additive: EvidenceChunkResponse
    # (schemas.py) doesn't declare this field, so it's silently dropped for
    # any caller that constructs the response model via `**to_dict()`.
    group_rank: int = 0

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
        # Retrieval-time grouping (see _build_retrieval_groups docstring
        # below) — precomputed once per reload() since it only depends on
        # the corpus, not the query.
        self.chunk_group_key: dict[str, str] = {}
        self.group_members: dict[str, list[dict]] = {}

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
        self.chunk_group_key = _build_retrieval_groups(self.chunks)
        self.group_members = {}
        for c in self.chunks:
            gk = self.chunk_group_key.get(_chunk_id(c), _chunk_id(c))
            self.group_members.setdefault(gk, []).append(c)
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
                vectors.append(np.zeros(get_settings().embedding_dim, dtype=np.float32))
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
        file_name=meta.get("file_name", ""),
    )


def retrieve_evidence(
    query: str | None = None,
    risk_payload: dict | None = None,
    active_guideline: str = "NICE",
    source_filters: list[str] | None = None,
    top_k: int | None = None,
    refined_query=None,
    deltas: dict | None = None,
    context: Any = None,  # Optional[PatientContext] — see rag/patient_context_builder.py.
                          # Biases both risk-payload-driven queries (care plan) AND
                          # free-text queries (evidence search with a loaded patient)
                          # with the patient's current symptoms + sustained trends.
    use_metadata_reranking: bool = True,
    use_context_expansion: bool = True,
    adaptive_top_k: bool = True,
    use_mmr: bool = True,
) -> list[EvidenceChunkResult]:
    """
    Hybrid search -> RRF -> dedupe -> cross-encoder rerank (graceful
    degrade on failure) -> metadata-aware final scoring -> MMR -> sibling
    context expansion -> (empty-candidates fallback).

    The four `use_*`/`adaptive_top_k` flags are all togglable ablation
    switches (default True = full pipeline), mirroring the offline eval
    harness's retrieve_evidence_harness() exactly:

    `use_metadata_reranking`: replaces the flat cross-encoder-plus-nudge
    score with the compound formula
        FinalScore = 0.80 * CrossEncoder
                   + 0.10 * SectionWeight(chunk_type, intent)
                   + 0.05 * AgeMatch(query, chunk_text)
                   + 0.05 * GuidelineBoost
    addressing three measured failure modes: recommendation chunks ranked
    below background prose, age-band retrieval errors (7-59-day dosing
    served for a day-2 newborn), and an intent-blind reranker. The 0.80
    cross-encoder weight is deliberate — semantic relevance still
    dominates; the metadata terms are tie-breakers and edge-case
    correctors, not overrides.

    `use_context_expansion`: after final selection, appends sibling chunks
    of selected recommendation chunks as extra un-ranked context (see
    _context_expand_siblings).

    `adaptive_top_k`: scales settings.retrieval_top_k up for complex /
    cross-guideline queries so the reranker has more candidates.
    """
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
        query = build_clinical_query(risk_payload, active_guideline, deltas=deltas, context=context)
    if not query:
        query = "EOS neonatal sepsis management guidelines"

    # Free-text queries (evidence search) never go through build_clinical_query,
    # so if a patient is loaded as context here, its trend/symptom terms need
    # appending explicitly — build_clinical_query already handles this for the
    # risk_payload path above, so only do it here to avoid double-appending.
    if risk_payload is None and context is not None and hasattr(context, "query_context_terms"):
        extra_terms = context.query_context_terms()
        if extra_terms:
            query = query + " " + " ".join(extra_terms)

    # Optional query refinement ablation (behind config flag).
    if settings.enable_query_refinement and (risk_payload or context) and refined_query is None:
        from rag.query_refiner import refine_query
        refined_query = refine_query(query, risk_payload or {}, context=context)

    if refined_query is not None and refined_query.action == "decompose":
        from rag.query_refiner import RefinedQuery
        sub_results: list[list[EvidenceChunkResult]] = []
        for sub_q in refined_query.sub_queries:
            sub_results.append(
                retrieve_evidence(
                    query=sub_q,
                    risk_payload=None,
                    active_guideline=active_guideline,
                    source_filters=source_filters,
                    top_k=top_k,
                    refined_query=RefinedQuery(action="pass_through", sub_queries=[sub_q]),
                    deltas=deltas,
                    context=context,
                    use_metadata_reranking=use_metadata_reranking,
                    use_context_expansion=use_context_expansion,
                    adaptive_top_k=adaptive_top_k,
                    use_mmr=use_mmr,
                )
            )
        merged_ids = reciprocal_rank_fusion(
            sub_results,
            k=settings.rrf_k,
            id_fn=lambda c: c.chunk_id,
        )
        final_k = top_k or settings.rerank_top_k
        return [item for item, _ in merged_ids[:final_k]]

    if refined_query is not None and refined_query.sub_queries:
        query = refined_query.sub_queries[0]

    # HyDE (Hypothetical Document Embeddings): when refinement chose "hyde",
    # embed the LLM's hypothetical guideline passage instead of the query
    # itself for the SEMANTIC leg only — closes the lexical gap between a
    # short query and a long guideline passage better than embedding the
    # bare query does. BM25 below still uses `query` (the real text), never
    # the hypothetical passage, since lexical matching against invented
    # text would be actively misleading, not helpful.
    semantic_query_text = query
    if (
        refined_query is not None
        and refined_query.action == "hyde"
        and refined_query.hypothetical_document
    ):
        semantic_query_text = refined_query.hypothetical_document

    # ── Detect intent early — used in section weighting, context expansion,
    # and adaptive k (all three care about what kind of query this is). ────
    intent = _detect_query_intent(query)

    print(f"[Retrieval] Query: '{query[:100]}' guideline={active_guideline} intent={intent} store={len(store.chunks)} chunks")

    # get_embed_model()/get_reranker() download from HuggingFace Hub on their
    # first call in this process (cached forever after via @lru_cache — but
    # NOT cached if they raise). If the backend machine has no internet at
    # that exact moment (e.g. testing offline mode with a freshly-restarted
    # backend, before either model has ever loaded), this used to raise
    # uncaught all the way up to main.py and 500 the whole request — even
    # though the chunk store itself was already loaded and usable. Treat a
    # model-load failure the same as "no chunks": fall back to seed data
    # rather than crash the request.
    try:
        embed_model = get_embed_model()
    except Exception as e:
        print(f"[Retrieval] Embedding model unavailable ({e}) — seed fallback")
        return _fallback_chunks(query, active_guideline, top_k or settings.rerank_top_k)

    retrieve_k = settings.retrieval_top_k
    if adaptive_top_k:
        retrieve_k = _adaptive_retrieval_top_k(query, intent, retrieve_k)

    # Semantic search normally uses the query as written — embeddings already
    # capture meaning-level similarity, so synonym-stuffing here would just
    # dilute the vector. When HyDE fired above, semantic_query_text is the
    # hypothetical passage instead. BM25 is pure keyword overlap and always
    # uses the real query, expanded with same-concept clinical terms (see
    # rag/clinical_terms.py) to catch the "newborn blood infection" vs
    # "early-onset neonatal sepsis" mismatch that keyword search alone would
    # miss — it never sees the hypothetical passage.
    bm25_query = expand_query(query)

    semantic_hits = store.semantic_search(semantic_query_text, embed_model, top_k=retrieve_k)
    bm25_hits = store.bm25_search(bm25_query, top_k=retrieve_k)

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
    try:
        reranker = get_reranker()
        pairs = [(query, c.get("chunk_text", "")) for c in candidates]
        rerank_scores = [float(s) for s in reranker.predict(pairs)]
    except Exception as e:
        print(f"[Retrieval] Reranker unavailable ({e}) — using RRF order without reranking")
        # RRF already gave `candidates` a reasonable relevance order; skip
        # straight to returning the top-k from that order rather than
        # crashing the whole request over a model that couldn't load. Still
        # respects grouping, matching the "degraded but not broken" contract
        # the harness's own reranker-failure branch establishes.
        final_k = top_k or settings.rerank_top_k
        degraded = [(c, 1.0 - i * 0.01) for i, c in enumerate(candidates)]
        seen_groups: dict[str, tuple[dict, float]] = {}
        group_order: list[str] = []
        for chunk, score in degraded:
            gk = store.chunk_group_key.get(_chunk_id(chunk), _chunk_id(chunk))
            if gk not in seen_groups:
                seen_groups[gk] = (chunk, score)
                group_order.append(gk)
        selected = [seen_groups[gk] for gk in group_order[:final_k]]
        results = _expand_groups(store, selected)
        print(f"[Retrieval] Returning {len(results)} chunks (no rerank — degraded mode)")
        return results

    # ── FinalScore computation ────────────────────────────────────────────
    # Flat original: FinalScore = CrossEncoder + GuidelineNudge (flat
    # settings.guideline_nudge_weight for on-guideline chunks, 0 otherwise).
    #
    # Metadata-aware (default): FinalScore = 0.80*CrossEncoder +
    # 0.10*SectionWeight + 0.05*AgeMatch + 0.05*GuidelineBoost — see
    # use_metadata_reranking's docstring above. guideline_nudge_weight
    # itself is unchanged (still settings.guideline_nudge_weight, default
    # 0.15) — NOT lowered to compensate for the new SectionWeight term, per
    # the harness's own finding that lowering it let cross-guideline
    # near-ties through far too often (23/30 cases vs. an intended ~4-8).
    guideline_upper = active_guideline.upper()
    nudge_weight = settings.guideline_nudge_weight
    final_scores: list[float] = []
    for chunk, ce_score in zip(candidates, rerank_scores):
        source = str(chunk.get("source_name", "")).upper() + str(
            (chunk.get("metadata") or {}).get("source", "")
        ).upper()
        guideline_boost = nudge_weight if guideline_upper in source else 0.0

        if use_metadata_reranking:
            meta = chunk.get("metadata") or {}
            sw = _section_weight(meta.get("chunk_type", ""), intent)
            am = _age_band_match(query, chunk.get("chunk_text", ""))
            final = (0.80 * ce_score
                     + 0.10 * sw
                     + 0.05 * am
                     + 0.05 * guideline_boost)
        else:
            final = ce_score + guideline_boost

        final_scores.append(final)

    order = sorted(range(len(candidates)), key=lambda i: final_scores[i], reverse=True)

    # Retrieval-time grouping: collapse candidates to one representative per
    # group (its best-final-score member) BEFORE the MMR pool is built, so a
    # recommendation and its same-page companion paragraph can't both eat
    # separate top_k slots. Each group is expanded back out at the end.
    final_k = top_k or settings.rerank_top_k
    seen_groups: dict[str, tuple[dict, float]] = {}
    group_order: list[str] = []
    for i in order:
        chunk = candidates[i]
        gk = store.chunk_group_key.get(_chunk_id(chunk), _chunk_id(chunk))
        if gk not in seen_groups:
            seen_groups[gk] = (chunk, final_scores[i])
            group_order.append(gk)

    # Over-fetch before diversity selection so MMR has real alternatives to
    # pick from rather than just re-ordering an already-narrow top-k.
    pool_size = min(len(group_order), max(final_k * 3, final_k + 5))
    pool = [seen_groups[gk] for gk in group_order[:pool_size]]

    # ── MMR with embedding cosine similarity ─────────────────────────────
    if use_mmr:
        chunk_id_to_idx = {_chunk_id(c): i for i, c in enumerate(store.chunks)}
        embed_map: dict[str, np.ndarray] | None = None
        if store.embeddings is not None:
            norms = np.linalg.norm(store.embeddings, axis=1, keepdims=True) + 1e-10
            normed_embs = store.embeddings / norms
            embed_map = {
                _chunk_id(chunk): normed_embs[chunk_id_to_idx[_chunk_id(chunk)]]
                for chunk, _ in pool
                if _chunk_id(chunk) in chunk_id_to_idx
            } or None
        selected = _mmr_select(pool, k=final_k, lambda_mult=0.7, embed_map=embed_map)
    else:
        selected = pool[:final_k]

    results = _expand_groups(store, selected)

    # ── Sibling context expansion (appended after selection) ─────────────
    if use_context_expansion and store.group_members:
        results = _context_expand_siblings(results, store, intent)

    print(f"[Retrieval] Returning {len(results)} chunks (MMR-diversified from pool of {len(pool)}), "
          f"top score={results[0].similarity_score if results else 'N/A'}")
    return results


def _mmr_select(
    pool: list[tuple[dict, float]],
    k: int,
    lambda_mult: float = 0.7,
    embed_map: dict[str, np.ndarray] | None = None,
) -> list[tuple[dict, float]]:
    """
    Maximal Marginal Relevance selection over a candidate pool.

    Flat vector/BM25 RAG tends to return several near-duplicate chunks (same
    section split across overlapping windows, or the same fact repeated
    across two guideline PDFs) which wastes the LLM's limited context on
    redundant text instead of covering the query's different facets. MMR
    trades a little top-1 relevance for coverage: each pick balances its own
    rerank score against how different it is from what's already selected.

    When `embed_map` is provided (chunk_id -> normalised embedding vector,
    built from ChunkStore.embeddings), redundancy is measured by COSINE
    SIMILARITY between chunk embeddings instead of token-Jaccard overlap.
    Cosine similarity correctly treats synonymous clinical phrasing (e.g.
    "IV ampicillin" vs "ampicillin IM/IV") as similar, which Jaccard
    over-penalises as dissimilar even though the two convey the same fact —
    this was a measured failure mode in the offline eval harness. Falls
    back to Jaccard token overlap when embed_map is absent/incomplete.

    lambda_mult closer to 1.0 favours relevance; closer to 0.0 favours
    diversity. 0.7 keeps the top-ranked chunk first but meaningfully
    penalises near-duplicates after that.
    """
    if not pool:
        return []
    if len(pool) <= k:
        return pool

    def _cid(chunk: dict) -> str:
        return str(chunk.get("id", ""))

    if embed_map:
        def _sim(a: dict, b: dict) -> float:
            va, vb = embed_map.get(_cid(a)), embed_map.get(_cid(b))
            if va is None or vb is None:
                return 0.0
            return float(np.dot(va, vb))
    else:
        def _tokens(chunk: dict) -> set[str]:
            text = (chunk.get("chunk_text", "") or "").lower()
            return set(re.findall(r"[a-z0-9]{4,}", text))

        token_cache = {id(c): _tokens(c) for c, _ in pool}

        def _sim(a: dict, b: dict) -> float:
            ta, tb = token_cache[id(a)], token_cache[id(b)]
            if not ta or not tb:
                return 0.0
            union = ta | tb
            return len(ta & tb) / len(union) if union else 0.0

    remaining = list(pool)
    scores = [s for _, s in remaining]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0

    def norm(s: float) -> float:
        return (s - lo) / span

    selected: list[tuple[dict, float]] = [remaining.pop(0)]

    while remaining and len(selected) < k:
        best_idx, best_val = 0, float("-inf")
        for i, (chunk, score) in enumerate(remaining):
            max_sim = max((_sim(chunk, sel) for sel, _ in selected), default=0.0)
            mmr_val = lambda_mult * norm(score) - (1 - lambda_mult) * max_sim
            if mmr_val > best_val:
                best_val, best_idx = mmr_val, i
        selected.append(remaining.pop(best_idx))

    return selected


def _expand_groups(store: "ChunkStore", selected: list[tuple[dict, float]]) -> list[EvidenceChunkResult]:
    """Turns a list of (representative chunk, score) — one per chosen
    retrieval group, already in rank order — into the full result list:
    every member of a chosen group is returned, tagged with the same
    group_rank, so the group as a whole still counts as exactly one of the
    top_k positions."""
    out: list[EvidenceChunkResult] = []
    for rank, (chunk, score) in enumerate(selected, start=1):
        gk = store.chunk_group_key.get(_chunk_id(chunk), _chunk_id(chunk))
        members = store.group_members.get(gk) or [chunk]
        for m in members:
            result = _to_result(m, score)
            result.group_rank = rank
            out.append(result)
    return out


def _context_expand_siblings(
    results: list[EvidenceChunkResult],
    store: "ChunkStore",
    intent: str,
    max_siblings: int = 2,
) -> list[EvidenceChunkResult]:
    """After final selection, append sibling chunks (same source_name +
    page_number + recommendation_id as a selected chunk) that weren't
    already included, as un-ranked extra context (group_rank stays 0, so
    they never count against a top_k slot).

    Even when a recommendation IS retrieved, its immediately adjacent
    remarks/dosing notes often carry the practical detail an LLM needs to
    answer "what antibiotic / what dose" — this gives the LLM more usable
    material without inflating retrieval k. Siblings are ONLY appended,
    never reranked, so they can never push an actual recommendation chunk
    out of the context window. Only applies for 'treatment'/'monitoring'
    intent — for risk_assessment/definition/diagnosis, extra sibling
    remarks are usually noise.
    """
    if intent not in ("treatment", "monitoring"):
        return results

    included_ids = {r.chunk_id for r in results}
    by_id = {_chunk_id(c): c for c in store.chunks}
    siblings_to_add: list[EvidenceChunkResult] = []
    seen_sibling_ids: set[str] = set()

    for result in results:
        if len(siblings_to_add) >= max_siblings:
            break
        orig = by_id.get(result.chunk_id)
        if orig is None:
            continue
        orig_meta = orig.get("metadata") or {}
        orig_rec_id = orig_meta.get("recommendation_id")
        if not orig_rec_id or result.page_number is None:
            continue

        for candidate in store.chunks:
            cand_id = _chunk_id(candidate)
            if cand_id in included_ids or cand_id in seen_sibling_ids:
                continue
            if candidate.get("source_name") != orig.get("source_name"):
                continue
            if candidate.get("page_number") != orig.get("page_number"):
                continue
            cand_meta = candidate.get("metadata") or {}
            if cand_meta.get("recommendation_id") != orig_rec_id:
                continue
            seen_sibling_ids.add(cand_id)
            sib = _to_result(candidate, result.similarity_score * 0.8)  # slightly lower score to preserve order
            sib.group_rank = 0  # not a retrieval slot — appended context only
            siblings_to_add.append(sib)
            if len(siblings_to_add) >= max_siblings:
                break

    return results + siblings_to_add


def assemble_evidence_sections(
    retrieved: list[dict[str, Any]],
    max_sections: int = 6,
    max_chunks_per_section: int = 14,
) -> list[dict[str, Any]]:
    """
    Groups retrieved chunks into their full source SECTION — the document
    heading each chunk was filed under at ingestion (chunk.py/clean.py's
    heading detection, carried through as the `section` field on every row)
    — pulling in EVERY chunk belonging to that section from the full
    ChunkStore, not just the ones this particular retrieval happened to
    return. This is what makes a section legible and complete rather than a
    scattering of disconnected fragments (e.g. 19 separate WHO chunk cards,
    several of which don't make sense read in isolation) — the clinician
    sees the real section as it reads in the source document, with the
    chunks that were actually matched by their query marked so they can be
    highlighted in the UI, rather than every chunk in the section looking
    equally "found."

    `retrieved` is a list of dicts matching EvidenceChunkResult.to_dict()'s
    shape (chunk_id, source, source_name, section, chunk_text,
    similarity_score, region_tag, version, chunk_index, page_number,
    file_name) — the same shape both a fresh retrieve_evidence() call and
    the Flutter client's already-fetched search results serialize to, so
    this works uniformly whichever path fed it.

    Sections are ranked by their best-scoring retrieved chunk, capped at
    max_sections. Within a section, member chunks are capped at
    max_chunks_per_section (kept in reading order — page then chunk_index —
    so a capped section is still a coherent prefix, not an arbitrary
    scattering) to bound response size for a genuinely huge section.
    """
    store = get_chunk_store()
    if not store.chunks or not retrieved:
        return []

    retrieved_ids = {r.get("chunk_id", "") for r in retrieved}

    group_order: list[tuple[str, str]] = []
    group_best_score: dict[tuple[str, str], float] = {}
    for r in retrieved:
        key = (r.get("source_name", ""), r.get("section", ""))
        score = float(r.get("similarity_score", 0.0))
        if key not in group_best_score:
            group_order.append(key)
            group_best_score[key] = score
        else:
            group_best_score[key] = max(group_best_score[key], score)

    group_order.sort(key=lambda k: group_best_score[k], reverse=True)
    group_order = group_order[:max_sections]

    sections: list[dict[str, Any]] = []
    for source_name, section in group_order:
        members = [
            c for c in store.chunks
            if c.get("source_name") == source_name and c.get("section") == section
        ]
        members.sort(key=lambda c: (
            c.get("page_number") if c.get("page_number") is not None else 0,
            c.get("chunk_index") if c.get("chunk_index") is not None else 0,
        ))
        truncated = len(members) > max_chunks_per_section
        members = members[:max_chunks_per_section]
        if not members:
            continue

        section_chunks = []
        full_text_parts = []
        first_page = None
        file_name = ""
        source_code = ""
        for m in members:
            cid = _chunk_id(m)
            page = m.get("page_number")
            if first_page is None and page is not None:
                first_page = page
            meta = m.get("metadata") or {}
            if not file_name:
                file_name = meta.get("file_name", "")
            if not source_code:
                source_code = meta.get("source", "")
            text = m.get("chunk_text", "")
            full_text_parts.append(text)
            section_chunks.append({
                "chunk_id": cid,
                "chunk_text": text,
                "page_number": page,
                "highlighted": cid in retrieved_ids,
            })

        sections.append({
            "source": source_code,
            "source_name": source_name,
            "section": section,
            "file_name": file_name,
            "page_number": first_page,
            "region_tag": members[0].get("region_tag", ""),
            "version": members[0].get("version", ""),
            "similarity_score": group_best_score[(source_name, section)],
            "chunks": section_chunks,
            "full_text": "\n\n".join(full_text_parts),
            "truncated": truncated,
        })

    return sections



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