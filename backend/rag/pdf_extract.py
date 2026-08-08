"""
pdf_extract.py — Structure-aware PDF extraction for clinical guideline PDFs.

Uses PyMuPDF (fitz) for per-line font/size/bold metadata (needed by clean.py
for typography-based heading detection) and pdfplumber for table extraction
(tables are kept structurally separate so chunk.py can serialize them as
key:value prose rather than flattening them into noise).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import fitz          # PyMuPDF
import pdfplumber


@dataclass
class Line:
    text: str
    page: int
    size: float
    font: str
    bold: bool
    y: float   # top-of-line y-coordinate for reading order


@dataclass
class TableBlock:
    page: int
    markdown: str
    n_rows: int
    n_cols: int


@dataclass
class Document:
    lines: list[Line] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    body_size: float = 0.0
    body_font: str = ""
    n_pages: int = 0


def _is_bold(font_name: str, flags: int) -> bool:
    if flags & (2 ** 4):
        return True
    fn = font_name.lower()
    return any(tok in fn for tok in ("bold", "black", "heavy", "semibold"))


def _extract_lines(doc: fitz.Document) -> list[Line]:
    lines: list[Line] = []
    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                rep = max(spans, key=lambda s: len(s["text"]))
                size = round(rep["size"], 1)
                font = rep["font"]
                bold = any(_is_bold(s["font"], s["flags"]) for s in spans)
                y = line["bbox"][1]
                lines.append(Line(text=text, page=pno, size=size, font=font, bold=bold, y=y))
    return lines


def _compute_body_style(lines: list[Line]) -> tuple[float, str]:
    """Body text = the (size, font) pair with the most cumulative characters."""
    weights: dict[tuple[float, str], int] = {}
    for ln in lines:
        key = (ln.size, ln.font)
        weights[key] = weights.get(key, 0) + len(ln.text)
    if not weights:
        return 9.5, ""
    (size, font), _ = max(weights.items(), key=lambda kv: kv[1])
    return size, font


def _table_to_markdown(table: list[list[str | None]]) -> str:
    rows = [[(c or "").strip().replace("\n", " ") for c in row] for row in table]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return ""
    header, *body = rows
    sep = ["---"] * len(header)
    out = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in body:
        r = (r + [""] * len(header))[: len(header)]
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _extract_tables(pdf_path: str) -> list[TableBlock]:
    tables: list[TableBlock] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages):
            try:
                found = page.extract_tables()
            except Exception:
                found = []
            for t in found:
                if not t or len(t) < 2:
                    continue
                n_cols = max(len(r) for r in t)
                if n_cols < 2:
                    continue   # 1-column "tables" are usually mis-detected running text
                md = _table_to_markdown(t)
                if md:
                    tables.append(TableBlock(page=pno, markdown=md, n_rows=len(t), n_cols=n_cols))
    return tables


def extract_document(pdf_path: str) -> Document:
    doc = fitz.open(pdf_path)
    lines = _extract_lines(doc)
    body_size, body_font = _compute_body_style(lines)
    tables = _extract_tables(pdf_path)
    return Document(
        lines=lines,
        tables=tables,
        body_size=body_size,
        body_font=body_font,
        n_pages=len(doc),
    )