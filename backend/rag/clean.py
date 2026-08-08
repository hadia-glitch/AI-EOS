"""
clean.py — Noise removal + paragraph/heading reconstruction for clinical PDFs.

Fixes applied:
  #1  Front-matter pages (ISBN/copyright/CC-license/contributors/funding)
      detected by keyword density and dropped wholesale.
  #3  Table of contents detected by dot-leader / trailing-page-number line
      density and dropped.
  #9  Repeated running headers/footers stripped cross-page by normalised-text
      frequency (digits collapsed so "Page 3" and "Page 4" hash together).
  #6/#10  Broken paragraphs / mid-sentence PDF line-wraps rejoined into real
      paragraphs BEFORE any short-line filtering — fixing the old pipeline's
      Bug 1 where a bare len(line) < 40 filter deleted most content because
      wrapped PDF lines are typically 30–50 chars.
  Heading detection uses real typography (bold / larger font from
  pdf_extract.py) with regex numbering / ALL-CAPS as fallback.
  Admin sections (References, Acknowledgements, Committee members) are
  skipped and not indexed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from rag.pdf_extract import Document, Line

# ---------------------------------------------------------------------------
# Front matter / TOC / boilerplate detection
# ---------------------------------------------------------------------------

_FRONT_MATTER_KEYWORDS = re.compile(
    r"\b(isbn|copyright|creative commons|all rights reserved|"
    r"licen[cs]e|licensing|contributors?|acknowledge?ments?|funding|"
    r"financial disclosure|conflicts? of interest|preface|foreword|"
    r"table of contents|how to cite|corresponding author|"
    r"published by|printed in|graphic design)\b",
    re.I,
)

_TOC_LINE_RE = re.compile(
    r"(\.\s*){3,}\s*\d{1,4}\s*$"
    r"|^\s*\d{1,2}(\.\d{1,2}){0,3}\.?\s+.{3,90}\s+\d{1,4}\s*$"
)

_ADMIN_SECTION_HEADING_RE = re.compile(
    r"^(acknowledge?ments?|declarations? of interests?|conflicts? of interest|"
    r"committee (on|membership)|contributors?|peer review(ers?)?|"
    r"guideline development group|steering committee|annex(es)?(\s*\d+)?|"
    r"references?|bibliography|abbreviations?( and acronyms)?|"
    r"lead authors?|ex officio|liaisons?|staff|\d{4}\s*[\u2013\-]\s*\d{4})\s*$",
    re.I,
)

_REFERENCE_ENTRY_RE = re.compile(r"^\s*\d{1,3}\.\s+[A-Z][a-zA-Z\-']+\s+[A-Z]{1,3}(,|\s)")

_HEADER_FOOTER_KEYWORD_RE = re.compile(
    r"(downloaded from|by guest|pediatrics volume|"
    r"from the american academy of pediatrics|^\s*page\s+\d+\s*$)",
    re.I,
)


def _normalize_for_repeat_check(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def detect_running_headers_footers(lines: list[Line], n_pages: int) -> set[str]:
    """A normalised line that recurs on ≥35% of pages is a running header/footer."""
    if n_pages <= 2:
        return set()
    per_page_texts: dict[int, set[str]] = {}
    for ln in lines:
        per_page_texts.setdefault(ln.page, set()).add(_normalize_for_repeat_check(ln.text))
    counts: Counter[str] = Counter()
    for texts in per_page_texts.values():
        counts.update(texts)
    threshold = max(2, int(n_pages * 0.35))
    return {t for t, c in counts.items() if c >= threshold and len(t) > 3}


def is_front_matter_page(page_lines: list[Line]) -> bool:
    text = " ".join(l.text for l in page_lines)
    if len(text) < 40:
        return False
    hits = len(_FRONT_MATTER_KEYWORDS.findall(text))
    words = max(1, len(text.split()))
    return hits >= 3 or (hits >= 1 and words < 120)


def is_toc_page(page_lines: list[Line]) -> bool:
    toc_hits = sum(1 for l in page_lines if _TOC_LINE_RE.search(l.text))
    return toc_hits >= 4 or (len(page_lines) > 0 and toc_hits / max(1, len(page_lines)) > 0.4)


# ---------------------------------------------------------------------------
# Heading detection (typography-first, regex fallback)
# ---------------------------------------------------------------------------

_NUMBERED_HEADING_RE = re.compile(r"^(\d+\.\d+(?:\.\d+){0,2})\.?\s+(.{3,100})$")
_ALLCAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 \-/&,()']{3,79}$")


def is_heading_line(ln: Line, body_size: float, body_font: str) -> bool:
    text = ln.text.strip()
    if not text or len(text) > 120:
        return False
    typographic = (ln.bold and ln.font != body_font) or (ln.size > body_size + 0.75)
    if typographic and len(text.split()) <= 14:
        return True
    if _NUMBERED_HEADING_RE.match(text) and len(text) < 100:
        return True
    if _ALLCAPS_HEADING_RE.match(text) and len(text.split()) <= 12:
        return True
    return False


# ---------------------------------------------------------------------------
# Paragraph reconstruction (fixes Bug 1: wrapped PDF lines)
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?:;\"'\u201d\u2019)\]]\s*$")
_BULLET_START_RE = re.compile(r"^\s*([•\-*●▪\u25b8]|\d+[.)]|\u2022)\s+")


def join_wrapped_lines(text_lines: list[str]) -> str:
    """
    Merge PDF line-wraps into real paragraphs: a line that does NOT end with
    terminal punctuation is glued to the next line with a space unless the next
    line starts a new bullet/list item.
    """
    if not text_lines:
        return ""
    out: list[str] = [text_lines[0]]
    for line in text_lines[1:]:
        prev = out[-1]
        starts_new_item = bool(_BULLET_START_RE.match(line))
        if not _SENTENCE_END_RE.search(prev) and not starts_new_item and prev.strip():
            out[-1] = prev.rstrip() + " " + line.lstrip()
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Block dataclass
# ---------------------------------------------------------------------------

@dataclass
class Block:
    kind: str   # "heading" | "paragraph" | "table"
    text: str
    page: int


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_clean_blocks(doc: Document, source_name: str) -> list[Block]:
    """
    Lines → filtered, paragraph-joined, heading-tagged blocks, with tables
    spliced in at the correct page in reading order.
    """
    hf = detect_running_headers_footers(doc.lines, doc.n_pages)

    pages: dict[int, list[Line]] = {}
    for ln in doc.lines:
        pages.setdefault(ln.page, []).append(ln)

    blocks: list[Block] = []
    dropped_front_matter = 0
    dropped_toc = 0
    skipping_admin = False

    for pno in sorted(pages):
        page_lines = pages[pno]

        if is_front_matter_page(page_lines):
            dropped_front_matter += 1
            continue
        if is_toc_page(page_lines):
            dropped_toc += 1
            continue

        kept: list[Line] = []
        for ln in page_lines:
            norm = _normalize_for_repeat_check(ln.text)
            if norm in hf:
                continue
            if _HEADER_FOOTER_KEYWORD_RE.search(ln.text) and len(ln.text) < 90:
                continue
            kept.append(ln)
        if not kept:
            continue

        buf: list[str] = []

        def flush(page: int = pno) -> None:
            if buf:
                joined = join_wrapped_lines(buf)
                if joined.strip():
                    blocks.append(Block(kind="paragraph", text=joined.strip(), page=page))
                buf.clear()

        for ln in kept:
            text = ln.text.strip()
            if is_heading_line(ln, doc.body_size, doc.body_font):
                flush()
                heading_body = _NUMBERED_HEADING_RE.match(text)
                heading_text = heading_body.group(2).strip() if heading_body else text
                skipping_admin = bool(_ADMIN_SECTION_HEADING_RE.match(heading_text))
                blocks.append(Block(kind="heading", text=text, page=pno))
                continue
            if skipping_admin:
                continue
            if _REFERENCE_ENTRY_RE.match(text):
                continue
            buf.append(text)
        flush()

        # Splice this page's tables in after text blocks
        for t in doc.tables:
            if t.page == pno:
                blocks.append(Block(kind="table", text=t.markdown, page=pno))

    print(
        f"[clean] {source_name}: dropped {dropped_front_matter} front-matter "
        f"page(s), {dropped_toc} TOC page(s), {len(hf)} repeated "
        f"header/footer line(s) stripped."
    )
    return blocks