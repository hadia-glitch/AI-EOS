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
from services.llm_provider import LLMUnavailableError, call_llm


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
    # Region tags / population descriptors that appear as standalone lines in
    # some PDFs (e.g. a chart legend or table row label). Anchored to the
    # WHOLE line (nothing else on it) rather than just the start, because
    # the un-anchored `.*$` version also matched real sentences that happen
    # to start with these words -- e.g. WHO's PSBI guideline discusses study
    # sites "in five countries in South Asia and sub-Saharan Africa" as
    # actual epidemiological content, not noise, and a PDF line-break right
    # before "sub-Saharan" would have silently deleted it.
    re.compile(r"^(South East Asian|Middle Eastern|Latin American|Sub-Saharan African)\s*$", re.I),
]

# Evidence-grading labels (recommendation strength / quality of evidence)
# that legitimately appear as very short, standalone lines when a guideline's
# grading table gets extracted cell-by-cell (e.g. WHO's "Strong" / "Moderate"
# column, or NICE/GRADE "high/moderate/low/very low"). Without this
# exception these fall under _MIN_LINE_CHARS and get silently dropped --
# losing the evidence-quality grading while keeping the recommendation text
# next to it, which defeats the point of a system meant to surface evidence
# quality alongside advice.
_GRADE_KEYWORDS = re.compile(
    r"^\s*(strong|weak|conditional|high|moderate|low|very\s+low)\s*$", re.I,
)

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
            # Keep if it looks like a numbered list item or bullet, or is a
            # standalone evidence-grading label (see _GRADE_KEYWORDS above)
            if not re.match(r"^\s*[\d\-•*]\s+\S", stripped) and not _GRADE_KEYWORDS.match(stripped):
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


def _split_sentences(text: str) -> list[str]:
    """Extract sentences via spaCy; fall back to regex if spaCy unavailable."""
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        doc = nlp(text)
        return [sent.text.strip() for sent in doc.sents if len(sent.text.strip()) > 20]
    except Exception:
        import re
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p.strip() for p in parts if len(p.strip()) > 20]


def _semantic_chunks(text: str, metadata: dict) -> list[tuple[str, dict]]:
    """
    spaCy sentences -> TF-IDF -> K-means clustering -> chronological concat.
    k = max(3, min(20, len(sentences) // 15)) — scales with document length.
    """
    sentences = _split_sentences(text)
    if len(sentences) < 3:
        return [(text, metadata)]

    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    # k formula for paper methods section — do not hardcode k=6 globally.
    k = max(3, min(20, len(sentences) // 15))
    k = min(k, len(sentences))

    vectorizer = TfidfVectorizer(max_features=500)
    matrix = vectorizer.fit_transform(sentences)
    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(matrix)

    clusters: dict[int, list[str]] = {}
    for idx, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(sentences[idx])

    chunks: list[tuple[str, dict]] = []
    for cluster_id in sorted(clusters.keys()):
        chunk_text = " ".join(clusters[cluster_id])
        meta = dict(metadata)
        meta["chunking_strategy"] = "semantic"
        meta["semantic_cluster"] = cluster_id
        chunks.append((chunk_text, meta))
    return chunks


def _proposition_chunks(text: str, metadata: dict, settings) -> list[tuple[str, dict]]:
    """Split section text into single-fact assertions via local LLM."""
    section = metadata.get("section", "General")
    prompt = f"""Split the following clinical guideline section into single-fact assertions.
Each assertion must be one standalone factual claim. Preserve clinical accuracy.
Return ONLY valid JSON: {{"propositions": ["fact 1", "fact 2", ...]}}

SECTION ({section}):
{text[:4000]}"""

    try:
        raw, _ = call_llm(
            prompt,
            settings=settings,
            system="You extract atomic clinical facts. Respond with valid JSON only.",
        )
        body = raw.strip()
        if body.startswith("```"):
            body = body.split("```", 2)[1]
            if body.startswith("json"):
                body = body[4:]
            body = body.rsplit("```", 1)[0].strip()
        import json
        data = json.loads(body)
        props = [str(p).strip() for p in data.get("propositions", []) if str(p).strip()]
    except (LLMUnavailableError, Exception):
        props = _split_sentences(text)

    if not props:
        return [(text, metadata)]

    chunks: list[tuple[str, dict]] = []
    for i, prop in enumerate(props):
        meta = dict(metadata)
        meta["chunking_strategy"] = "proposition"
        meta["proposition_index"] = i
        chunks.append((prop, meta))
    return chunks


# ═════════════════════════════════════════════════════════════════════════════
# Clinical Structure-Aware Adaptive Semantic Chunking (CSASC)
# ═════════════════════════════════════════════════════════════════════════════
#
# Fourth chunking strategy (chunking_strategy="clinical"), alongside fixed /
# semantic / proposition above. Where fixed splits by token count and
# semantic clusters+reorders sentences by topic, CSASC is built specifically
# for Clinical Practice Guideline documents (NICE/AAP/WHO-style), where the
# unit that actually matters for a clinician is not a paragraph or a fixed
# token window but a *clinical decision unit* (CDU): a recommendation
# together with whatever exceptions, contraindications, evidence notes, and
# monitoring guidance belong to it. Splitting a CDU across two chunks means
# retrieval can surface "start antibiotics" without its own "...unless
# penicillin-allergic" clause three lines later.
#
# This is a heuristic, regex/TF-IDF-based structural parser, not a trained
# document-layout model -- it works on the same cleaned plain text the other
# three strategies already receive (no access to real PDF layout/bounding
# boxes in this pipeline). It is deliberately conservative: anything it
# can't confidently classify falls through to a plain "paragraph" chunk
# rather than being force-fit into a recommendation/table/list bucket.
#
# Pipeline (mirrors the 10-stage design this was specified against):
#   1. Block segmentation      -> _split_into_blocks
#   2. Block classification    -> _classify_block
#   3. Hierarchy + CDU grouping -> _build_clinical_units
#   4. Adaptive per-type sizing + smart overlap -> _adaptive_split_unit
#   5. Metadata enrichment     -> folded into the Chunk objects below
#
# Stages "document tree" and "clinical integrity checks" are folded into
# _build_clinical_units (the orphaned-exception merge rule) rather than
# being separate passes, to keep this a single linear scan over blocks --
# a second full pass would double the (already non-trivial) cost of
# TF-IDF vectorising every block on top of the KMeans-based "semantic"
# strategy already in this file.


# ── Stage 1-2: block segmentation + classification ─────────────────────────

_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+(.*)$")
_ALLCAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 \-/&,()]{3,79}$")

_RECOMMENDATION_TRIGGER_RE = re.compile(
    r"\b(offer|consider|do not offer|do not use|give|administer|refer|take|perform|"
    r"discuss|start|initiate|obtain|prescribe|use)\b.{0,20}\b(should|must|do not|"
    r"is recommended|are recommended)?",
    re.I,
)
_RECOMMENDATION_MODAL_RE = re.compile(r"\b(should|must|shall|is recommended|are recommended)\b", re.I)

_EXCEPTION_RE = re.compile(
    r"\b(exception|contraindicat|do not (use|offer|extend|continue|start|give|discharge)\b|caution|unless|"
    r"without (senior|specialist|consultant)?\s?(review|discussion|input)|"
    r"should not be (offered|given|used|extended|continued)|not recommended (for|if|in)|avoid (in|if))\b", re.I,
)
_EVIDENCE_RE = re.compile(
    r"\b(evidence statement|rationale|quality of evidence|GRADE|systematic review|"
    r"randomi[sz]ed controlled trial|meta-analysis|committee opinion)\b", re.I,
)
_MONITORING_RE = re.compile(
    r"\b(monitor|review at|reassess|follow[- ]up|observe for|repeat (the )?(crp|culture|test)|"
    r"re-?evaluate)\b", re.I,
)

_BULLET_LINE_RE = re.compile(r"^\s*[•\-*●▪]\s+\S|^\s*\d+[.)]\s+\S")
_TABLE_UNIT_RE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|g|kg|ml|mL|mg/kg|units?|mmol|hours?|hrs?|days?)\b", re.I)

# NICE tags individual recommendations inline with a trailing review-era
# marker -- "[2021]", "[2012, amended 2021]", "[2026]" -- meaning
# recommendations from several different evidence-review eras coexist in
# one PDF. A single document-level `version` field (see document_mapping.py)
# can't express that. Captured per recommendation unit below and surfaced
# as chunk metadata so retrieval/fact-checking can reason about how current
# a specific recommendation is, not just the document as a whole.
_RECOMMENDATION_YEAR_RE = re.compile(r"\[\s*(\d{4}(?:\s*,\s*amended\s*\d{4})?)\s*\]", re.I)

# Front/back-matter section headings that are administrative, not clinical
# content -- committee rosters, declarations of interest, reference lists,
# annexes. These survive line-level noise filtering because each individual
# line (a name + affiliation, a citation) looks like ordinary prose; only
# recognisable as boilerplate at the section level. Matched against a
# heading block's text with numbering already stripped.
_ADMIN_SECTION_RE = re.compile(
    r"^(acknowledge?ments?|declarations? of interests?|conflicts? of interest|"
    r"committee membership|contributors?|peer review(ers?)?|"
    r"guideline development group|steering committee|annex(es)?(\s*\d+)?|"
    r"references?|bibliography|abbreviations? and acronyms)\s*$", re.I,
)

_PAGE_MARKER_RE = re.compile(r"<<<PAGE:(.*?)>>>")

_MIN_BLOCK_CHARS = 20


def _split_into_blocks(text: str) -> list[str]:
    """Blank-line-delimited paragraphs, further split so a numbered heading
    that shares a line-break with following prose becomes its own block
    (guideline PDFs frequently run a heading straight into its first
    sentence with only a single newline, not a blank line, once cleaned)."""
    raw_paragraphs = re.split(r"\n\s*\n", text)
    blocks: list[str] = []
    for para in raw_paragraphs:
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        current: list[str] = []
        for line in lines:
            stripped = line.strip()
            is_heading_line = _is_short_heading_line(stripped) or bool(_ALLCAPS_HEADING_RE.match(stripped))
            if is_heading_line and current:
                blocks.append("\n".join(current))
                current = [stripped]
            else:
                current.append(stripped)
        if current:
            blocks.append("\n".join(current))
    return [b for b in blocks if len(b.strip()) >= _MIN_BLOCK_CHARS or _is_short_heading_line(b.strip())
            or _PAGE_MARKER_RE.search(b)]


def _is_short_heading_line(line: str) -> bool:
    """True only for a standalone, low-depth numbered heading line (e.g.
    "1.3 Blood cultures") -- deliberately NOT true for a long numbered
    recommendation sentence (e.g. "1.3.2 Offer intravenous benzylpenicillin
    ..."), which must stay classified/handled as a recommendation, not get
    torn off as a heading boundary."""
    m = _NUMBERED_HEADING_RE.match(line)
    if not m:
        return False
    depth = m.group(1).count(".") + 1
    return depth <= 2 and len(line) < 120


def _classify_block(block: str) -> str:
    """Returns one of: heading, recommendation, exception, evidence,
    monitoring, table, bullet_list, paragraph."""
    stripped = block.strip()
    first_line = stripped.split("\n", 1)[0]

    m = _NUMBERED_HEADING_RE.match(first_line)
    if m:
        depth = m.group(1).count(".") + 1
        # Depth 1-2 ("1", "1.3") reads as a heading ONLY if that first line
        # is actually short (a real heading, not a recommendation sentence
        # that happens to start "1.2 patients who..."); depth 3+ ("1.3.2")
        # in a CPG is almost always a numbered recommendation regardless of
        # sentence length, so no length cap applies there.
        if depth <= 2 and len(first_line) < 120 and len(block.split("\n")) <= 2:
            return "heading"
        if depth >= 3:
            return "recommendation"

    if _ALLCAPS_HEADING_RE.match(first_line) and len(block.split("\n")) == 1:
        return "heading"

    lines = [l for l in stripped.split("\n") if l.strip()]
    bullet_lines = sum(1 for l in lines if _BULLET_LINE_RE.match(l))
    if lines and bullet_lines / len(lines) >= 0.6:
        return "bullet_list"

    table_lines = sum(1 for l in lines if _TABLE_UNIT_RE.search(l))
    if lines and len(lines) >= 3 and table_lines / len(lines) >= 0.5:
        return "table"

    if _EXCEPTION_RE.search(stripped):
        return "exception"
    if _EVIDENCE_RE.search(stripped):
        return "evidence"
    if _MONITORING_RE.search(stripped):
        return "monitoring"
    if _RECOMMENDATION_TRIGGER_RE.search(stripped) and _RECOMMENDATION_MODAL_RE.search(stripped):
        return "recommendation"

    return "paragraph"


# ── Stage 3: hierarchy tracking + clinical decision unit grouping ──────────

class _ClinicalUnit:
    __slots__ = ("chunk_type", "section", "subsection", "recommendation_id",
                 "page", "blocks")

    def __init__(self, chunk_type: str, section: str, subsection: str,
                 recommendation_id: str, page: str):
        self.chunk_type = chunk_type
        self.section = section
        self.subsection = subsection
        self.recommendation_id = recommendation_id
        self.page = page
        self.blocks: list[tuple[str, str]] = []  # (block_text, block_type)

    @property
    def text(self) -> str:
        return "\n\n".join(b for b, _ in self.blocks)

    @property
    def token_count(self) -> int:
        return len(self.text.split())

    @property
    def topic(self) -> str:
        heading_block = next((b for b, t in self.blocks if t == "heading"), None)
        if heading_block:
            m = _NUMBERED_HEADING_RE.match(heading_block.strip().split("\n", 1)[0])
            return m.group(2).strip() if m else heading_block.strip()[:60]
        return self.subsection or self.section or self.chunk_type

    @property
    def recommendation_year(self) -> str:
        """Trailing NICE-style era tag on this unit's own text, e.g. "2021"
        or "2012, amended 2021" -- see _RECOMMENDATION_YEAR_RE. Takes the
        LAST match in the unit (the tag is appended after the text it
        applies to, not before it); if the orphaned-exception merge (see
        _build_clinical_units) has attached a caveat carrying its own,
        separately-amended tag, that later tag wins -- it reflects the most
        recently revised part of this clinical decision unit. Empty string
        for sources that don't use inline era tags (AAP/WHO don't)."""
        matches = _RECOMMENDATION_YEAR_RE.findall(self.text)
        return re.sub(r"\s+", " ", matches[-1]).strip() if matches else ""


def _build_clinical_units(blocks: list[str]) -> list[_ClinicalUnit]:
    units: list[_ClinicalUnit] = []
    section, subsection, recommendation_id, page = "General", "", "", ""
    current: _ClinicalUnit | None = None
    # True while inside an administrative section (acknowledgements,
    # committee rosters, declarations of interest, references, annexes) --
    # everything is dropped, not just the heading line, until the next
    # heading that ISN'T itself administrative. A 98-page guideline PDF can
    # easily carry several pages of GDG/committee member names+affiliations
    # or a full reference list; none of that trips the line-level noise
    # filters in _clean_text() (each line reads as ordinary prose on its
    # own), so without this it silently becomes retrievable, clinical-
    # looking chunks sitting in the same vector space as real
    # recommendations.
    skipping_admin_section = False

    def close_current():
        if current is not None and current.blocks:
            units.append(current)

    for raw_block in blocks:
        page_match = _PAGE_MARKER_RE.search(raw_block)
        if page_match:
            page = page_match.group(1)
            if current is not None and not current.page:
                current.page = page
            raw_block = _PAGE_MARKER_RE.sub("", raw_block).strip()
            if not raw_block:
                continue

        btype = _classify_block(raw_block)

        if btype == "heading":
            close_current()
            current = None
            m = _NUMBERED_HEADING_RE.match(raw_block.strip().split("\n", 1)[0])
            heading_text = m.group(2).strip() if m else raw_block.strip()
            skipping_admin_section = bool(_ADMIN_SECTION_RE.match(heading_text))
            if m:
                depth = m.group(1).count(".") + 1
                if depth == 1:
                    section, subsection = m.group(2).strip(), ""
                else:
                    subsection = m.group(2).strip()
            else:
                section = raw_block.strip()[:80]
            recommendation_id = ""
            continue

        if skipping_admin_section:
            continue

        if btype == "recommendation":
            close_current()
            m = _NUMBERED_HEADING_RE.match(raw_block.strip().split("\n", 1)[0])
            recommendation_id = m.group(1) if m else recommendation_id
            current = _ClinicalUnit("recommendation", section, subsection, recommendation_id, page)
            current.blocks.append((raw_block, btype))
            continue

        if btype in ("table", "bullet_list"):
            # Own chunk (Stage 4, Case C/D) but tagged with the CURRENT
            # unit's hierarchy so retrieval-time metadata still links it to
            # the recommendation it supports, without textually merging it
            # into the prose (a table pasted into running text is exactly
            # the kind of chunk-boundary damage this strategy exists to
            # avoid).
            standalone = _ClinicalUnit(btype, section, subsection, recommendation_id, page)
            standalone.blocks.append((raw_block, btype))
            units.append(standalone)
            continue

        # exception / evidence / monitoring / paragraph: attach to the
        # currently open recommendation unit if one exists; otherwise this
        # is body text with no governing recommendation (e.g. introductory
        # narrative) -- give it its own paragraph-type unit rather than
        # dropping it or force-attaching it to an unrelated prior unit.
        if current is not None:
            current.blocks.append((raw_block, btype))
        else:
            close_current()
            current = _ClinicalUnit("paragraph", section, subsection, recommendation_id, page)
            current.blocks.append((raw_block, btype))

    close_current()

    # Stage 6 integrity check (orphaned-exception merge): if a recommendation
    # unit ends without picking up any exception/monitoring block, but the
    # VERY NEXT unit in document order is a short exception/monitoring-led
    # paragraph unit (i.e. a new heading arrived one block too early and
    # split them), merge the next unit's blocks back in rather than leaving
    # the recommendation without its caveat.
    merged: list[_ClinicalUnit] = []
    i = 0
    while i < len(units):
        unit = units[i]
        if (unit.chunk_type == "recommendation"
                and not any(t in ("exception", "monitoring") for _, t in unit.blocks)
                and i + 1 < len(units)):
            nxt = units[i + 1]
            nxt_leads_with_caveat = nxt.blocks and nxt.blocks[0][1] in ("exception", "monitoring", "paragraph")
            if (nxt.chunk_type == "paragraph" and nxt_leads_with_caveat
                    and nxt.token_count <= 60
                    and any(t in ("exception", "monitoring") for _, t in nxt.blocks)):
                unit.blocks.extend(nxt.blocks)
                merged.append(unit)
                i += 2
                continue
        merged.append(unit)
        i += 1

    return merged


# ── Stage 4/5: adaptive sizing, sentence-level semantic refinement, smart overlap ──

# Stage 7's dynamic size table -- (keep_whole_up_to, target_split_size) in
# approximate whitespace-token counts. Tables/bullet lists are exempt (never
# split, see _build_clinical_units); everything else routes through here.
_SIZE_RANGES: dict[str, tuple[int, int]] = {
    "recommendation": (300, 200),   # keep whole up to 300 tokens; else target ~200/piece
    "evidence": (600, 400),
    "paragraph": (500, 350),
}
_DEFAULT_SIZE_RANGE = (400, 300)


def _adjacent_similarity_split(sentences: list[str], target_size: int) -> list[list[int]]:
    """TF-IDF cosine similarity between ADJACENT sentences only (no
    clustering, no reordering -- unlike the KMeans-based _semantic_chunks
    above). Splits at the lowest-similarity boundaries once a running
    sentence group would exceed target_size tokens. Returns groups of
    sentence indices."""
    if len(sentences) <= 1:
        return [[i for i in range(len(sentences))]]

    from sklearn.feature_extraction.text import TfidfVectorizer
    try:
        vectorizer = TfidfVectorizer(max_features=200)
        matrix = vectorizer.fit_transform(sentences)
        sims = []
        for i in range(len(sentences) - 1):
            a, b = matrix[i], matrix[i + 1]
            denom = (a.multiply(a).sum() ** 0.5) * (b.multiply(b).sum() ** 0.5)
            sims.append(float((a.multiply(b)).sum() / denom) if denom else 0.0)
    except ValueError:
        sims = [1.0] * (len(sentences) - 1)  # degenerate (e.g. all-stopword) input -- don't split

    groups: list[list[int]] = []
    current = [0]
    current_len = len(sentences[0].split())
    for i in range(1, len(sentences)):
        sent_len = len(sentences[i].split())
        boundary_sim = sims[i - 1]
        if current_len + sent_len > target_size and boundary_sim < 0.5:
            groups.append(current)
            current = [i]
            current_len = sent_len
        else:
            current.append(i)
            current_len += sent_len
    groups.append(current)
    return groups


def _adaptive_split_unit(unit: "_ClinicalUnit") -> list[str]:
    """Returns 1+ chunk texts for this unit. Small units (or table/bullet
    units, which are never split) come back as a single chunk; oversized
    units get sentence-level, similarity-boundary splitting with smart
    overlap of exception blocks only (Stage 8) instead of a blind trailing
    token window."""
    if unit.chunk_type in ("table", "bullet_list"):
        return [unit.text]

    keep_whole, target = _SIZE_RANGES.get(unit.chunk_type, _DEFAULT_SIZE_RANGE)
    if unit.token_count <= keep_whole:
        return [unit.text]

    exception_texts = [b for b, t in unit.blocks if t in ("exception", "monitoring")]
    core_sentences = _split_sentences(unit.text)
    if len(core_sentences) <= 1:
        return [unit.text]

    groups = _adjacent_similarity_split(core_sentences, target_size=target)
    pieces = [" ".join(core_sentences[i] for i in g) for g in groups]

    if len(pieces) > 1 and exception_texts:
        # Smart overlap: duplicate just the exception/monitoring block(s)
        # into every piece that doesn't already literally contain that text,
        # instead of a fixed trailing-token window that would duplicate
        # unrelated prose too.
        for exc in exception_texts:
            for i, piece in enumerate(pieces):
                if exc not in piece:
                    pieces[i] = piece.rstrip() + "\n\n" + exc

    return pieces


# ── Orchestration ────────────────────────────────────────────────────────────

def _clinical_structure_chunks(text: str, metadata: dict, settings) -> list[tuple[str, dict]]:
    """CSASC entry point -- dispatched from _chunk_document_text when
    settings.chunking_strategy == "clinical". `text` should ideally be a
    FULL document (all pages concatenated with <<<PAGE:n>>> markers, see
    ingest_directory's clinical-strategy branch) rather than a single page,
    since clinical decision units routinely span page breaks; it still
    works on single-page input (falls back to metadata['page_number'] for
    every resulting chunk), just with the same page-boundary risk the other
    three strategies already have."""
    blocks = _split_into_blocks(text)
    units = _build_clinical_units(blocks)

    results: list[tuple[str, dict]] = []
    for unit in units:
        pieces = _adaptive_split_unit(unit)
        for i, piece in enumerate(pieces):
            piece = _PAGE_MARKER_RE.sub("", piece).strip()
            if not piece:
                continue
            meta = dict(metadata)
            hierarchy_path = " > ".join(p for p in (unit.section, unit.subsection, unit.recommendation_id) if p)
            meta.update({
                "chunking_strategy": "clinical",
                "chunk_type": unit.chunk_type,
                "section": unit.section,
                "subsection": unit.subsection,
                "recommendation_id": unit.recommendation_id,
                # Per-recommendation review-era tag (e.g. NICE's inline
                # "[2021]" / "[2012, amended 2021]" / "[2026]"), distinct
                # from the document-level `version` in document_mapping.py
                # -- a single 98-page guideline can carry recommendations
                # from several different evidence-review eras. Empty string
                # for sources (AAP/WHO) that don't tag recommendations this
                # way; falls back to the whole-document version if neither
                # is available so the field is never silently absent.
                "recommendation_year": unit.recommendation_year or metadata.get("version", ""),
                "topic": unit.topic,
                "hierarchy_path": hierarchy_path or unit.chunk_type,
                "page_number": unit.page or metadata.get("page_number"),
                "unit_piece_index": i,
                "unit_piece_count": len(pieces),
            })
            results.append((piece, meta))

    if not results:
        # Nothing structurally recognisable (e.g. a document that's all
        # running prose with no numbered recommendations) -- fail over to
        # fixed chunking rather than return an empty chunk list for this
        # whole document.
        print("[Ingest][CSASC] No clinical units detected -- falling back to fixed chunking for this document")
        return _chunk_document_text_fixed(text, metadata, settings)

    return results


def _chunk_document_text_fixed(text: str, metadata: dict, settings) -> list[tuple[str, dict]]:
    """Extracted from the original fixed-chunking branch so CSASC's
    no-structure-detected fallback can call it directly without recursing
    through the strategy dispatcher."""
    splitter = SentenceSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    doc = Document(text=text, metadata=metadata)
    nodes = splitter.get_nodes_from_documents([doc])
    return [(node.get_content(), dict(metadata)) for node in nodes]


def _chunk_document_text(
    text: str,
    metadata: dict,
    settings,
) -> list[tuple[str, dict]]:
    """Dispatch to fixed / semantic / proposition / clinical chunking per config."""
    strategy = (settings.chunking_strategy or "fixed").lower()

    if strategy == "semantic":
        return _semantic_chunks(text, metadata)

    if strategy == "proposition":
        return _proposition_chunks(text, metadata, settings)

    if strategy == "clinical":
        return _clinical_structure_chunks(text, metadata, settings)

    return _chunk_document_text_fixed(text, metadata, settings)


# ── PDF loading ────────────────────────────────────────────────────────────────

def load_pdf_documents(directory: Path) -> list[tuple[Document, DocumentMeta]]:
    from llama_index.core.readers import SimpleDirectoryReader

    pdf_files = sorted(directory.glob("**/*.pdf"))
    if not pdf_files:
        pdf_files = sorted(directory.glob("*.pdf"))

    print(f"[Ingest] Found {len(pdf_files)} PDF file(s) in {directory}")

    documents: list[tuple[Document, DocumentMeta]] = []
    for pdf_path in pdf_files:
        try:
            reader = SimpleDirectoryReader(input_files=[str(pdf_path)])
            docs = reader.load_data()
            if not docs:
                print(f"[Ingest] {pdf_path.name}: no pages extracted, skipping")
                continue

            # Content sniffing (document_mapping.py): the first ~2 pages'
            # raw text is enough to catch an organisation name / DOI / NG
            # number even before cleaning, and is far cheaper than sniffing
            # the whole document. Filename still wins when it matches AND
            # agrees -- this only sharpens LOCAL-fallback cases and flags
            # disagreements, see document_mapping.infer_document_meta's
            # docstring for the exact resolution order.
            sniff_text = "\n".join(d.get_content()[:3000] for d in docs[:2])
            meta = infer_document_meta(pdf_path.name, sniff_text=sniff_text)

            if meta.mismatch_warning:
                print(f"[Ingest] WARNING {pdf_path.name}: {meta.mismatch_warning}")
            if meta.confidence == "low":
                print(f"[Ingest] WARNING {pdf_path.name}: no filename or content match — "
                      f"tagged as LOCAL/{meta.region_tag}. Verify this is intentional.")
            print(f"[Ingest] Loading: {pdf_path.name} → {meta.source_name} "
                  f"(confidence={meta.confidence}, matched_by={meta.matched_by})")

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
                    "meta_confidence": meta.confidence,
                    "meta_matched_by": meta.matched_by,
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

        storage_path, storage_url = _upload_pdf_to_storage(
            supabase, guidelines_dir / file_name, meta.source, file_name
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

        chunk_index = 0
        batch: list[dict] = []
        section_map: dict = {}

        if (settings.chunking_strategy or "fixed").lower() == "clinical":
            # CSASC needs the whole document, not one page at a time -- a
            # recommendation and its exception routinely straddle a page
            # break. Concatenate all pages with an explicit page marker so
            # _clinical_structure_chunks can still recover a page_number
            # per resulting chunk (see CSASC's _PAGE_MARKER_RE).
            combined_parts = []
            for doc in docs:
                text = doc.get_content()
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue
                text = _clean_text(text)
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue
                page_label = doc.metadata.get("page_label", "")
                combined_parts.append(f"<<<PAGE:{page_label}>>>\n{text}")
            combined_text = "\n\n".join(combined_parts)

            base_meta = {"file_name": file_name, "source": meta.source, "page_number": None}
            text_chunks = _chunk_document_text(combined_text, base_meta, settings)

            for chunk_text, chunk_meta in text_chunks:
                if _is_noise_chunk(chunk_text):
                    total_skipped += 1
                    continue
                embedding = embed_model.get_text_embedding(chunk_text)
                section = chunk_meta.get("hierarchy_path") or chunk_meta.get("section") \
                    or _extract_section_title(chunk_text, chunk_index, section_map)
                batch.append({
                    "document_id": document_id,
                    "chunk_text": chunk_text,
                    "embedding": embedding,
                    "section": section,
                    "page_number": chunk_meta.get("page_number"),
                    "chunk_index": chunk_index,
                    "source_name": meta.source_name,
                    "version": meta.version,
                    "region_tag": meta.region_tag,
                    "ingestion_date": ingestion_date,
                    "metadata": {
                        **chunk_meta,
                        "node_id": hashlib.md5(chunk_text[:200].encode()).hexdigest()[:12],
                    },
                })
                chunk_index += 1
                if len(batch) >= 50:
                    supabase.table("evidence_chunks").insert(batch).execute()
                    total_inserted += len(batch)
                    print(f"[Ingest]   … inserted {total_inserted} chunks so far")
                    batch = []

        else:
            for doc in docs:
                text = doc.get_content()
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue
                text = _clean_text(text)
                if _is_noise_chunk(text):
                    total_skipped += 1
                    continue

                base_meta = {
                    "file_name": file_name,
                    "source": meta.source,
                    "page_number": doc.metadata.get("page_label"),
                }
                text_chunks = _chunk_document_text(text, base_meta, settings)

                for chunk_text, chunk_meta in text_chunks:
                    if _is_noise_chunk(chunk_text):
                        total_skipped += 1
                        continue

                    embedding = embed_model.get_text_embedding(chunk_text)
                    section = _extract_section_title(chunk_text, chunk_index, section_map)

                    batch.append({
                        "document_id": document_id,
                        "chunk_text": chunk_text,
                        "embedding": embedding,
                        "section": section,
                        "page_number": chunk_meta.get("page_number"),
                        "chunk_index": chunk_index,
                        "source_name": meta.source_name,
                        "version": meta.version,
                        "region_tag": meta.region_tag,
                        "ingestion_date": ingestion_date,
                        "metadata": {
                            **chunk_meta,
                            "node_id": hashlib.md5(chunk_text[:200].encode()).hexdigest()[:12],
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