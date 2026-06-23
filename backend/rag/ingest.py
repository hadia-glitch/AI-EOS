"""Ingestion pipeline 7.3.1 — PDF → chunks → embeddings → Supabase."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from llama_index.core import Document, Settings as LlamaSettings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from config import get_settings
from db import get_supabase
from rag.document_mapping import DocumentMeta, infer_document_meta

from dotenv import load_dotenv
load_dotenv()
import os
print("SUPABASE_URL =", os.getenv("SUPABASE_URL"))
print("SUPABASE_KEY =", os.getenv("SUPABASE_SERVICE_KEY")) 
def _section_from_text(text: str, chunk_index: int) -> str:
    """Heuristic section title from chunk start."""
    first_line = text.strip().split("\n")[0][:120]
    if len(first_line) > 10:
        return first_line
    return f"Section {chunk_index + 1}"


def load_pdf_documents(directory: Path) -> list[tuple[Document, DocumentMeta]]:
    """Load PDFs using LlamaIndex SimpleDirectoryReader pattern."""
    from llama_index.core.readers import SimpleDirectoryReader

    pdf_files = sorted(directory.glob("**/*.pdf"))
    if not pdf_files:
        pdf_files = sorted(directory.glob("*.pdf"))

    documents: list[tuple[Document, DocumentMeta]] = []
    for pdf_path in pdf_files:
        meta = infer_document_meta(pdf_path.name)
        reader = SimpleDirectoryReader(input_files=[str(pdf_path)])
        docs = reader.load_data()
        for doc in docs:
            doc.metadata.update(
                {
                    "source_name": meta.source_name,
                    "source": meta.source,
                    "version": meta.version,
                    "region_tag": meta.region_tag,
                    "file_name": pdf_path.name,
                }
            )
            documents.append((doc, meta))
    return documents


def ingest_directory(directory: str | Path | None = None, clear_existing: bool = False) -> dict:
    """Run full ingestion pipeline."""
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
    documents_processed = 0

    if clear_existing:
        supabase.table("evidence_chunks").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        supabase.table("guideline_documents").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

    doc_pairs = load_pdf_documents(guidelines_dir)
    if not doc_pairs:
        return {
            "status": "no_pdfs",
            "message": f"No PDF files in {guidelines_dir}",
            "chunks_inserted": 0,
        }

    # Group by file for document registry
    by_file: dict[str, tuple[DocumentMeta, list[Document]]] = {}
    for doc, meta in doc_pairs:
        fname = doc.metadata.get("file_name", "unknown.pdf")
        by_file.setdefault(fname, (meta, []))[1].append(doc)

    for file_name, (meta, docs) in by_file.items():
        doc_row = (
            supabase.table("guideline_documents")
            .insert(
                {
                    "name": file_name,
                    "source": meta.source,
                    "version": meta.version,
                    "region_tag": meta.region_tag,
                    "ingested_at": ingestion_date,
                    "active": True,
                }
            )
            .execute()
        )
        document_id = doc_row.data[0]["id"]
        documents_processed += 1

        chunk_index = 0
        batch: list[dict] = []

        for doc in docs:
            nodes = splitter.get_nodes_from_documents([doc])
            for node in nodes:
                text = node.get_content()
                if not text or len(text.strip()) < 20:
                    continue

                embedding = embed_model.get_text_embedding(text)
                section = node.metadata.get("section") or _section_from_text(text, chunk_index)

                batch.append(
                    {
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
                    }
                )
                chunk_index += 1

                if len(batch) >= 50:
                    supabase.table("evidence_chunks").insert(batch).execute()
                    total_inserted += len(batch)
                    batch = []

        if batch:
            supabase.table("evidence_chunks").insert(batch).execute()
            total_inserted += len(batch)

    return {
        "status": "success",
        "documents_processed": documents_processed,
        "chunks_inserted": total_inserted,
        "ingestion_date": ingestion_date,
    }


def main():
    parser = argparse.ArgumentParser(description="NeoGuard AI guideline ingestion")
    parser.add_argument("--dir", default=None, help="Guidelines PDF directory")
    parser.add_argument("--clear", action="store_true", help="Clear existing chunks first")
    args = parser.parse_args()
    result = ingest_directory(args.dir, clear_existing=args.clear)
    print(result)


if __name__ == "__main__":
    main()
