"""
chunk.py — Clinical-guideline-aware chunker for NeoGuard EOS RAG pipeline.

Architecture: chunks are built and validated WITHOUT the source prefix, then
the prefix is injected as the very last step. This ensures:
  - Orphan detection works on actual content (not "Source: WHO\n...")
  - Size limits apply to content only (prefix adds ~100–180 chars on top;
    CONTENT_MAX_CHARS is set conservatively so total stays under 1200)
  - Validation sees only the clinical content

Fixes applied (numbered per the issue list):
  #1  Orphan patterns (Moderate, Low, High, etc.) merged into previous chunk
      by semantic dependency, NOT by size.
  #2  Oversized chunks split at CLINICAL_BOUNDARIES (Evidence, Remarks,
      Implementation, etc.) BEFORE falling back to sentence splitting.
  #3  Tables serialized as key:value prose; never cell-by-cell.
  #4  Context-free statements (No studies, Moderate) get parent context prepended.
  #5  Recommendation → Evidence → Remarks hierarchy maintained as one unit.
  #6  Target 350–900 content chars; merge <200, split >950 at sentence boundaries.
  #7  Metadata injected into chunk text (self-contained embeddings).
  #8  Recommendation objects group rec + evidence + remarks before splitting.
  #9  Splits respect likely clinician queries (section-boundary-aware).
  #10 Structural-only chunks ("(six recommendations, plus remarks)") discarded.
  #11 Standalone meaning validated; orphan-starting chunks merged upward.
  #12 Table row relationships preserved via key:value serialization.
  #13 10–15% sentence-level overlap between adjacent text chunks.
  #14 Large Remarks blocks split at semantic sub-labels.

Additional:
  A   Recommendation packaging: rec + evidence + remarks stored together.
  B   Table serialization: markdown -> readable key:value prose.
  C   Retrieval-oriented context prefix prepended last (after all validation).
  D   Semantic chunk validation: length, completeness, orphan check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from rag.clean import Block

# ---------------------------------------------------------------------------
# Size targets — these apply to CONTENT only (prefix added separately at end)
# Conservative so that content + ~180-char prefix stays well under 1200 total
# ---------------------------------------------------------------------------
CONTENT_MIN_CHARS = 200       # merge content below this (issue #6, #11)
CONTENT_TARGET_CHARS = 700    # soft target
CONTENT_MAX_CHARS = 950       # hard split threshold (issue #6)
OVERLAP_RATIO = 0.12          # ~12% sentence-level overlap (issue #13)

# ---------------------------------------------------------------------------
# Clinical boundary markers — ALWAYS start a new chunk (issues #2, #9)
# ---------------------------------------------------------------------------
CLINICAL_BOUNDARIES = re.compile(
    r"^(Evidence|Recommendation|Remarks?|Implementation|"
    r"Resource considerations?|Values? and preferences?|"
    r"Acceptability|Feasibility|Monitoring|References?|"
    r"Rationale|Clinical rationale|Special populations?|"
    r"Exceptions?|Summary of evidence|Background|"
    r"Justification|Certainty of evidence|"
    r"Strength of recommendation)\s*[:\-]?\s*$",
    re.I | re.M,
)

# Sub-labels within Remarks blocks (issue #14)
REMARKS_SUB_LABELS = re.compile(
    r"(?:^|\n)(Clinical rationale|Implementation|Exceptions?|"
    r"Monitoring|Special populations?|AMR|Antimicrobial|"
    r"Caution|Warning|Note|Background|Context)\s*[:\-]",
    re.I,
)

# ---------------------------------------------------------------------------
# Orphan / structural patterns (issues #1, #4, #10, #11)
# ---------------------------------------------------------------------------
ORPHAN_PATTERNS = re.compile(
    r"^(Moderate|Low|High|Very low|Probably acceptable|"
    r"No studies?\.?|No trials?\.?|Low costs?|Feasible|"
    r"Fair|Conditional|Strong|Yes|No|N\/A|Not applicable|"
    r"Insufficient evidence|Insufficient data|"
    r"See above|See below|As above|As below|"
    r"Unclear|Unknown)\s*\.?\s*$",
    re.I,
)

STRUCTURAL_DISCARD_RE = re.compile(
    r"^\([^)]{3,80}(recommendations?|remarks?|evidence)[^)]{0,40}\)\s*$",
    re.I,
)

_RECOMMENDATION_MODAL_RE = re.compile(
    r"\b(should|must|shall|is recommended|are recommended|offer|"
    r"do not offer|do not use|consider|we recommend|we suggest|"
    r"strongly recommend)\b",
    re.I,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\u2018\u201c])")
_NUMBERED_HEADING_RE = re.compile(r"^(\d+\.\d+(?:\.\d+){0,2})\.?\s+(.*)$")


# ---------------------------------------------------------------------------
# Table serialization (issues #3, #12, B)
# ---------------------------------------------------------------------------

def serialize_table(markdown_table: str) -> str:
    """
    Convert a markdown table into key:value prose for better embeddings.

    Input:
        | Criterion    | Rating   |
        |---|---|
        | Certainty    | Moderate |
        | Resource use | Low cost |

    Output:
        Evidence Profile
        • Certainty: Moderate
        • Resource use: Low cost
    """
    lines = [l.strip() for l in markdown_table.strip().splitlines() if l.strip()]
    lines = [l for l in lines if not re.match(r"^\|[\s\-|]+\|$", l)]
    if not lines:
        return markdown_table

    def parse_row(line: str) -> list[str]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        return [c for c in cells if c]

    rows = [parse_row(l) for l in lines]
    if not rows:
        return markdown_table

    header = rows[0]
    body = rows[1:]

    if not body:
        return " | ".join(header)

    # Detect two-column tables (Criterion | Rating) → bullet per row
    if len(header) == 2:
        parts = ["Evidence Profile"]
        for row in body:
            if len(row) >= 2 and (row[0] or row[1]):
                parts.append(f"• {row[0]}: {row[1]}" if row[0] else f"• {row[1]}")
        return "\n".join(parts)

    # Multi-column → key:value pairs per row
    parts = ["Evidence Profile"]
    for row in body:
        if not any(c for c in row):
            continue
        pairs: list[str] = []
        for i, cell in enumerate(row):
            if not cell:
                continue
            label = header[i] if i < len(header) else f"Col{i + 1}"
            pairs.append(f"{label}: {cell}")
        if pairs:
            parts.append("• " + " | ".join(pairs))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentence utilities
# ---------------------------------------------------------------------------



# Bullet/enumeration markers that start new logical "sentences" in clinical guidelines.
# Matches both newline-prefixed AND inline ■/■ style markers (common in WHO tables
# where multiple ■ items appear on a single line inside a table cell).
_BULLET_SPLIT_RE = re.compile(r"\n(?=[■•●▪]|\d{1,2}[.)]\s)")
_INLINE_BULLET_RE = re.compile(r"(?<=[^\n])\s+(?=[■•●▪])")


def _normalize_bullets(text: str) -> str:
    """
    Normalize inline bullet markers (■ appearing mid-sentence in long runs) to
    newline-prefixed so _BULLET_SPLIT_RE can handle them.
    Only applied when the text is long enough that splitting would help.
    """
    if len(text) <= CONTENT_MAX_CHARS:
        return text
    return _INLINE_BULLET_RE.sub("\n", text)


def split_sentences(text: str) -> list[str]:
    """
    Sentence splitter for clinical guideline text.
    Handles both prose (sentence-final punctuation) and bullet-list text
    (■ / • / 1. style enumeration) — both are extremely common in WHO/NICE/AAP
    guidelines and bullet items rarely end in periods, so a regex that only looks
    for '.?!' misses them entirely.

    spaCy is used when available; otherwise the regex path handles bullets too.
    """
    try:
        import spacy
        nlp = getattr(split_sentences, "_nlp", None)
        if nlp is None:
            nlp = spacy.load("en_core_web_sm")
            split_sentences._nlp = nlp
        doc = nlp(text)
        sents = [s.text.strip() for s in doc.sents if s.text.strip()]
        if sents:
            return sents
    except Exception:
        pass

    # Normalize inline bullet markers to newline-prefixed before splitting
    text = _normalize_bullets(text)

    # Step 1: split at bullet markers (■, •, numbered lists)
    bullet_parts = _BULLET_SPLIT_RE.split(text.strip())

    # Step 2: within each part, split further at prose sentence boundaries
    result: list[str] = []
    for part in bullet_parts:
        part = part.strip()
        if not part:
            continue
        sub = _SENTENCE_SPLIT_RE.split(part)
        result.extend(s.strip() for s in sub if s.strip())
    return result


def _lacks_context(text: str) -> bool:
    """True when the text is an orphan value that requires parent context."""
    return bool(ORPHAN_PATTERNS.match(text.strip()))


# ---------------------------------------------------------------------------
# Clinical boundary splitting (issues #2, #9)
# ---------------------------------------------------------------------------

def split_at_clinical_boundaries(text: str) -> list[tuple[str, str]]:
    """
    Split text at CLINICAL_BOUNDARIES headings.
    Returns (label, content) tuples; label is "" for pre-boundary content.
    """
    segments: list[tuple[str, str]] = []
    current_label = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        m = CLINICAL_BOUNDARIES.match(line.strip())
        if m:
            content = "\n".join(current_lines).strip()
            if content:
                segments.append((current_label, content))
            current_label = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    tail = "\n".join(current_lines).strip()
    if tail:
        segments.append((current_label, tail))

    return segments if segments else [("", text)]


def split_remarks_block(text: str) -> list[str]:
    """Issue #14: split a Remarks block at semantic sub-labels."""
    parts = REMARKS_SUB_LABELS.split(text)
    if len(parts) <= 1:
        return [text]
    result: list[str] = []
    # Parts alternate: [before, label1, after1, label2, after2, ...]
    result.append(parts[0].strip())
    i = 1
    while i < len(parts) - 1:
        label = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        combined = f"{label}: {body}" if body else label
        result.append(combined)
        i += 2
    return [r for r in result if r.strip()]


# ---------------------------------------------------------------------------
# Size-bounded splitting with overlap (issues #6, #13)
# ---------------------------------------------------------------------------

def _hard_cut(text: str) -> list[str]:
    """Split at word boundaries to avoid cutting mid-word when hard-capping."""
    if len(text) <= CONTENT_MAX_CHARS:
        return [text]
    # Try to split at last space before the limit
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = start + CONTENT_MAX_CHARS
        if end >= len(text):
            pieces.append(text[start:])
            break
        # Walk back to last space
        cut = text.rfind(" ", start, end)
        if cut <= start:
            cut = end   # no space found — hard cut at char boundary
        pieces.append(text[start:cut])
        start = cut + 1
    return [p.strip() for p in pieces if p.strip()]


def _split_long_text(text: str) -> list[str]:
    """
    Sentence-boundary (and bullet-boundary) split with CONTENT_MAX_CHARS and
    sentence-level overlap.  Every piece is guaranteed <= CONTENT_MAX_CHARS.

    Key guarantee: even a single "sentence" (e.g. a very long bullet row)
    that exceeds the limit is hard-cut at a word boundary, so the output
    never contains a piece > CONTENT_MAX_CHARS regardless of input structure.
    """
    if len(text) <= CONTENT_MAX_CHARS:
        return [text]

    sentences = split_sentences(text)
    if not sentences:
        return _hard_cut(text)

    overlap_chars = int(CONTENT_MAX_CHARS * OVERLAP_RATIO)
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        slen = len(sent) + 1

        # If this single sentence is already over the limit, flush first,
        # then hard-cut the long sentence independently.
        if slen > CONTENT_MAX_CHARS:
            if current:
                pieces.append(" ".join(current))
                current = []
                current_len = 0
            pieces.extend(_hard_cut(sent))
            continue

        if current and current_len + slen > CONTENT_MAX_CHARS:
            pieces.append(" ".join(current))
            # Carry trailing sentences worth ~overlap_chars into next piece
            overlap: list[str] = []
            olen = 0
            for s in reversed(current):
                if olen >= overlap_chars:
                    break
                overlap.insert(0, s)
                olen += len(s) + 1
            current = overlap
            current_len = olen

        current.append(sent)
        current_len += slen

    if current:
        pieces.append(" ".join(current))

    # Final safety pass — any piece over the limit gets hard-cut
    result: list[str] = []
    for p in pieces:
        result.extend(_hard_cut(p))
    return result


# ---------------------------------------------------------------------------
# Recommendation object (issues #5, #8, A)
# ---------------------------------------------------------------------------

@dataclass
class RecommendationObject:
    section: str
    subsection: str
    page: int
    rec_id: str
    statement: str = ""
    evidence: str = ""
    remarks: str = ""
    implementation: str = ""
    resources: str = ""
    feasibility: str = ""
    acceptability: str = ""
    other_fields: dict[str, str] = field(default_factory=dict)

    def to_prose(self) -> str:
        parts: list[str] = []
        if self.statement:
            parts.append(f"Recommendation: {self.statement}")
        if self.evidence:
            parts.append(f"Evidence: {self.evidence}")
        if self.resources:
            parts.append(f"Resource considerations: {self.resources}")
        if self.acceptability:
            parts.append(f"Acceptability: {self.acceptability}")
        if self.feasibility:
            parts.append(f"Feasibility: {self.feasibility}")
        if self.implementation:
            parts.append(f"Implementation: {self.implementation}")
        if self.remarks:
            parts.append(f"Remarks: {self.remarks}")
        for k, v in self.other_fields.items():
            if v:
                parts.append(f"{k}: {v}")
        return "\n\n".join(parts)

    def content_len(self) -> int:
        return len(self.to_prose())


# ---------------------------------------------------------------------------
# Unit dataclass
# ---------------------------------------------------------------------------

@dataclass
class Unit:
    section: str
    subsection: str
    recommendation_id: str
    page: int
    kind: str  # "text" | "table" | "recommendation"
    text: str = ""


# ---------------------------------------------------------------------------
# Step 1: Build units from cleaned blocks
# ---------------------------------------------------------------------------

def build_units(blocks: list[Block]) -> list[Unit]:
    units: list[Unit] = []
    section, subsection = "General", ""
    rec_counter = 0
    buf_text: list[str] = []
    buf_page: Optional[int] = None
    buf_is_rec = False

    def flush() -> None:
        nonlocal buf_text, buf_page, buf_is_rec
        if buf_text:
            joined = "\n\n".join(buf_text)
            kind = "recommendation" if buf_is_rec else "text"
            units.append(Unit(
                section=section,
                subsection=subsection,
                recommendation_id=(f"rec{rec_counter}" if rec_counter and buf_is_rec else ""),
                page=buf_page or 0,
                kind=kind,
                text=joined,
            ))
        buf_text = []
        buf_page = None
        buf_is_rec = False

    for b in blocks:
        if b.kind == "heading":
            flush()
            m = _NUMBERED_HEADING_RE.match(b.text.strip())
            heading_text = m.group(2).strip() if m else b.text.strip()
            if m:
                subsection = heading_text
            else:
                section, subsection = heading_text, ""
            rec_counter = 0
            continue

        if b.kind == "table":
            flush()
            units.append(Unit(
                section=section, subsection=subsection,
                recommendation_id="", page=b.page,
                kind="table", text=b.text,
            ))
            continue

        is_rec = bool(_RECOMMENDATION_MODAL_RE.search(b.text)) and len(b.text) < 800
        if is_rec:
            flush()
            rec_counter += 1
            buf_is_rec = True

        buf_page = buf_page or b.page
        buf_text.append(b.text)

    flush()
    return units


# ---------------------------------------------------------------------------
# Step 2: Parse a unit into a RecommendationObject
# ---------------------------------------------------------------------------

def _build_rec_object(unit: Unit) -> RecommendationObject:
    obj = RecommendationObject(
        section=unit.section,
        subsection=unit.subsection,
        page=unit.page,
        rec_id=unit.recommendation_id,
    )
    segments = split_at_clinical_boundaries(unit.text)

    for label_raw, content in segments:
        label = label_raw.lower().rstrip(":- ").strip()
        if not label or _RECOMMENDATION_MODAL_RE.search(content[:200]):
            if not obj.statement:
                obj.statement = content
            else:
                obj.other_fields[label_raw or "Body"] = content
        elif "evidence" in label:
            obj.evidence = (obj.evidence + "\n\n" + content).strip() if obj.evidence else content
        elif "remark" in label:
            obj.remarks = (obj.remarks + "\n\n" + content).strip() if obj.remarks else content
        elif "implementation" in label:
            obj.implementation = (obj.implementation + "\n\n" + content).strip() if obj.implementation else content
        elif "resource" in label:
            obj.resources = (obj.resources + "\n\n" + content).strip() if obj.resources else content
        elif "feasib" in label:
            obj.feasibility = (obj.feasibility + "\n\n" + content).strip() if obj.feasibility else content
        elif "accept" in label or "values" in label or "preference" in label:
            obj.acceptability = (obj.acceptability + "\n\n" + content).strip() if obj.acceptability else content
        else:
            if label_raw:
                obj.other_fields[label_raw] = content
            elif not obj.statement:
                obj.statement = content

    return obj


# ---------------------------------------------------------------------------
# Step 3: Validation on RAW CONTENT (no prefix) (issues #4, #10, #11, D)
# ---------------------------------------------------------------------------

def _is_meaningful(content: str) -> bool:
    """
    Return True if the raw content (no prefix) is self-contained and worth indexing.
    Rejects structural annotations, pure orphan values, and empty/tiny chunks.
    """
    stripped = content.strip()
    if not stripped:
        return False
    if STRUCTURAL_DISCARD_RE.match(stripped):
        return False
    if _lacks_context(stripped):
        return False
    if len(stripped) < CONTENT_MIN_CHARS:
        # Short is OK only for standalone recommendation statements
        return bool(_RECOMMENDATION_MODAL_RE.search(stripped))
    return True


# ---------------------------------------------------------------------------
# Step 4: Orphan merging on raw content (issues #1, #11)
# ---------------------------------------------------------------------------

def _merge_orphans(raw: list[dict]) -> list[dict]:
    """
    Merge chunks whose content is an orphan value or is under CONTENT_MIN_CHARS
    into the preceding chunk from the same section, by semantic dependency.

    Works on raw_content (the pre-prefix text), so orphan patterns match correctly.
    """
    merged: list[dict] = []
    for rec in raw:
        content = rec["raw_content"]
        stripped = content.strip()
        is_orphan = _lacks_context(stripped) or STRUCTURAL_DISCARD_RE.match(stripped)
        is_tiny = len(stripped) < CONTENT_MIN_CHARS and rec["chunk_type"] == "paragraph"
        is_rec = bool(rec.get("recommendation_id"))

        can_merge = (is_orphan or (is_tiny and not is_rec)) and merged
        if can_merge and merged[-1]["section"] == rec["section"]:
            prev = merged[-1]
            if is_orphan and not is_tiny:
                # Prepend context from subsection/section
                context = rec.get("subsection") or rec.get("section") or ""
                if context:
                    prev["raw_content"] = prev["raw_content"].rstrip() + \
                        f"\n\n{context}: {stripped}"
                else:
                    prev["raw_content"] = prev["raw_content"].rstrip() + "\n\n" + stripped
            else:
                prev["raw_content"] = prev["raw_content"].rstrip() + "\n\n" + stripped
            continue

        merged.append(rec)
    return merged


# ---------------------------------------------------------------------------
# Step 5: Context prefix (issues C, #7)
# ---------------------------------------------------------------------------

def _make_prefix(source: str, section: str, subsection: str, rec_id: str) -> str:
    """Retrieval-oriented context prefix prepended to each chunk's final text."""
    parts = [f"Source: {source}"]
    if section and section not in ("General",):
        parts.append(f"Section: {section}")
    if subsection:
        parts.append(f"Topic: {subsection}")
    if rec_id:
        parts.append(f"Recommendation: {rec_id}")
    return "\n".join(parts) + "\n\n"


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _slugify(text: str, maxlen: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:maxlen] or "sec"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def chunk_document(units: list[Unit], source: str) -> list[dict]:
    """
    Convert units into validated, self-contained chunks.

    Pipeline:
      1. Tables → serialize to key:value prose
      2. Recommendation units → RecommendationObject → prose
      3. Plain text → split at CLINICAL_BOUNDARIES, then Remarks sub-labels
      4. Size-bounded sentence splitting with overlap
      5. Collect raw_content records (NO prefix yet)
      6. Merge orphan/tiny chunks by semantic dependency
      7. Validate each chunk on raw_content
      8. Prepend context prefix → final chunk_text
      9. Assign chunk_id
    """
    raw: list[dict] = []

    def push(content: str, unit: Unit, chunk_type: str, rec_id: str = "") -> None:
        """Add a raw record (content only, no prefix yet)."""
        content = content.strip()
        if not content:
            return
        raw.append({
            "source": source,
            "page": unit.page,
            "section": unit.section,
            "subsection": unit.subsection,
            "recommendation_id": rec_id,
            "chunk_type": chunk_type,
            "raw_content": content,
        })

    for unit in units:

        # ── TABLES ──────────────────────────────────────────────────────
        if unit.kind == "table":
            prose = serialize_table(unit.text)
            for piece in _split_long_text(prose):
                push(piece, unit, "table")
            continue

        # ── RECOMMENDATION OBJECTS ───────────────────────────────────
        is_rec_unit = unit.kind == "recommendation" or (
            unit.recommendation_id and _RECOMMENDATION_MODAL_RE.search(unit.text[:300])
        )
        if is_rec_unit:
            rec_obj = _build_rec_object(unit)
            prose = rec_obj.to_prose()

            if rec_obj.content_len() <= CONTENT_MAX_CHARS:
                push(prose, unit, "recommendation", unit.recommendation_id)
            else:
                # Head: statement + evidence together
                head_parts: list[str] = []
                if rec_obj.statement:
                    head_parts.append(f"Recommendation: {rec_obj.statement}")
                if rec_obj.evidence:
                    head_parts.append(f"Evidence: {rec_obj.evidence}")
                head = "\n\n".join(head_parts)
                if head:
                    for piece in _split_long_text(head):
                        push(piece, unit, "recommendation", unit.recommendation_id)

                # Remarks: split at sub-labels first (issue #14)
                if rec_obj.remarks:
                    for sub in split_remarks_block(rec_obj.remarks):
                        for piece in _split_long_text(f"Remarks: {sub}"):
                            push(piece, unit, "paragraph", unit.recommendation_id)

                # Remaining fields
                for label, content in [
                    ("Implementation", rec_obj.implementation),
                    ("Resource considerations", rec_obj.resources),
                    ("Acceptability", rec_obj.acceptability),
                    ("Feasibility", rec_obj.feasibility),
                ]:
                    if content:
                        for piece in _split_long_text(f"{label}: {content}"):
                            push(piece, unit, "paragraph", unit.recommendation_id)

                for k, v in rec_obj.other_fields.items():
                    if v:
                        for piece in _split_long_text(f"{k}: {v}"):
                            push(piece, unit, "paragraph", unit.recommendation_id)
            continue

        # ── PLAIN TEXT UNITS ─────────────────────────────────────────
        segments = split_at_clinical_boundaries(unit.text)
        for label, content in segments:
            if not content.strip():
                continue
            label_prefix = f"{label.strip()}\n\n" if label.strip() else ""
            full_content = label_prefix + content

            if label and "remark" in label.lower() and len(content) > CONTENT_MAX_CHARS:
                for sub in split_remarks_block(content):
                    for piece in _split_long_text(label_prefix + sub):
                        push(piece, unit, "paragraph", unit.recommendation_id)
            else:
                for piece in _split_long_text(full_content):
                    push(piece, unit, "paragraph", unit.recommendation_id)

    # ── POST-PROCESSING ─────────────────────────────────────────────

    # 1. Merge orphan / tiny chunks into their predecessors
    merged = _merge_orphans(raw)

    # 2. Validate, add prefix, assign IDs
    section_running: dict[str, int] = {}
    out: list[dict] = []

    for rec in merged:
        content = rec["raw_content"].strip()

        # Validate on raw content (no prefix)
        if not _is_meaningful(content):
            continue

        prefix = _make_prefix(
            source, rec["section"], rec["subsection"], rec["recommendation_id"]
        )
        chunk_text = prefix + content

        sec_slug = _slugify(rec["section"])
        base = f"{source}_pg{rec['page'] + 1}_{sec_slug}"
        if rec["recommendation_id"]:
            base += f"_{rec['recommendation_id']}"
        section_running[base] = section_running.get(base, 0) + 1
        chunk_id = f"{base}_chunk{section_running[base]}"

        out.append({
            "chunk_id": chunk_id,
            "source": source,
            "page": rec["page"],
            "section": rec["section"],
            "subsection": rec["subsection"],
            "recommendation_id": rec["recommendation_id"],
            "chunk_type": rec["chunk_type"],
            "chunk_text": chunk_text,
            "chars": len(chunk_text),
        })

    return out