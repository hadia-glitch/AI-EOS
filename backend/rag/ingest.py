"""
ingest.py — Supabase ingestion driver for the NeoGuard guideline RAG pipeline.

Replaces the previous llama_index-based fixed/semantic/proposition/"clinical
structure ablation" chunker with the structure-aware pipeline validated
offline (pdf_extract.py -> clean.py -> chunk.py, driven by run_ingest.py
against chunks_clinical_v2.csv in the eval harness). That harness produced
measurably better chunk quality against the target thresholds (0 chunks
under 250 chars outside of legitimately-short standalone recommendations, 0
chunks over 1200 chars, >=70% of chunks in the 350-900 char sweet spot,
recommendation + evidence + remarks kept as one retrievable unit instead of
being split apart) than the old ad-hoc splitter — so this file now wires
those same three modules directly into Supabase ingestion instead of
maintaining two divergent chunking implementations that could silently drift
apart (harness says one thing, production serves another).

Pipeline per PDF:
    pdf_extract.extract_document()   -- PyMuPDF line/typography + pdfplumber
                                         tables, structure preserved
    -> clean.build_clean_blocks()    -- front-matter/TOC/header-footer
                                         removal, paragraph reconstruction,
                                         typography-based heading detection,
                                         admin-section (references/
                                         acknowledgements/committee) skip
    -> chunk.build_units()           -- blocks -> section/subsection/
                                         recommendation-tagged units
    -> chunk.chunk_document()        -- clinical-boundary-aware splitting,
                                         recommendation+evidence+remarks
                                         packaging, table serialization,
                                         orphan merging, size validation,
                                         retrieval-oriented "Source: ...
                                         Section: ... Topic: ..." prefix
    -> embed each chunk's chunk_text and upsert into evidence_chunks.

Kept from the previous version, UNCHANGED:
  - _upload_pdf_to_storage() -- still uploads the raw PDF to Supabase
    Storage so GuidelinePdfViewerScreen / offline_pdf_cache.dart can serve
    it for the "jump to source page" feature.
  - guideline_documents row shape (name/source/version/region_tag/
    ingested_at/active/storage_path/storage_url) -- unchanged consumers:
    document_mapping-driven admin screens, offline_pdf_cache.dart's document
    list sync.
  - The public ingest_directory(directory=None, clear_existing=False) -> dict
    signature and return shape (status/documents_processed/chunks_inserted/
    ingestion_date) -- main.py's /api/v1/admin/guidelines/ingest and
    /rebuild-cache routes call this directly and need NO changes.
  - evidence_chunks column names retrieve.py's ChunkStore.reload() and
    _to_result() already read: document_id, chunk_text, embedding, section,
    page_number, chunk_index, source_name, version, region_tag, metadata
    (jsonb, with metadata["source"] read by retrieve.py's source_filter and
    by EvidenceChunkResult.source).

REMOVED: settings.chunking_strategy ablation (fixed/semantic/proposition/
clinical) and every _chunk_document_text*/_ClinicalUnit/_semantic_chunks/
_proposition_chunks/_split_into_blocks/_classify_block helper that
implemented it, along with the old regex-based _clean_text/_is_noise_chunk
noise stripping (clean.py's front-matter/TOC/header-footer detection
supersedes it with real per-page/typography logic instead of line-level
regexes). There is now exactly one chunking strategy — the validated
clinical pipeline — so config.py's `chunking_strategy` field is vestigial:
nothing in this file reads it anymore. Left in config.py rather than
deleted, since removing a settings field is a breaking change for any .env
that still sets CHUNKING_STRATEGY=... and pydantic-settings' extra="ignore"
already makes an unused key harmless.

NEW: AAP term/preterm disambiguation. NeoGuard now ingests four guideline
PDFs -- NICE, WHO, and TWO AAP documents (>=35 weeks "term" management and a
<35 weeks "preterm" companion). Both AAP files hit the same generic
filename/content signals in document_mapping.py, so without help they'd
collide under one indistinguishable source_name. See
rag/document_mapping.py's _refine_aap_gestational_scope() -- this file just
sniffs a couple of pages of each PDF's text with a cheap plain-text pass (no
duplicate structure-aware extraction) and passes that to
infer_document_meta() so the disambiguation has real content to check, not
just a filename guess.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from config import get_settings
from db import get_supabase
from rag.chunk import build_units, chunk_document
from rag.clean import build_clean_blocks
from rag.document_mapping import DocumentMeta, infer_document_meta
from rag.pdf_extract import extract_document

STORAGE_BUCKET = "guidelines"


def _upload_pdf_to_storage(
    supabase, local_path: Path, source: str, file_name: str
) -> tuple[str | None, str | None]:
    """
    Uploads the guideline PDF to Supabase Storage so the mobile app can
    download it once and cache it locally for offline viewing (see
    lib/data/offline_pdf_cache.dart and the "jump to source page" feature
    in evidence_search_screen.dart). Non-fatal on failure — ingestion still
    completes and the chunk stays searchable, it just won't have an offline
    PDF link until the next successful ingest.

    Requires a public "guidelines" bucket to exist in Supabase Storage
    (Dashboard -> Storage -> New bucket -> "guidelines" -> Public = ON).
    These are guideline PDFs, never patient data, so a public bucket is
    appropriate here (unlike anything in the patient_* tables).
    """
    if not local_path.exists():
        return None, None

    storage_path = f"{source}/{file_name}"
    try:
        with open(local_path, "rb") as f:
            supabase.storage.from_(STORAGE_BUCKET).upload(
                storage_path,
                f.read(),
                {"content-type": "application/pdf", "upsert": "true"},
            )
        storage_url = supabase.storage.from_(STORAGE_BUCKET).get_public_url(storage_path)
        print(f"[Ingest] Uploaded {file_name} to Storage: {storage_path}")
        return storage_path, storage_url
    except Exception as e:
        print(f"[Ingest] Storage upload failed for {file_name} (non-fatal): {e}")
        return None, None


def _sniff_first_pages(pdf_path: Path, max_pages: int = 2, max_chars: int = 6000) -> str:
    """
    Cheap plain-text pull of the first couple of pages, used ONLY to feed
    document_mapping.infer_document_meta()'s content-sniffing signal (source
    organisation name, NG number, DOI prefix, term/preterm wording). This is
    deliberately NOT the same as extract_document() below -- no typography,
    no tables, no per-line metadata -- because that full structure-aware
    pass runs once per file anyway right after this, and duplicating it here
    just to read two pages of plain text would double PDF-parsing cost for
    no benefit. Never raises: a failed sniff just means document_mapping
    falls back to filename-only matching (still correct for filenames that
    already carry a strong signal, e.g. "nice_ng195...pdf").
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        parts = [doc[p].get_text() for p in range(min(max_pages, len(doc)))]
        doc.close()
        return "\n".join(parts)[:max_chars]
    except Exception as e:
        print(f"[Ingest] Could not sniff {pdf_path.name} for content signals (non-fatal): {e}")
        return ""


def _chunk_source_label(meta: DocumentMeta) -> str:
    """
    The `source` string chunk.py embeds in every chunk's "Source: {source}"
    retrieval prefix and uses to build chunk_id (chunk_document(units,
    source)). Uses the (possibly AAP-term/preterm-refined) source_name so
    the two AAP documents produce distinguishable chunk_ids and prefix text
    instead of colliding under the bare "AAP" code -- e.g.
    "AAP EOS Guidelines — Preterm (<35 Weeks' Gestation)" rather than just
    "AAP" for both files. Falls back to the canonical source code if
    source_name was never set for some reason.
    """
    return meta.source_name or meta.source


def ingest_directory(
    directory: str | Path | None = None,
    clear_existing: bool = False,
) -> dict:
    settings = get_settings()
    guidelines_dir = Path(directory or settings.guidelines_dir)

    if not guidelines_dir.exists():
        guidelines_dir.mkdir(parents=True, exist_ok=True)
        return {
            "status": "no_pdfs",
            "message": f"No PDFs found. Place guideline PDFs in {guidelines_dir}",
            "chunks_inserted": 0,
        }

    pdf_paths = sorted(guidelines_dir.glob("*.pdf"))
    if not pdf_paths:
        return {
            "status": "no_pdfs",
            "message": f"No PDF files in {guidelines_dir}",
            "chunks_inserted": 0,
        }

    embed_model = HuggingFaceEmbedding(model_name=settings.embedding_model)
    supabase = get_supabase()
    ingestion_date = datetime.now(timezone.utc).isoformat()

    if clear_existing:
        print("[Ingest] Clearing existing chunks and documents…")
        supabase.table("evidence_chunks").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()
        supabase.table("guideline_documents").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()

    total_inserted = 0
    total_discarded = 0  # chunks chunk.py's own _is_meaningful() validation dropped
    documents_processed = 0
    mismatch_warnings: list[dict] = []

    for pdf_path in pdf_paths:
        file_name = pdf_path.name
        print(f"\n[Ingest] === Processing {file_name} ===")

        sniff_text = _sniff_first_pages(pdf_path)
        meta = infer_document_meta(file_name, sniff_text)
        print(
            f"[Ingest]   Mapped -> source={meta.source} version={meta.version} "
            f"region={meta.region_tag} confidence={meta.confidence} "
            f"matched_by={meta.matched_by}"
            + (f" scope={meta.gestational_scope}" if meta.gestational_scope else "")
        )
        if meta.mismatch_warning:
            print(f"[Ingest]   WARNING: {meta.mismatch_warning}")
            mismatch_warnings.append({"file": file_name, "warning": meta.mismatch_warning})

        storage_path, storage_url = _upload_pdf_to_storage(
            supabase, pdf_path, meta.source, file_name
        )

        doc_row = (
            supabase.table("guideline_documents")
            .insert({
                "name": file_name,
                "source": meta.source,
                "version": meta.version,
                "region_tag": meta.region_tag,
                "ingested_at": ingestion_date,
                "active": True,
                "storage_path": storage_path,
                "storage_url": storage_url,
            })
            .execute()
        )
        document_id = doc_row.data[0]["id"]
        documents_processed += 1

        # ── Structure-aware extraction -> clean -> chunk ────────────────────
        doc = extract_document(str(pdf_path))
        print(
            f"[Ingest]   [extract] {len(doc.lines)} lines, {doc.n_pages} pages, "
            f"{len(doc.tables)} table(s), body style = {doc.body_size}pt {doc.body_font!r}"
        )

        source_label = _chunk_source_label(meta)
        blocks = build_clean_blocks(doc, source_label)
        units = build_units(blocks)
        chunks = chunk_document(units, source_label)
        print(f"[Ingest]   [chunk] {len(chunks)} chunk(s) built")

        # ── Embed + insert ───────────────────────────────────────────────
        batch: list[dict] = []
        for i, c in enumerate(chunks):
            embedding = embed_model.get_text_embedding(c["chunk_text"])
            batch.append({
                "document_id": document_id,
                "chunk_text": c["chunk_text"],
                "embedding": embedding,
                "section": c["section"] or "General",
                # chunk.py's `page` is 0-indexed (straight from pdf_extract's
                # Line.page); evidence_chunks.page_number and the mobile
                # viewer's pageNumber are both 1-indexed.
                "page_number": c["page"] + 1,
                "chunk_index": i,
                "source_name": meta.source_name,
                "version": meta.version,
                "region_tag": meta.region_tag,
                "ingestion_date": ingestion_date,
                "metadata": {
                    "source": meta.source,
                    "file_name": file_name,
                    "chunk_id": c["chunk_id"],
                    "subsection": c["subsection"],
                    "recommendation_id": c["recommendation_id"],
                    "chunk_type": c["chunk_type"],
                    "chars": c["chars"],
                    "gestational_scope": meta.gestational_scope,
                    "confidence": meta.confidence,
                    "matched_by": meta.matched_by,
                    "node_id": hashlib.md5(c["chunk_text"][:200].encode()).hexdigest()[:12],
                },
            })
            if len(batch) >= 50:
                supabase.table("evidence_chunks").insert(batch).execute()
                total_inserted += len(batch)
                print(f"[Ingest]   … inserted {total_inserted} chunks so far")
                batch = []

        if batch:
            supabase.table("evidence_chunks").insert(batch).execute()
            total_inserted += len(batch)

        print(f"[Ingest] {file_name}: {len(chunks)} chunks inserted")

    print(
        f"\n[Ingest] Complete: {total_inserted} chunks inserted across "
        f"{documents_processed} document(s), {total_discarded} discarded by validation"
    )
    return {
        "status": "success",
        "documents_processed": documents_processed,
        "chunks_inserted": total_inserted,
        "noise_chunks_discarded": total_discarded,
        "ingestion_date": ingestion_date,
        "mismatch_warnings": mismatch_warnings,
    }


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Ingest guideline PDFs into Supabase")
    ap.add_argument("--dir", default=None, help="Directory of PDFs (default: settings.guidelines_dir)")
    ap.add_argument("--clear", action="store_true", help="Clear existing chunks/documents first")
    args = ap.parse_args()

    result = ingest_directory(args.dir, clear_existing=args.clear)
    print(result)


if __name__ == "__main__":
    main()