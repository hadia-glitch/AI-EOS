"""Map PDF filenames to source metadata for ingestion."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DocumentMeta:
    source: str
    source_name: str
    version: str
    region_tag: str


def infer_document_meta(filename: str) -> DocumentMeta:
    """Infer guideline metadata from PDF filename."""
    lower = filename.lower()

    if "nice" in lower or "ng195" in lower:
        return DocumentMeta(
            source="NICE",
            source_name="NICE NG195",
            version="2026",
            region_tag="UK",
        )
    if "aap" in lower or "puopolo" in lower and "2018" in lower:
        return DocumentMeta(
            source="AAP",
            source_name="AAP EOS Guidelines 2023",
            version="2023",
            region_tag="USA",
        )
    if "who" in lower:
        return DocumentMeta(
            source="WHO",
            source_name="WHO Newborn Sepsis Guidelines",
            version="2024",
            region_tag="GLOBAL",
        )
    if "kuzniewicz" in lower or "2024" in lower or "2023065267" in lower:
        return DocumentMeta(
            source="EOSCAL",
            source_name="Kuzniewicz et al. 2024 EOSCAL Recalibration",
            version="2024",
            region_tag="GLOBAL",
        )
    if "2011" in lower or "e1155" in lower:
        return DocumentMeta(
            source="EOSCAL",
            source_name="Puopolo et al. 2011 Original EOSCAL",
            version="2011",
            region_tag="GLOBAL",
        )
    if "2019" in lower:
        return DocumentMeta(
            source="EOSCAL",
            source_name="Puopolo et al. 2019 EOSCAL Update",
            version="2019",
            region_tag="GLOBAL",
        )
  

    stem = Path(filename).stem
    return DocumentMeta(
        source="LOCAL",
        source_name=stem,
        version="1.0",
        region_tag="GENERAL",
    )
