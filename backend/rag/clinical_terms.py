"""
Clinical term normalisation — SNOMED-lite synonym expansion for neonatal EOS.

Full SNOMED CT integration (UMLS license, concept graph, ~350k concepts) is
disproportionate for a single-condition (EOS) system with a handful of
guideline documents — it adds a license dependency and an ontology server
for marginal recall gain over a curated domain dictionary. Instead this
module hand-curates the clinical synonym clusters that actually appear
in NICE/AAP/WHO/EOSCAL literature and clinician phrasing, and expands a
free-text query with same-cluster terms before it hits BM25/semantic search.

This directly targets the "newborn blood infection" vs "early-onset
neonatal sepsis" mismatch: keyword search alone would miss it; expansion
makes sure BM25 sees "sepsis", "EOS", "bacteremia" alongside the user's
own words. Semantic search (embeddings) partially covers this already,
which is exactly why hybrid search — not either alone — is used.

If/when the project ingests broader paediatric literature beyond EOS,
swap this for a real SNOMED CT / UMLS lookup (e.g. via the NLM UTS API or
a self-hosted SNOMED terminology server) — the expand_query() interface
below is deliberately the only integration point retrieve.py depends on,
so that swap is a one-file change.
"""

from __future__ import annotations

import re

# ── Curated synonym clusters ────────────────────────────────────────────────
# Each cluster is a set of interchangeable clinical terms/abbreviations.
# Expansion is symmetric: any term in a cluster pulls in the rest of it.

_SYNONYM_CLUSTERS: list[set[str]] = [
    {"eos", "early-onset sepsis", "early onset sepsis", "early-onset neonatal sepsis",
     "newborn sepsis", "neonatal sepsis", "newborn blood infection", "neonatal infection",
     "newborn infection", "neonatal bacteremia"},
    {"gbs", "group b streptococcus", "group b strep", "streptococcus agalactiae"},
    {"rom", "rupture of membranes", "prom", "prelabor rupture of membranes",
     "premature rupture of membranes", "prolonged rupture of membranes", "membrane rupture"},
    {"chorioamnionitis", "intra-amniotic infection", "intraamniotic infection",
     "womb infection", "amnionitis"},
    {"crp", "c-reactive protein", "c reactive protein", "inflammatory marker"},
    {"pct", "procalcitonin"},
    {"wbc", "white blood cell count", "white cell count", "leukocyte count", "cbc",
     "complete blood count", "full blood count", "fbc"},
    {"i:t ratio", "it ratio", "immature to total ratio", "immature-to-total neutrophil ratio"},
    {"iap", "intrapartum antibiotic prophylaxis", "intrapartum antibiotics",
     "maternal antibiotics", "peripartum antibiotics"},
    {"antibiotics", "antibiotic therapy", "antimicrobial therapy", "empirical antibiotics",
     "empiric antibiotics", "abx"},
    {"ampicillin", "amoxicillin"},  # clinically substitutable in most EOS regimens
    {"benzylpenicillin", "penicillin g", "penicillin"},
    {"gentamicin", "aminoglycoside"},
    {"blood culture", "bacteremia", "culture-positive", "blood cultures"},
    {"respiratory distress", "tachypnoea", "tachypnea", "grunting", "breathing difficulty",
     "increased work of breathing"},
    {"cpap", "continuous positive airway pressure", "respiratory support",
     "mechanical ventilation", "oxygen requirement", "supplemental oxygen"},
    {"hypothermia", "low temperature", "temperature instability"},
    {"fever", "pyrexia", "maternal fever", "intrapartum fever", "hyperthermia"},
    {"lethargy", "irritability", "poor tone", "floppy", "hypotonia", "reduced activity"},
    {"poor perfusion", "shock", "capillary refill", "hypotension"},
    {"apgar", "apgar score"},
    {"preterm", "premature", "prematurity", "preterm birth"},
    {"thrombocytopenia", "low platelets", "platelet count"},
    {"nicu", "neonatal intensive care", "neonatal intensive care unit", "special care baby unit", "scbu"},
    {"stewardship", "antibiotic stewardship", "antimicrobial stewardship"},
]

# Precompute term -> cluster index for fast lookup
_TERM_TO_CLUSTER: dict[str, int] = {}
for _idx, _cluster in enumerate(_SYNONYM_CLUSTERS):
    for _term in _cluster:
        _TERM_TO_CLUSTER[_term] = _idx


def _find_matches(text_lower: str) -> set[int]:
    """Return the set of cluster indices whose terms appear in text_lower."""
    matched: set[int] = set()
    for term, cluster_idx in _TERM_TO_CLUSTER.items():
        # Word-boundary match for single words; substring match for phrases
        # (phrases already carry enough specificity to avoid false positives).
        if " " in term or ":" in term:
            if term in text_lower:
                matched.add(cluster_idx)
        else:
            if re.search(rf"\b{re.escape(term)}\b", text_lower):
                matched.add(cluster_idx)
    return matched


def expand_query(query: str, max_extra_terms: int = 12) -> str:
    """
    Expand a free-text clinical query with same-cluster synonyms.

    Returns the original query with additional terms appended (deduplicated,
    original casing preserved for the base query). Capped at max_extra_terms
    to avoid diluting BM25/embedding signal with an overlong query.
    """
    if not query or not query.strip():
        return query

    text_lower = query.lower()
    clusters = _find_matches(text_lower)
    if not clusters:
        return query

    extra: list[str] = []
    seen = set(re.findall(r"[a-z0-9:]+", text_lower))
    for idx in clusters:
        for term in _SYNONYM_CLUSTERS[idx]:
            if term in seen:
                continue
            extra.append(term)
            seen.add(term)
            if len(extra) >= max_extra_terms:
                break
        if len(extra) >= max_extra_terms:
            break

    if not extra:
        return query
    return query + " " + " ".join(extra)


def normalize_for_matching(text: str) -> str:
    """
    Canonicalise a piece of text by replacing every recognised synonym with
    the first (canonical) term in its cluster. Useful for a cheap
    apples-to-apples comparison (e.g. dedup / diversity checks) where exact
    wording differs but clinical meaning is identical.
    """
    text_lower = text.lower()
    for cluster in _SYNONYM_CLUSTERS:
        canonical = sorted(cluster, key=len)[0]
        for term in sorted(cluster, key=len, reverse=True):
            if term == canonical:
                continue
            pattern = rf"\b{re.escape(term)}\b" if " " not in term else re.escape(term)
            text_lower = re.sub(pattern, canonical, text_lower)
    return text_lower