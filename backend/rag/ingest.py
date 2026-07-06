"""
Ingestion pipeline — PDF → clean text → chunks → embeddings → Supabase.

Production-grade cleaning removes:
  - "Author Manuscript" watermarks (NIH/PMC PDFs)
  - Page headers/footers (running titles, page numbers, dates)
  - Citation boilerplate lines
  - Reference list entries
  - Short noise lines (< 60 chars that are not headings)
  - Repeated whitespace and encoding artifacts
"""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from llama_index.core import Document, Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from config import get_settings
from db import get_supabase
from rag.document_mapping import DocumentMeta, infer_document_meta


# ── Noise patterns to strip from PDF text ─────────────────────────────────────

# Lines matching any of these regexes are removed before chunking
_NOISE_LINE_PATTERNS: list[re.Pattern] = [
    # NIH / PMC author manuscript watermarks
    re.compile(r"author\s+manuscript", re.I),
    re.compile(r"HHS\s+Public\s+Access", re.I),
    re.compile(r"NIH-PA\s+Author", re.I),
    re.compile(r"Europe\s+PMC\s+Funders", re.I),
    re.compile(r"PMC\s+Canada\s+Author", re.I),
    re.compile(r"manuscript\s+author\s+manuscript", re.I),
    # Page numbers / running headers
    re.compile(r"^\s*\d{1,4}\s*$"),                          # bare page numbers
    re.compile(r"^\s*Page\s+\d+\s+(of\s+\d+)?\s*$", re.I), # "Page 3 of 10"
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),                     # "- 3 -"
    # DOI / URL lines that are pure metadata
    re.compile(r"^\s*doi:\s*10\.", re.I),
    re.compile(r"^\s*https?://\S+\s*$"),
    # Copyright lines
    re.compile(r"copyright\s*(©|\(c\))", re.I),
    re.compile(r"©\s*\d{4}", re.I),
    re.compile(r"all rights reserved", re.I),
    # Journal / publisher running headers (common patterns)
    re.compile(r"^\s*(J\s+)?Pediatr(ics)?\s*\.", re.I),
    re.compile(r"^\s*Pediatrics\s+\d{4}", re.I),
    re.compile(r"^\s*\w+\s+et\s+al\.\s*$", re.I),           # bare "Smith et al."
    # Reference list entries: start with [1] or 1.
    re.compile(r"^\s*\[\d+\]"),
    re.compile(r"^\s*\d+\.\s+[A-Z][a-z]+\s+[A-Z]"),        # "1. Smith J, ..."
    # Conflict of interest / funding boilerplate
    re.compile(r"conflict\s+of\s+interest", re.I),
    re.compile(r"financial\s+disclosure", re.I),
    re.compile(r"supported\s+by\s+(a\s+)?grant", re.I),
    re.compile(r"^\s*(Funding|Acknowledgment|Disclosure)s?\s*:?\s*$", re.I),
    # Region tags / population descriptors that appear as standalone lines in some PDFs
    re.compile(r"^(South East Asian|Middle Eastern|Latin American|Sub-Saharan)\b.*$", re.I),
]

# Entire chunk is discarded if it matches these
_NOISE_CHUNK_PATTERNS: list[re.Pattern] = [
    re.compile(r"(author\s+manuscript\s*){2,}", re.I),  # repeated watermark
    re.compile(r"^(\s*\d+\s*){5,}$"),                   # page number soup
]

# Known heading keywords — short lines with these are KEPT even if < 60 chars
_HEADING_KEYWORDS = re.compile(
    r"\b(introduction|background|methods|results|discussion|conclusion|"
    r"recommendation|management|treatment|antibiotic|diagnosis|sepsis|"
    r"risk|assessment|clinical|neonatal|maternal|laboratory|monitoring|"
    r"guideline|protocol|evidence|summary|action|plan)\b",
    re.I,
)

# Minimum meaningful content length for a chunk
_MIN_CHUNK_CHARS = 150
_MIN_LINE_CHARS = 40  # lines shorter than this are dropped unless they're headings


def _clean_text(raw: str) -> str:
    """
    Remove PDF extraction artifacts from raw text.
    Returns cleaned text, or empty string if the page is mostly noise.
    """
    lines = raw.split("\n")
    cleaned: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Skip empty lines (will be normalised later)
        if not stripped:
            cleaned.append("")
            continue

        # Drop lines matching noise patterns
        if any(p.search(stripped) for p in _NOISE_LINE_PATTERNS):
            continue

        # Drop very short lines that are not meaningful headings
        if len(stripped) < _MIN_LINE_CHARS and not _HEADING_KEYWORDS.search(stripped):
            # Keep if it looks like a numbered list item or bullet
            if not re.match(r"^\s*[\d\-•*]\s+\S", stripped):
                continue

        cleaned.append(stripped)

    # Collapse multiple blank lines into one
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    # Normalise whitespace within lines
    result = re.sub(r"[ \t]{2,}", " ", result)
    # Remove lines that are just punctuation or numbers after cleaning
    result = "\n".join(
        l for l in result.split("\n")
        if not re.match(r"^\s*[.,;:\-–—_|/\\*•·]\s*$", l)
    )
    return result.strip()


def _is_noise_chunk(text: str) -> bool:
    """Return True if the chunk is too noisy to be useful."""
    if len(text.strip()) < _MIN_CHUNK_CHARS:
        return True
    for p in _NOISE_CHUNK_PATTERNS:
        if p.search(text):
            return True
    # If more than 40% of words are "author" or "manuscript", discard
    words = text.lower().split()
    if words:
        noise_words = sum(1 for w in words if w in ("author", "manuscript", "hhs", "pmc", "nih"))
        if noise_words / len(words) > 0.3:
            return True
    return False


def _extract_section_title(text: str, chunk_index: int, doc_section_map: dict) -> str:
    """
    Extract a meaningful section title from chunk text.
    Looks for heading-like lines: ALL CAPS, Title Case short lines, or
    lines containing known clinical heading keywords.
    Falls back to first meaningful sentence.
    """
    lines = text.strip().split("\n")

    for line in lines[:5]:  # check first 5 lines for a heading
        stripped = line.strip()
        if not stripped or len(stripped) < 3:
            continue
        # ALL CAPS heading (common in guidelines)
        if stripped.isupper() and 4 < len(stripped) < 80:
            return stripped.title()
        # Short Title Case line likely to be a heading
        if len(stripped) < 80 and stripped[0].isupper() and _HEADING_KEYWORDS.search(stripped):
            return stripped
        # Numbered section e.g. "1.3 Management of suspected infection"
        if re.match(r"^\d+(\.\d+)*\.?\s+[A-Z]", stripped) and len(stripped) < 100:
            return re.sub(r"^\d+(\.\d+)*\.?\s+", "", stripped)

    # Fall back to first sentence of the chunk (max 80 chars)
    first_sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    if len(first_sentence) > 80:
        first_sentence = first_sentence[:77] + "…"
    return first_sentence if len(first_sentence) > 10 else f"Section {chunk_index + 1}"


# ── PDF loading ────────────────────────────────────────────────────────────────

def load_pdf_documents(directory: Path) -> list[tuple[Document, DocumentMeta]]:
    from llama_index.core.readers import SimpleDirectoryReader

    pdf_files = sorted(directory.glob("**/*.pdf"))
    if not pdf_files:
        pdf_files = sorted(directory.glob("*.pdf"))

    print(f"[Ingest] Found {len(pdf_files)} PDF file(s) in {directory}")

    documents: list[tuple[Document, DocumentMeta]] = []
    for pdf_path in pdf_files:
        meta = infer_document_meta(pdf_path.name)
        print(f"[Ingest] Loading: {pdf_path.name} → {meta.source_name}")
        try:
            reader = SimpleDirectoryReader(input_files=[str(pdf_path)])
            docs = reader.load_data()
            for doc in docs:
                # Clean the text at load time
                original_text = doc.get_content()
                cleaned = _clean_text(original_text)
                if len(cleaned) < 100:
                    continue  # page is mostly noise, skip

                # Document.text is read-only in this LlamaIndex version —
                # construct a fresh Document with the cleaned text instead
                # of mutating the existing one.
                new_metadata = dict(doc.metadata)
                new_metadata.update({
                    "source_name": meta.source_name,
                    "source": meta.source,
                    "version": meta.version,
                    "region_tag": meta.region_tag,
                    "file_name": pdf_path.name,
                })
                cleaned_doc = Document(text=cleaned, metadata=new_metadata)
                documents.append((cleaned_doc, meta))
        except Exception as e:
            print(f"[Ingest] ERROR loading {pdf_path.name}: {e}")

    return documents


# ── Main ingestion pipeline ────────────────────────────────────────────────────

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

    embed_model = HuggingFaceEmbedding(model_name=settings.embedding_model)
    LlamaSettings.embed_model = embed_model

    splitter = SentenceSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    supabase = get_supabase()
    ingestion_date = datetime.now(timezone.utc).isoformat()
    total_inserted = 0
    total_skipped = 0
    documents_processed = 0

    if clear_existing:
        print("[Ingest] Clearing existing chunks and documents…")
        supabase.table("evidence_chunks").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()
        supabase.table("guideline_documents").delete().neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()

    doc_pairs = load_pdf_documents(guidelines_dir)
    if not doc_pairs:
        return {
            "status": "no_pdfs",
            "message": f"No PDF files in {guidelines_dir}",
            "chunks_inserted": 0,
        }

    # Group pages by file
    by_file: dict[str, tuple[DocumentMeta, list[Document]]] = {}
    for doc, meta in doc_pairs:
        fname = doc.metadata.get("file_name", "unknown.pdf")
        if fname not in by_file:
            by_file[fname] = (meta, [])
        by_file[fname][1].append(doc)

    for file_name, (meta, docs) in by_file.items():
        print(f"[Ingest] Processing {file_name} ({len(docs)} pages)…")

        doc_row = (
            supabase.table("guideline_documents")
            .insert({
                "name": file_name,
                "source": meta.source,
                "version": meta.version,
                "region_tag": meta.region_tag,
                "ingested_at": ingestion_date,
                "active": True,
            })
            .execute()
        )
        document_id = doc_row.data[0]["id"]
        documents_processed += 1

        chunk_index = 0
        batch: list[dict] = []
        section_map: dict = {}

        for doc in docs:
            nodes = splitter.get_nodes_from_documents([doc])
            for node in nodes:
                text = node.get_content()

                # Skip noise chunks
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue

                # Final clean pass on the chunk itself
                text = _clean_text(text)
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue

                embedding = embed_model.get_text_embedding(text)
                section = _extract_section_title(text, chunk_index, section_map)

                batch.append({
                    "document_id": document_id,
                    "chunk_text": text,
                    "embedding": embedding,
                    "section": section,
                    "page_number": node.metadata.get("page_label"),
                    "chunk_index": chunk_index,
                    "source_name": meta.source_name,
                    "version": meta.version,
                    "region_tag": meta.region_tag,
                    "ingestion_date": ingestion_date,
                    "metadata": {
                        "file_name": file_name,
                        "source": meta.source,
                        "node_id": hashlib.md5(text[:200].encode()).hexdigest()[:12],
                    },
                })
                chunk_index += 1

                if len(batch) >= 50:
                    supabase.table("evidence_chunks").insert(batch).execute()
                    total_inserted += len(batch)
                    print(f"[Ingest]   … inserted {total_inserted} chunks so far")
                    batch = []

        if batch:
            supabase.table("evidence_chunks").insert(batch).execute()
            total_inserted += len(batch)

        print(f"[Ingest] {file_name}: {chunk_index} clean chunks, {total_skipped} noise chunks skipped")

    print(f"[Ingest] Complete: {total_inserted} chunks inserted, {total_skipped} noise chunks discarded")
    return {
        "status": "success",
        "documents_processed": documents_processed,
        "chunks_inserted": total_inserted,
        "noise_chunks_discarded": total_skipped,
        "ingestion_date": ingestion_date,
    }


def main():
    parser = argparse.ArgumentParser(description="NeoGuard AI guideline ingestion")
    parser.add_argument("--dir", default=None, help="Guidelines PDF directory")
    parser.add_argument(
        "--clear", action="store_true",
        help="Clear existing chunks first (recommended when re-ingesting)"
    )
    args = parser.parse_args()
    result = ingest_directory(args.dir, clear_existing=args.clear)
    print(result)


if __name__ == "__main__":
    main()