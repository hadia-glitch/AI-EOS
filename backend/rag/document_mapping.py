"""
Map PDF filenames (+ optional sniffed content) to source metadata for
ingestion.

WHY THIS CHANGED: filename-only matching silently mislabels anything
renamed, re-downloaded, or re-exported without the original filename
surviving (e.g. "download (3).pdf", a scanned copy someone renamed, or a
locally-authored protocol that happens to contain "nice" in a hospital
name). A wrong `source` tag doesn't just mis-file a document — it feeds
straight into `_detect_cross_guideline_conflict()` and the retrieval
guideline-nudge in `retrieve.py`, so a mislabeled NICE doc silently stops
counting as NICE evidence. This version keeps the fast filename path but
adds content sniffing as a second, independent signal, cross-checks the
two against each other, and returns a confidence + provenance so a bad
match is visible in logs instead of silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

Confidence = str  # "high" | "medium" | "low"


@dataclass
class DocumentMeta:
    source: str
    source_name: str
    version: str
    region_tag: str
    # New fields -- all additive, nothing above breaks existing callers that
    # only read .source/.source_name/.version/.region_tag.
    confidence: Confidence = "high"
    matched_by: str = "filename"          # "filename" | "content" | "filename+content" | "fallback"
    mismatch_warning: str = ""            # non-empty if filename and content signals disagreed


# ── Filename signals (fast path, unchanged behaviour when they hit) ────────

_FILENAME_RULES: list[tuple[re.Pattern, DocumentMeta]] = [
    (re.compile(r"nice|ng195", re.I),
     DocumentMeta("NICE", "NICE NG195", "2026", "UK")),
    # More-specific patterns MUST come before the generic "aap" pattern below --
    # _match_filename returns on first hit, so a specific rule listed after a
    # generic one that also matches never fires. (This is also backstopped by
    # the content-sniff year override in infer_document_meta(), which fixes
    # the version even for filenames -- like "aap_management_of_neonates.pdf"
    # -- that don't literally contain "puopolo" or "2018" and so never hit
    # this rule regardless of ordering.)
    (re.compile(r"puopolo.*2018|2018.*puopolo", re.I),
     DocumentMeta("AAP", "AAP: Management of Neonates Born at \u226535 Weeks' Gestation With Suspected EOS (Puopolo et al. 2018)", "2018", "USA")),
    (re.compile(r"aap", re.I),
     DocumentMeta("AAP", "AAP EOS Guidelines", "2018", "USA")),
    # WHO's 2015 PSBI guideline ("...when referral is not feasible") was
    # superseded in 2024 by "WHO recommendations for management of serious
    # bacterial infections in infants aged 0-59 days" (updated guidance
    # reissued Apr 2025). Both are WHO-branded and both match a bare "who"
    # filename/content signal, so the specific 2015-title pattern must be
    # checked first -- otherwise an old PSBI PDF silently gets labelled as
    # the current 2024 guidance. See _WHO_TITLE_SIGNALS below for the
    # content-level version of this same disambiguation.
    (re.compile(r"psbi|referral.?is.?not.?feasible", re.I),
     DocumentMeta("WHO", "WHO Guideline: Managing Possible Serious Bacterial Infection in Young Infants When Referral Is Not Feasible (SUPERSEDED 2024)", "2015", "GLOBAL")),
    (re.compile(r"who", re.I),
     DocumentMeta("WHO", "WHO Recommendations for Management of Serious Bacterial Infections in Infants Aged 0-59 Days", "2024", "GLOBAL")),
    (re.compile(r"kuzniewicz|2023065267", re.I),
     DocumentMeta("EOSCAL", "Kuzniewicz et al. 2024 EOSCAL Recalibration", "2024", "GLOBAL")),
    (re.compile(r"2011|e1155", re.I),
     DocumentMeta("EOSCAL", "Puopolo et al. 2011 Original EOSCAL", "2011", "GLOBAL")),
    (re.compile(r"2019", re.I),
     DocumentMeta("EOSCAL", "Puopolo et al. 2019 EOSCAL Update", "2019", "GLOBAL")),
]


def _match_filename(filename: str) -> DocumentMeta | None:
    lower = filename.lower()
    for pattern, meta in _FILENAME_RULES:
        if pattern.search(lower):
            return DocumentMeta(meta.source, meta.source_name, meta.version, meta.region_tag,
                                 confidence="high", matched_by="filename")
    return None


# ── Content signals (sniffed from the first ~2 pages of extracted text) ────
# Each source gets a set of strong identity regexes (organisation name,
# guideline number, DOI prefix, etc.), used both to confirm a filename match
# and to recover a source when the filename gives nothing usable.

_CONTENT_SIGNALS: dict[str, list[re.Pattern]] = {
    "NICE": [
        re.compile(r"National Institute for Health and Care Excellence", re.I),
        re.compile(r"\bNICE\b"),
        re.compile(r"\bNG\s?195\b", re.I),
        re.compile(r"nice\.org\.uk", re.I),
    ],
    "AAP": [
        re.compile(r"American Academy of Pediatrics", re.I),
        re.compile(r"\bAAP\b"),
        re.compile(r"publications\.aap\.org", re.I),
        re.compile(r"\bPediatrics\b.{0,40}\b(20\d{2})\b"),
    ],
    "WHO": [
        re.compile(r"World Health Organi[sz]ation", re.I),
        re.compile(r"\bWHO\b"),
        re.compile(r"who\.int", re.I),
    ],
    "EOSCAL": [
        re.compile(r"\bPuopolo\b", re.I),
        re.compile(r"\bKuzniewicz\b", re.I),
        re.compile(r"\bEOSCAL\b", re.I),
        re.compile(r"early[- ]onset sepsis calculator", re.I),
    ],
}

# DOI prefixes are a signal for the journal-published sources (NICE guidance
# has no DOI in this form, so absence here is not informative for NICE).
# Weight is deliberately kept at +1 (equal to a single keyword hit), NOT
# stronger -- 10.1542/peds is the *Pediatrics* journal DOI prefix and is
# shared by both AAP clinical reports and the EOSCAL papers (Puopolo 2011/
# 2019 were also published in Pediatrics). Weighting it higher than a plain
# keyword would let this DOI hint always win for AAP even on an EOSCAL-only
# PDF that has zero AAP-specific text but does mention "Puopolo" -- keeping
# it at +1 lets an actual EOSCAL keyword hit stay competitive instead of
# being auto-overridden by a DOI prefix the two source families share.
_DOI_SOURCE_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"10\.1542/peds"), "AAP"),        # Pediatrics (AAP journal) / EOSCAL papers both live here
    (re.compile(r"10\.1136/bmjopen"), "EOSCAL"),  # Qatar EOSCAL validation cohort, per your source URL map
]

# Title-level disambiguation between the two, differently-versioned WHO
# guidelines -- both match the generic "World Health Organization"/"WHO"
# content signals above, so that alone can't tell them apart. Checked
# whenever a WHO filename/content match is confirmed, to correct
# source_name (version itself is separately corrected by the content-year
# override in infer_document_meta()).
_WHO_TITLE_SIGNALS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"referral is not feasible", re.I),
     "WHO Guideline: Managing Possible Serious Bacterial Infection in Young Infants When Referral Is Not Feasible (SUPERSEDED 2024)"),
    (re.compile(r"serious bacterial infections? in infants aged 0.{0,3}59", re.I),
     "WHO Recommendations for Management of Serious Bacterial Infections in Infants Aged 0-59 Days"),
]


def _who_source_name(sniff_text: str, default: str) -> str:
    for pattern, name in _WHO_TITLE_SIGNALS:
        if pattern.search(sniff_text):
            return name
    return default

_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")

# UK vs US English spelling gives a weak region tiebreaker ONLY for
# documents that gave no organisation signal at all (i.e. genuine LOCAL
# uploads) -- never used to override an NICE/AAP/WHO organisation match.
_UK_SPELLING = re.compile(r"\b(paediatric|colour|organisation|foetal|anaesth)", re.I)
_US_SPELLING = re.compile(r"\b(pediatric|color|organization|fetal|anesth)", re.I)


def _match_content(sniff_text: str) -> tuple[str | None, list[str]]:
    """Returns (best_source_or_None, [all sources that matched >=1 signal])."""
    if not sniff_text:
        return None, []
    hits: dict[str, int] = {}
    for source, patterns in _CONTENT_SIGNALS.items():
        count = sum(1 for p in patterns if p.search(sniff_text))
        if count:
            hits[source] = count
    for pattern, source in _DOI_SOURCE_HINTS:
        if pattern.search(sniff_text):
            hits[source] = hits.get(source, 0) + 1  # see _DOI_SOURCE_HINTS comment: kept equal to a keyword hit, not stronger

    if not hits:
        return None, []
    matched_sources = sorted(hits.keys())
    best = max(hits.items(), key=lambda kv: kv[1])[0]
    return best, matched_sources


def _extract_year(sniff_text: str, fallback: str) -> str:
    years = _YEAR_PATTERN.findall(sniff_text)
    return years[-1] if years else fallback  # last year mentioned is usually a publication/amendment date


def _region_from_source(source: str, sniff_text: str) -> str:
    if source == "NICE":
        return "UK"
    if source == "AAP":
        return "USA"
    if source in ("WHO", "EOSCAL"):
        return "GLOBAL"
    if sniff_text:
        if _UK_SPELLING.search(sniff_text) and not _US_SPELLING.search(sniff_text):
            return "UK"
        if _US_SPELLING.search(sniff_text) and not _UK_SPELLING.search(sniff_text):
            return "USA"
    return "GENERAL"


_SOURCE_NAME_TEMPLATE = {
    "NICE": "NICE NG195", "AAP": "AAP EOS Guidelines",
    "WHO": "WHO Recommendations for Management of Serious Bacterial Infections in Infants Aged 0-59 Days",
    "EOSCAL": "EOSCAL (source year TBD)",
}


def infer_document_meta(filename: str, sniff_text: str = "") -> DocumentMeta:
    """
    Infer guideline metadata from a filename and, when available, the first
    ~1-2 pages of extracted PDF text.

    `sniff_text` is optional so every existing call site that only has a
    filename keeps working unmodified -- passing it in is strictly additive
    and only sharpens/corrects the result. See ingest.py's
    load_pdf_documents() for how it's obtained (first page's cleaned text).

    Resolution order:
      1. Filename match found, no sniff_text given -> return it as-is
         (confidence="high", matched_by="filename") -- unchanged behaviour.
      2. Filename match found AND sniff_text given -> cross-check. Content
         confirms filename -> confidence="high", matched_by="filename+content".
         On confirmation, `version` is additionally refined from the actual
         year(s) mentioned in the document's own text (see below) rather
         than left at the filename rule's template guess, and for WHO
         specifically `source_name` is refined via _WHO_TITLE_SIGNALS since
         two differently-dated WHO guidelines both match a bare "who"
         filename/content signal.
         Content contradicts filename (a DIFFERENT source's signals appear
         and the filename's own source has zero content signals) -> still
         return the filename's match (filenames are usually the more
         deliberate signal for guideline PDFs you sourced yourself), but
         confidence="medium" and mismatch_warning is populated -- this is
         the case you actually want to see in ingestion logs.
      3. No filename match, sniff_text given and it matches a known source
         -> return that, confidence="medium", matched_by="content".
      4. Neither gives a match -> LOCAL fallback, confidence="low",
         matched_by="fallback", using the filename stem as source_name and
         a spelling-based region_tag guess instead of a hardcoded "GENERAL".

    Why refine `version` from content on every confirmed match, not just
    add a more specific filename rule: filename rules pin one template
    version per *source family* (e.g. "AAP" -> "2018"), which is wrong for
    any individual PDF whose real publication/update year differs and
    doesn't happen to appear in its filename (e.g.
    "aap_management_of_neonates.pdf" contains neither "puopolo" nor "2018",
    so no amount of filename-rule reordering fixes its version -- only
    reading the year out of the document itself does). This is a heuristic
    (last 4-digit year mentioned in the first ~1-2 sniffed pages), so it can
    occasionally be wrong for documents with an unrelated year earlier in
    that span -- but it is strictly more accurate than a hardcoded per-
    source-family guess.
    """
    filename_meta = _match_filename(filename)
    content_best, content_all = _match_content(sniff_text) if sniff_text else (None, [])

    if filename_meta is not None:
        if not sniff_text:
            return filename_meta

        if content_best == filename_meta.source or filename_meta.source in content_all:
            filename_meta.confidence = "high"
            filename_meta.matched_by = "filename+content"

            sniffed_year = _extract_year(sniff_text, fallback="")
            if sniffed_year and sniffed_year != filename_meta.version:
                filename_meta.version = sniffed_year
                filename_meta.matched_by = "filename+content(year)"

            if filename_meta.source == "WHO":
                filename_meta.source_name = _who_source_name(sniff_text, filename_meta.source_name)

            return filename_meta

        if content_all and filename_meta.source not in content_all:
            filename_meta.confidence = "medium"
            filename_meta.matched_by = "filename"
            filename_meta.mismatch_warning = (
                f"Filename matched '{filename_meta.source}' but content sniffing found signals for "
                f"{content_all} and none for '{filename_meta.source}' -- verify this file is correctly named."
            )
            return filename_meta

        return filename_meta  # no content signals at all either way -- filename match stands, unweakened

    if content_best is not None:
        year = _extract_year(sniff_text, fallback="unknown")
        source_name = _SOURCE_NAME_TEMPLATE.get(content_best, content_best)
        if content_best == "WHO":
            source_name = _who_source_name(sniff_text, source_name)
        return DocumentMeta(
            source=content_best,
            source_name=source_name,
            version=year,
            region_tag=_region_from_source(content_best, sniff_text),
            confidence="medium",
            matched_by="content",
        )

    stem = Path(filename).stem
    return DocumentMeta(
        source="LOCAL",
        source_name=stem,
        version=_extract_year(sniff_text, fallback="1.0") if sniff_text else "1.0",
        region_tag=_region_from_source("LOCAL", sniff_text),
        confidence="low",
        matched_by="fallback",
    )