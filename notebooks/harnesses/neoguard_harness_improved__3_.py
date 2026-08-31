"""
neoguard_harness.py
====================

Frozen, offline experiment harness for the NeoGuard EOS RAG evaluation.

This module is a *research harness*, not a copy-paste of the production
backend. It reimplements the same logic as the real
backend/rag/{retrieve,rrf,query_builder,query_refiner,clinical_terms}.py and
backend/domain/contraindication_rules.py and the relevant slices of
backend/services/gemini_service.py, so that:

  - RQ1/RQ2 (retrieval ablations) run against a local chunk store instead of
    Supabase/pgvector, with the exact same fusion/rerank/MMR algorithm.
  - RQ3 (disambiguation/safety) runs the exact same deterministic trigger
    logic (_detect_cross_guideline_conflict, check_contraindications with
    KDIGO staging, trend_state_change) as the shipped app.

Nothing here writes to Supabase, calls FastAPI routes, or touches the mobile
app. The only network calls this module makes are (a) downloading embedding/
reranker models from HuggingFace on first use, and (b) LLM provider calls
you explicitly configure (Gemini/Groq API key, or a local Ollama endpoint
if you happen to be running one and point at it).

Where this harness *diverges* from the real backend, it is commented
explicitly with "DIVERGENCE:" so these are citable, disclosed limitations
in the paper's methods section rather than silent discrepancies.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Literal

import numpy as np

# ─────────────────────────────────────────────────────────────────────────
# 1. clinical_terms.py — copied verbatim (self-contained, no backend deps)
# ─────────────────────────────────────────────────────────────────────────

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
    {"ampicillin", "amoxicillin"},
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
    {"lbw", "low birth weight", "birth weight", "birthweight"},
]

_TERM_TO_CLUSTER: dict[str, int] = {}
for _idx, _cluster in enumerate(_SYNONYM_CLUSTERS):
    for _term in _cluster:
        _TERM_TO_CLUSTER[_term] = _idx


def _find_matches(text_lower: str) -> set[int]:
    matched: set[int] = set()
    for term, cluster_idx in _TERM_TO_CLUSTER.items():
        if " " in term or ":" in term:
            if term in text_lower:
                matched.add(cluster_idx)
        else:
            if re.search(rf"\b{re.escape(term)}\b", text_lower):
                matched.add(cluster_idx)
    return matched


def expand_query(query: str, max_extra_terms: int = 12) -> str:
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


# ─────────────────────────────────────────────────────────────────────────
# 2. rrf.py — copied verbatim
# ─────────────────────────────────────────────────────────────────────────

def reciprocal_rank_fusion(
    ranked_lists: list[list[Any]],
    k: int = 60,
    id_fn=None,
) -> list[tuple[Any, float]]:
    if id_fn is None:
        id_fn = lambda x: getattr(x, "node_id", None) or getattr(x, "id", None) or str(x)
    scores: dict[str, float] = {}
    items: dict[str, Any] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            item_id = str(id_fn(item))
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            items[item_id] = item
    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(items[item_id], score) for item_id, score in merged]


# ─────────────────────────────────────────────────────────────────────────
# 3. contraindication_rules.py — copied verbatim (self-contained, KDIGO-staged)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ContraindicationFlag:
    drug: str
    reason: str
    severity: str = "HIGH"


_GENTAMICIN_NAMES = {"gentamicin", "gentamycin"}
_PENICILLIN_NAMES = {"penicillin", "ampicillin", "benzylpenicillin", "amoxicillin"}
_AKI_STAGE_SEVERITY = {1: "HIGH", 2: "CRITICAL", 3: "CRITICAL"}


def _normalize_drug(name: str) -> str:
    return name.strip().lower()


def _staged_nephrotoxicity_flag(drug: str, aki: dict) -> ContraindicationFlag | None:
    stage = aki.get("aki_stage")
    if not stage or int(stage) < 1:
        return None
    reasons = []
    creat_stage = aki.get("creatinine_aki_stage") or 0
    urine_stage = aki.get("urine_aki_stage") or 0
    if creat_stage >= 1:
        rise48 = aki.get("creatinine_rise_48h")
        ratio = aki.get("creatinine_ratio_to_baseline")
        if rise48 is not None and float(rise48) >= 0.3:
            reasons.append(f"creatinine rose {float(rise48):.2f} mg/dL within 48h (KDIGO stage {creat_stage})")
        elif ratio is not None:
            reasons.append(f"creatinine {float(ratio):.2f}x baseline (KDIGO stage {creat_stage})")
    if urine_stage >= 1:
        reasons.append(f"sustained oliguria meeting KDIGO stage {urine_stage} urine-output criteria")
    if not reasons:
        return None
    return ContraindicationFlag(
        drug=drug,
        reason="Neonatal-modified KDIGO AKI stage " + str(int(stage)) + " — nephrotoxicity risk with "
               "gentamicin: " + "; ".join(reasons),
        severity=_AKI_STAGE_SEVERITY.get(int(stage), "HIGH"),
    )


def _snapshot_fallback_flag(drug: str, patient_snapshot: dict) -> ContraindicationFlag | None:
    urine = patient_snapshot.get("urine_output_ml_kg_hr")
    creatinine = patient_snapshot.get("creatinine_mg_dl")
    low_urine = urine is not None and float(urine) < 0.5
    high_creatinine = creatinine is not None and float(creatinine) > 1.5
    if not (low_urine or high_creatinine):
        return None
    reasons = []
    if low_urine:
        reasons.append(f"single urine output reading {urine} mL/kg/hr (<0.5)")
    if high_creatinine:
        reasons.append(f"single creatinine reading {creatinine} mg/dL (>1.5, unstaged)")
    return ContraindicationFlag(
        drug=drug,
        reason=(
            "Possible nephrotoxicity risk with gentamicin (UNSTAGED — single reading only, "
            "no prior assessment to compare against, does not yet meet full KDIGO staging "
            "criteria): " + "; ".join(reasons)
        ),
        severity="HIGH",
    )


def check_contraindications(
    patient_snapshot: dict,
    proposed_drugs: list[str],
    deltas: dict | None = None,
) -> list[ContraindicationFlag]:
    flags: list[ContraindicationFlag] = []
    normalized = [_normalize_drug(d) for d in proposed_drugs]
    for drug in normalized:
        if any(g in drug for g in _GENTAMICIN_NAMES):
            flag = None
            if deltas:
                flag = _staged_nephrotoxicity_flag(drug, deltas)
            if flag is None:
                flag = _snapshot_fallback_flag(drug, patient_snapshot)
            if flag is not None:
                flags.append(flag)
        if any(p in drug for p in _PENICILLIN_NAMES):
            allergy = patient_snapshot.get("penicillin_allergy")
            if allergy is True:
                flags.append(ContraindicationFlag(
                    drug=drug,
                    reason="Documented penicillin allergy — avoid beta-lactam agents",
                    severity="CRITICAL",
                ))
    return flags


def flags_to_strings(flags: list[ContraindicationFlag]) -> list[str]:
    return [f"[{f.severity}] {f.drug}: {f.reason}" for f in flags]


_WHO_LBW_EXCLUSION_G = 1500
_WHO_RECENT_HOSPITALIZATION_DAYS = 14


def check_who_outpatient_exclusions(
    patient_snapshot: dict,
    care_setting: str = "hospital",
) -> list[ContraindicationFlag]:
    """
    Mirror of contraindication_rules.py's check_who_outpatient_exclusions()
    (see that module for the full docstring). WHO's PSBI outpatient regimens
    do not apply to birth weight <1500g or infants hospitalized for illness
    in the prior 2 weeks — these infants must be hospitalized regardless of
    the presenting sign pattern. care_setting must be "outpatient_no_referral"
    for this to fire at all; it's a no-op for hospital-based care.
    """
    if care_setting != "outpatient_no_referral":
        return []
    flags: list[ContraindicationFlag] = []
    birth_weight = patient_snapshot.get("birth_weight_g")
    if birth_weight is not None and float(birth_weight) < _WHO_LBW_EXCLUSION_G:
        flags.append(ContraindicationFlag(
            drug="WHO outpatient PSBI regimen (oral amoxicillin / IM gentamicin+amoxicillin)",
            reason=(
                f"Birth weight {birth_weight}g is under WHO's explicit "
                f"{_WHO_LBW_EXCLUSION_G}g exclusion threshold for outpatient PSBI "
                "regimens — this infant must be hospitalized regardless of the "
                "presenting sign pattern or referral accessibility."
            ),
            severity="CRITICAL",
        ))
    recent_hosp = patient_snapshot.get("hospitalized_within_prior_14_days")
    if recent_hosp is True:
        flags.append(ContraindicationFlag(
            drug="WHO outpatient PSBI regimen (oral amoxicillin / IM gentamicin+amoxicillin)",
            reason=(
                f"Infant was hospitalized for illness within the prior "
                f"{_WHO_RECENT_HOSPITALIZATION_DAYS} days — WHO's outpatient "
                "regimens explicitly do not apply; this infant must be "
                "hospitalized for the current illness."
            ),
            severity="CRITICAL",
        ))
    return flags


# RESOLVED (previously a DIVERGENCE): contraindication_rules.py originally
# only encoded two rules — gentamicin/KDIGO nephrotoxicity and penicillin
# allergy. The golden vignette set's 20 "contraindication" vignettes also
# test a WHO PSBI outpatient-eligibility rule (<1500g birth weight / recent
# hospitalization) the shipped code did not implement.
# check_who_outpatient_exclusions() above adds that rule to both
# contraindication_rules.py and this mirror, so §5.2a (code-level) and §5.2b
# (generation-level) now measure the SAME underlying rule for that subset of
# vignettes (5 of the 20: 3 birth-weight + 2 recent-hospitalization cases).
#
# STILL A DISCLOSED GAP: the remaining 15 contraindication vignettes test
# rules that are NOT encoded as deterministic code anywhere in this
# codebase — confirmed Gram-negative organism requiring a regimen change,
# NICE's gentamicin-interval exceptions, maternal beta-lactam allergy,
# major congenital malformation precluding oral dosing, and unexplained
# bleeding/thrombocytopenia. These remain generation-level-only checks
# (§5.2b evaluates all 20 vignettes; only the LBW/recent-hosp subset also
# has a §5.2a code-level counterpart). Report this honestly rather than
# implying full code coverage of the stratum.


# ─────────────────────────────────────────────────────────────────────────
# 4. Deterministic care-plan checks — adapted from gemini_service.py
#    (only the two pure functions needed; no LLM call inside these)
# ─────────────────────────────────────────────────────────────────────────

# ── Exact guideline-source matching (BUGFIX 2026-08) ────────────────────
# The original checks here and in retrieve_evidence_harness's guideline
# boost both did `guideline_upper in source_key` -- a SUBSTRING test on
# (source_name+source).upper(). That's silently wrong whenever one
# source tag is a prefix of another: chunks_ingested_*.csv tags the
# preterm-specific AAP document "AAP_PRETERM" (confirmed by direct
# inspection -- distinct from "AAP", which is the term/Kaiser-calculator
# document), and "AAP" is a substring of "AAP_PRETERM". So for every
# active_guideline=="AAP" case, AAP_PRETERM chunks were silently treated
# as on-guideline: they earned the retrieval guideline boost AND were
# invisible to detect_cross_guideline_conflict's off-guideline count --
# regardless of whether the case was actually about a preterm infant.
# Confirmed empirically against the 30-case care_plan_dataset: gold_
# chunk_ids never cites AAP_PRETERM for ANY case (active_guideline is
# exactly {NICE, WHO, AAP}, never AAP_PRETERM), so for retrieval purposes
# "AAP" means the "AAP" source tag only. Replaced with an exact,
# allow-listed match below.
_GUIDELINE_ALLOWED_SOURCES = {"NICE": {"NICE"}, "WHO": {"WHO"}, "AAP": {"AAP"}}


def _guideline_source_match(active_guideline: str, source: str) -> bool:
    """Exact allow-listed match -- NOT substring containment. See BUGFIX
    note above _GUIDELINE_ALLOWED_SOURCES for why substring matching was
    wrong here."""
    source = (source or "").strip().upper()
    allowed = _GUIDELINE_ALLOWED_SOURCES.get((active_guideline or "").upper())
    if allowed is not None:
        return source in allowed
    return (active_guideline or "").upper() == source


def detect_cross_guideline_conflict(chunk_sources: list[str], active_guideline: str) -> bool:
    """True when >=2 distinct off-guideline sources appear in retrieved chunks.
    Adapted from gemini_service.py's _detect_cross_guideline_conflict,
    parameterised on a list of source strings instead of EvidenceChunkResult
    objects so it works against this harness's ChunkResult too.
    BUGFIX 2026-08: now uses the exact _guideline_source_match instead of
    a substring test -- see that function's docstring. This means an
    AAP_PRETERM chunk leaking into an AAP-guideline case is now correctly
    counted as off-guideline instead of silently passing as a match."""
    off_guideline_sources: set[str] = set()
    for source_key in chunk_sources:
        source_key = (source_key or "").strip()
        if not source_key:
            continue
        if not _guideline_source_match(active_guideline, source_key):
            off_guideline_sources.add(source_key.upper())
    return len(off_guideline_sources) >= 2


# ── WHO severity-tier awareness ──────────────────────────────────────────
# WHO's PSBI guideline has multiple parallel severity tracks (critical
# illness / A.2 vs. clinical severe infection-PSBI / A.3) that share a lot
# of surface vocabulary (ampicillin, gentamicin, referral all appear in
# more than one tier), so plain semantic/BM25 retrieval has no reliable
# way to tell them apart. Confirmed empirically against the 30-case
# care_plan_dataset: across every WHO case in ablation.csv/model1.csv,
# retrieved/gold chunk-id overlap was exactly zero, and tracing individual
# chunks showed a systematic tier SWAP -- CRITICAL cases (gold = the A.2
# "critical illness" chunk) were being handed the A.3 "clinical severe
# infection" chunk, and INTERMEDIATE/LOW cases had it reversed. Chunk text
# reliably self-identifies its tier (section headers and body text
# literally say "critical illness" or "clinical severe infection"/PSBI),
# so this is fixable at scoring time without new data -- see
# who_severity_tier_score() and its use in retrieve_evidence_harness /
# retrieve_evidence_graph, and the matching query-side terms added in
# build_clinical_query_harness.
_WHO_TIER_TERMS = {
    "critical": ["critical illness", "recommendation a.2", "a.2 (updated)", "a.2(updated)"],
    "clinical_severe": ["clinical severe infection", "recommendation a.3", "a.3a", "a.3b",
                          "possible serious bacterial infection", "psbi"],
}
_CATEGORY_TO_WHO_TIER = {"CRITICAL": "critical", "INTERMEDIATE": "clinical_severe", "HIGH": "clinical_severe"}

# BUGFIX 2026-08d: page-range clustering, primary signal (was keyword-only).
# Mapped by hand from chunks_ingested_1_.csv's WHO page/section listing:
# chunk_id "WHO_pgN_..." pages 1-4 are A.2's own pre-recommendation evidence
# review (Overview/PIRD/Sources/Critical outcomes/Other studies/Equity),
# page 5 is "Recommendation A.2 (UPDATED)" itself plus its Remarks/
# Background, pages 6-7 are a second Overview/Sources round for the same
# recommendation -- all of it critical-illness-tier. Page 8 is
# "Recommendation A.3a (UPDATED)" and pages 9-13 are ITS evidence review --
# clinical-severe-infection-tier. This directly targets the confirmed bug
# (CRITICAL cases retrieving the A.3a chunk and vice versa) with a much
# stronger signal than phrase-matching: reading the actual gold chunks for
# these cases showed several (e.g. WHO_pg6_remarks_chunk1, a genuine A.2
# remarks chunk) never say the words "critical illness" at all, so a
# keyword-only detector silently missed them. Deliberately narrow -- pages
# past 13 (A.4/A.5/staph/meningitis/the pg42-45 dosing-table summary that
# legitimately covers BOTH tiers side by side) are left unclassified
# (tier=None, neutral) rather than guessed, since misclassifying genuinely
# shared/dual-tier content would trade one bug for a subtler one.
_WHO_TIER_PAGE_RANGES = {"critical": range(1, 9), "clinical_severe": range(9, 14)}


def _who_case_tier(category: str | None) -> str | None:
    return _CATEGORY_TO_WHO_TIER.get((category or "").upper())


def _tier_from_text(text: str) -> str | None:
    """Phrase-based fallback (and the only signal available for the query
    text itself, which has no chunk_id/page). BUGFIX 2026-08d: previously
    returned the FIRST matching tier in dict iteration order, so a chunk
    that legitimately mentions BOTH tiers (e.g. WHO_pg45's side-by-side
    A.2-vs-A.3 dosing table) was silently assigned to whichever tier came
    first and then penalized as "wrong tier" for the other -- exactly
    backwards for genuinely shared content. Now returns None (neutral)
    whenever more than one tier's phrases are present."""
    text_l = (text or "").lower()
    matched = {tier for tier, terms in _WHO_TIER_TERMS.items() if any(t in text_l for t in terms)}
    return matched.pop() if len(matched) == 1 else None


def _who_tier_from_query(query: str) -> str | None:
    """Recovers the case's WHO severity tier from the query TEXT rather than
    a separate parameter, so retrieve_evidence_harness/retrieve_evidence_
    graph don't need a new argument threaded through the shared
    retrieve_fn(query, active_guideline, top_k) interface every arm
    (vector/graph/hybrid) implements identically. Relies on
    build_clinical_query_harness now always appending the tier phrase for
    WHO cases (see that function)."""
    return _tier_from_text(query)


def _chunk_who_page(chunk_id: str) -> int | None:
    m = re.match(r"WHO_pg(\d+)_", chunk_id or "")
    return int(m.group(1)) if m else None


def _chunk_who_tier(chunk_id: str, chunk_text: str) -> str | None:
    page = _chunk_who_page(chunk_id)
    if page is not None:
        for tier, rng in _WHO_TIER_PAGE_RANGES.items():
            if page in rng:
                return tier
    return _tier_from_text(chunk_text)


def who_severity_tier_score(active_guideline: str, case_tier: str | None, chunk_source: str,
                             chunk_id: str, chunk_text: str) -> float:
    """+1.0 if a WHO chunk's tier (page-range primary, phrase fallback --
    see _chunk_who_tier) matches the case's tier, -1.0 if it's a WHO chunk
    that identifies as the OTHER tier, 0.0 otherwise (non-WHO
    chunks/guidelines, tier-agnostic/shared WHO chunks, or no case tier)."""
    if (active_guideline or "").upper() != "WHO" or (chunk_source or "").upper() != "WHO":
        return 0.0
    if case_tier is None:
        return 0.0
    chunk_tier = _chunk_who_tier(chunk_id, chunk_text)
    if chunk_tier is None:
        return 0.0
    return 1.0 if chunk_tier == case_tier else -1.0


def trend_state_change_note(deltas: dict | None) -> str:
    """Copied verbatim from the trend_state_change branch of
    _apply_deterministic_care_plan_checks in gemini_service.py."""
    if not deltas:
        return ""
    score_delta = deltas.get("score_delta")
    if score_delta is not None and abs(float(score_delta)) >= 2:
        direction = "deteriorating" if float(score_delta) > 0 else "improving"
        return f"Deterministic trend note: EOSCAL score {direction} (delta {score_delta}) since last assessment."
    return ""


# ─────────────────────────────────────────────────────────────────────────
# 5. Corpus — gold-chunk corpus derived from the vignette set's own
#    citation_notes, since the 9 source PDFs' bytes are not available in
#    this environment. See README section "Corpus modes" for how to swap
#    in a real-PDF-ingested corpus using the actual pdf_extract.py ->
#    clean.py -> chunk.py pipeline (single hierarchical chunking strategy)
#    instead of this stub.
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    source: str          # NICE | AAP | WHO
    source_name: str
    section: str
    chunk_text: str
    embedding: np.ndarray | None = None
    # Retrieval-time grouping metadata (optional -- absent/blank for the stub
    # corpus and distractor chunks, which simply never get grouped). Mirrors
    # the columns chunk.py already writes to chunks_clinical_v2.csv; nothing
    # about chunk.py or the ingest pipeline needs to change for this to work.
    page: int = -1
    subsection: str = ""
    recommendation_id: str = ""
    chunk_type: str = ""


_SOURCE_NAME = {"AAP": "AAP 2018 EOS Report", "WHO": "WHO 2015 PSBI Guideline", "NICE": "NICE NG195 (2026-amended)"}


def _infer_source(chunk_id: str) -> str:
    if chunk_id.startswith("aap"):
        return "AAP"
    if chunk_id.startswith("who"):
        return "WHO"
    if chunk_id.startswith("nice"):
        return "NICE"
    return "UNKNOWN"


# A handful of generic distractor chunks — plausible-sounding EOS text that
# is NOT cited by any vignette gold_answer, so retrieval precision/negative-
# distractor metrics have something real to discriminate against instead of
# being trivially 100% on a 25-chunk corpus where every chunk is relevant to
# some query. Also includes two NEC-adjacent sentences (irrelevant to EOS)
# to give the negative_distractor stratum a genuine "don't force-fit" test.
_DISTRACTOR_CHUNKS = [
    ("nice_1.30.1_parent_info", "NICE", "General parent information leaflets should be offered alongside any antibiotic course discussion, covering what signs to watch for after discharge."),
    ("aap2018_p1_epidemiology", "AAP", "Early-onset sepsis incidence has declined substantially since universal intrapartum GBS prophylaxis was introduced in the 1990s-2000s."),
    ("who2015_followup", "WHO", "Follow-up visits after completing a PSBI outpatient course should occur on days 4, 8, and 15 for weight check and symptom review."),
    ("generic_nec_note_1", "GENERAL", "Necrotizing enterocolitis classically presents beyond the first week of life with feeding intolerance, abdominal distension, and bloody stools; management is supportive with bowel rest, not antibiotics for sepsis."),
    ("generic_jaundice_note_1", "GENERAL", "Neonatal jaundice appearing after 24 hours of life is usually physiological and managed with phototherapy thresholds unrelated to sepsis risk."),
    ("nice_1.5.2_temp_monitoring", "NICE", "Continuous or frequent temperature monitoring is recommended for babies with any risk factor, even if antibiotics are not started immediately."),
    ("aap2018_p4_gbs_screening", "AAP", "Universal antenatal GBS screening at 36-37 weeks gestation determines intrapartum prophylaxis eligibility but does not itself change postnatal management algorithms."),
]


def build_stub_corpus(vignette_json_path: str) -> list[Chunk]:
    with open(vignette_json_path) as f:
        data = json.load(f)
    notes: dict[str, str] = data["metadata"]["citation_notes"]

    chunks: list[Chunk] = []
    for chunk_id, note in notes.items():
        source = _infer_source(chunk_id)
        chunks.append(Chunk(
            chunk_id=chunk_id,
            source=source,
            source_name=_SOURCE_NAME.get(source, source),
            section=chunk_id,
            chunk_text=note,
        ))
    for chunk_id, source, text in _DISTRACTOR_CHUNKS:
        chunks.append(Chunk(
            chunk_id=chunk_id,
            source=source,
            source_name=_SOURCE_NAME.get(source, "General reference"),
            section=chunk_id,
            chunk_text=text,
        ))
    print(f"[Corpus] Built stub corpus: {len(notes)} gold chunks + {len(_DISTRACTOR_CHUNKS)} distractors "
          f"= {len(chunks)} total")
    return chunks


def build_stub_corpus_from_qa_csv(qa_csv_path: str) -> list[Chunk]:
    """Stub corpus builder for neonatal_eos_qa_eval_dataset.csv (the
    generation-axis QA eval set), for cases where the real guideline chunk
    corpus (chunks_clinical_v2.csv, or the AAP/WHO/NICE PDFs + ingest
    pipeline) is not available.

    DIVERGENCE / LIMITATION (same caveat as build_stub_corpus above, worse
    here): the QA CSV does NOT ship chunk_text -- only gold_chunk_id, page,
    and section metadata per question. This function therefore uses each
    row's own reference_answer as a stand-in for that gold chunk's text.
    That means gold-chunk retrieval will trivially succeed (the "chunk"
    being searched for IS the reference answer's own wording) and any
    BLEU/METEOR/BERTScore computed on generations grounded against this
    stub corpus will be inflated relative to a real-PDF corpus, where the
    retrieved guideline text is independent prose, not the reference
    answer. Use this ONLY to smoke-test that retrieval -> generation ->
    metrics wiring runs end-to-end; for paper-quality numbers, build the
    real corpus via build_corpus_from_real_ingest() or load_corpus_from_csv()
    instead.
    """
    import csv as _csv
    chunks: list[Chunk] = []
    seen_ids: set[str] = set()
    with open(qa_csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            chunk_id = (row.get("gold_chunk_id") or "").strip()
            if not chunk_id or chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            source = (row.get("source_guideline") or "").strip().upper() or _infer_source(chunk_id.lower())
            try:
                page = int(row.get("page", -1) or -1)
            except ValueError:
                page = -1
            chunks.append(Chunk(
                chunk_id=chunk_id,
                source=source,
                source_name=_SOURCE_NAME.get(source, source),
                section=row.get("section") or chunk_id,
                chunk_text=row["reference_answer"],
                page=page,
                chunk_type=row.get("chunk_type", ""),
            ))
    for chunk_id, source, text in _DISTRACTOR_CHUNKS:
        chunks.append(Chunk(
            chunk_id=chunk_id,
            source=source,
            source_name=_SOURCE_NAME.get(source, "General reference"),
            section=chunk_id,
            chunk_text=text,
        ))
    print(f"[Corpus] Built QA-CSV stub corpus: {len(seen_ids)} gold chunks + "
          f"{len(_DISTRACTOR_CHUNKS)} distractors = {len(chunks)} total  "
          f"(LIMITATION: chunk_text == reference_answer -- see docstring)")
    return chunks


def build_corpus_from_real_ingest(
    pdf_dir: str,
    csv_out: str = "chunks_clinical_v2.csv",
) -> list[Chunk]:
    """
    Real-PDF corpus builder — now backed by the ACTUAL production ingest
    pipeline (pdf_extract.py -> clean.py -> chunk.py, orchestrated by
    run_ingest.py's `ingest_one`), not a re-implementation of it.

    DIVERGENCE from the older version of this function: the old
    fixed/semantic/proposition/clinical(CSASC) four-way chunking-strategy
    dispatch has been removed entirely. There is now exactly ONE chunking
    strategy -- the heading -> paragraph -> sentence hierarchical chunker in
    chunk.py (hard size cap, tiny-chunk merging, sentence-boundary-safe
    splitting) fed by clean.py's noise-removal/paragraph-reconstruction and
    pdf_extract.py's typography-aware PyMuPDF/pdfplumber extraction. No LLM
    call is made anywhere in this path (proposition chunking is gone, so
    `call_llm` is no longer a parameter here).

    Requires `pdf_extract.py`, `clean.py`, `chunk.py`, and `run_ingest.py`
    to be present next to this module (same working directory / uploaded
    alongside it in Colab) -- this function imports `ingest_one` from
    run_ingest.py directly rather than shelling out to it.

    Source labeling: each PDF's guideline source (AAP/WHO/NICE) is inferred
    from its filename (must contain "aap", "who", or "nice"/"ng195"); an
    unrecognized filename falls back to its uppercased stem as the source
    label instead of failing, so an ingest never silently drops a file.

    Side effect: writes every chunk produced (across all PDFs in `pdf_dir`)
    to `csv_out` in the same shape run_ingest.py's own CLI writes
    (chunk_id, source, page, section, subsection, recommendation_id,
    chunk_type, chars, chunk_text) -- one CSV per call, ready to reload,
    diff, or hand off without re-running the PDF extraction.
    """
    import csv
    from pathlib import Path

    from run_ingest import ingest_one

    pdf_dir_path = Path(pdf_dir)
    pdf_files = sorted(pdf_dir_path.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {pdf_dir}")

    def _infer_source_label(filename: str) -> str:
        low = filename.lower()
        if "aap" in low:
            return "AAP"
        if "who" in low:
            return "WHO"
        if "nice" in low or "ng195" in low:
            return "NICE"
        print(f"[RealIngest] WARNING: could not infer source from filename "
              f"{filename!r} (expected 'aap'/'who'/'nice'/'ng195') -- "
              f"falling back to the filename stem as the source label.")
        return Path(filename).stem.upper()

    all_rows: list[dict] = []
    chunks: list[Chunk] = []
    for pdf_path in pdf_files:
        source = _infer_source_label(pdf_path.name)
        rows = ingest_one(source, str(pdf_path))
        all_rows.extend(rows)
        for r in rows:
            chunks.append(Chunk(
                chunk_id=r["chunk_id"],
                source=source,
                source_name=_SOURCE_NAME.get(source, source),
                section=r.get("subsection") or r.get("section") or r["chunk_type"],
                chunk_text=r["chunk_text"],
                page=int(r.get("page", -1) or -1),
                subsection=r.get("subsection", ""),
                recommendation_id=r.get("recommendation_id", ""),
                chunk_type=r.get("chunk_type", ""),
            ))

    fieldnames = ["chunk_id", "source", "page", "section", "subsection",
                  "recommendation_id", "chunk_type", "chars", "chunk_text"]
    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    print(f"[Corpus] Built REAL corpus from {pdf_dir}: {len(chunks)} chunks "
          f"across {len(pdf_files)} PDF(s). Chunk CSV saved -> {csv_out}")
    return chunks


def load_corpus_from_csv(csv_path: str) -> list[Chunk]:
    """Reload a corpus straight from a chunks_clinical_v2.csv previously
    written by build_corpus_from_real_ingest() (or run_ingest.py's CLI) --
    reuses the SAME chunk dataset without re-running PDF extraction or
    touching chunk.py. This is what you want when you're only changing
    retrieval-time behavior (e.g. retrieval grouping, top_k, rerankers) and
    don't need to regenerate the underlying chunks."""
    import csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    chunks: list[Chunk] = []
    for r in rows:
        source = r["source"]
        chunks.append(Chunk(
            chunk_id=r["chunk_id"],
            source=source,
            source_name=_SOURCE_NAME.get(source, source),
            section=r.get("subsection") or r.get("section") or r.get("chunk_type", ""),
            chunk_text=r["chunk_text"],
            page=int(r.get("page", -1) or -1),
            subsection=r.get("subsection", ""),
            recommendation_id=r.get("recommendation_id", ""),
            chunk_type=r.get("chunk_type", ""),
        ))
    print(f"[Corpus] Loaded {len(chunks)} chunks from {csv_path} (no re-ingest)")
    return chunks

# ─────────────────────────────────────────────────────────────────────────
# 5b. Anchor-phrase relevance judgment — makes retrieval scoring work
#    against a REAL, arbitrarily-chunked PDF corpus, not just the stub
#    corpus. RESOLVED a real gap: the stub corpus's chunk_id IS the gold
#    citation key (e.g. "aap2018_p3_categorical"), so exact chunk_id
#    matching against v["gold_answer"]["cited_chunks"] worked there — but a
#    real ingest.py run generates its own ids from PDF page/chunk position
#    (e.g. "aap_fixed_p3_7"), which will NEVER equal a gold citation key.
#    Previously this meant real-PDF mode required a manual, unautomated
#    chunk-ID remapping step (see the old §1.3 comment). Anchor phrases
#    remove that requirement entirely: instead of matching IDs, we check
#    whether a retrieved chunk's TEXT contains enough of the citation key's
#    curated anchor phrases to count as "the same underlying fact" — this
#    works identically whether the corpus is the stub (anchor phrases match
#    trivially, since the stub chunk text literally IS the citation note) or
#    a real PDF corpus with arbitrary boundaries.
#
#    CAVEAT you should sanity-check on your first real run: these 25 anchor
#    phrase lists were hand-curated from the actual AAP/WHO source text
#    (both fully available) and best-effort NICE NG195 terminology (fetched
#    live but not re-verified against your specific uploaded PDF's exact
#    wording/pagination). Some anchors are short/generic enough (e.g. "36
#    hours") that they could coincidentally match an unrelated real chunk
#    discussing a different threshold at the same number. Run the
#    `inspect_anchor_matches()` cell in §1.3 after your first real ingest
#    and eyeball a few keys before trusting RQ1's real-corpus numbers.
# ─────────────────────────────────────────────────────────────────────────

ANCHOR_PHRASES: dict[str, list[str]] = {
    "aap2018_p3_categorical": ["inadequate IAP", "18 hours", "chorioamnionitis"],
    "aap2018_p3_multivariate": ["Risk Calculator", "1 per 1000", "3 per 1000"],
    "aap2018_p5_labs": ["neither sensitive nor specific", "C-reactive protein", "white blood cell"],
    "aap2018_p6_treatment": ["Ampicillin and gentamicin", "first choice", "E coli"],
    "aap2018_p6_duration": ["36 to 48 hours", "sterile"],
    "aap2018_p2_abx_stewardship": ["wheezing", "asthma", "obesity"],
    "who2015_signs": ["severe chest in-drawing", "35.5", "convulsions"],
    "who2015_rec2_3": ["amoxicillin", "50 mg/kg", "twice daily"],
    "who2015_rec4": ["clinical severe infection", "close follow-up"],
    "who2015_rec5": ["critical illness", "hospitalized"],
    "who2015_lbw_exclusion": ["1500 g", "birth weight"],
    "who2015_remarks_worsening": ["day 8", "48 hours"],
    "who2015_amr_caution": ["gentamicin toxicity", "ototoxicity", "nephrotoxicity"],
    "nice_box1_riskfactors": ["rupture of membranes", "chorioamnionitis", "co-twin"],
    "nice_box2_indicators": ["apnoea", "seizures", "36.0", "38.0"],
    "nice_1.10.3_framework": ["red flag", "risk factor", "12 hours"],
    "nice_1.20.1_choice": ["benzylpenicillin", "gentamicin"],
    "nice_1.20.2_dose": ["25 mg/kg", "benzylpenicillin"],
    "nice_1.20.3_4_gent": ["gentamicin", "36 hours", "5 mg/kg"],
    "nice_1.20.8_gramneg": ["cefotaxime", "Gram-negative"],
    "nice_1.21.3_stop36h": ["36 hours", "negative"],
    "nice_1.21.4_review": ["24 hours", "review"],
    "nice_1.21.1_duration": ["7 days", "positive"],
    "nice_1.22.1_oralswitch": ["oral amoxicillin", "35"],
    "nice_1.10.4_calculator": ["calculator", "34"],

    # ─────────────────────────────────────────────────────────────────
    # v2 extension batch (neoguard_eos_vignettes_v2.json) anchors.
    # ROOT CAUSE OF THE "everything is 0" BUG: v2's gold_answer.cited_chunks
    # was generated directly from chunks_clinical.csv's own chunk_ids (e.g.
    # "who_clinical_17", "nice_clinical_35") -- NOT from the v0 dataset's
    # hand-written citation_notes keys (e.g. "who2015_rec4") that the 25
    # entries above were curated for. _anchor_hit() does
    # ANCHOR_PHRASES.get(citation_key) with no fallback, so every one of
    # these v2 keys returned None -> every anchor check was False -> P/R/F1
    # /MRR were all 0.0 for every v2 vignette, in every corpus arm,
    # regardless of retrieval quality. These 16 entries cover every
    # cited_chunks value actually used in v2 (verified against the 40
    # vignettes) -- phrases were pulled from and checked against the exact
    # chunk_text in chunks_clinical.csv for each id (watch line-wraps: a
    # phrase that straddles an embedded newline in the source text will
    # never match _anchor_hit's single-line `in` check).
    # NOTE: if you extend v2 with more vignettes that cite new chunk_ids,
    # you'll get silent 0s again for the new ones unless you add anchors
    # here too -- there's no fallback/warning today. Consider adding a
    # one-time startup assertion that every id in
    # {c for v in vignettes for c in v["gold_answer"]["cited_chunks"]} has
    # an ANCHOR_PHRASES entry, so this fails loudly instead of quietly.
    "aap_clinical_1": ["sensitive nor specific enough", "evidence-based intrapartum", "committee on fetus and newborn"],
    "aap_clinical_5": ["risk factors for eos", "maternal gbs colonization", "chorioamnionitis"],
    "aap_clinical_6": ["gbs iap", "32%", "risk stratificat"],
    "nice_clinical_13": ["pastoral support", "bacterial meningitis", "evidence review j"],
    "nice_clinical_18": ["pre-term birth following spontaneous labour", "rupture of membranes for more than 24 hours before a term birth", "invasive group b streptococcal infection in a previous baby"],  # PATCHED 2026-07-25: original anchors matched 0 real chunks ("ng195" and "trans men and non-binary" are either absent or on nearly every NICE page); new anchors verified unique to NICE_pg12_box-1-risk-factors-for-e_chunk1
    "nice_clinical_21": ["10 mmol/litre", "1.10.3", "red flag"],
    "nice_clinical_22": ["kaiser permanente neonatal sepsis calculator", "34+0 weeks", "12 hours"],
    "nice_clinical_35": ["benzylpenicillin sodium with gentamicin", "25 mg/kg every 12 hours", "1.20.1"],
    "nice_clinical_69": ["clindamycin", "group b streptococcus", "penicillin allergy"],
    "nice_clinical_70": ["severe penicillin allergy", "anaphylaxis", "cephalosporins were not recommended"],
    "who_clinical_16": ["imci signs of critical illness", "critical illness should be referred to hospital", "ampicillin im/iv plus gentamicin im/iv for at least 10 days"],  # PATCHED 2026-07-25: original anchors matched 0 real chunks (that guideline-structure/annexes text didn't survive front-matter filtering); new anchors verified unique to WHO_pg6_recommendation-a-2-updat_rec1_chunk1 (Recommendation A.2), which is the actual clinical content these vignettes cite
    "who_clinical_17": ["oral amoxicillin for at least 7 days", "gentamicin im/iv for at least 7 days", "imci sign of illness"],
    "who_clinical_18": ["ampicillin im/iv", "cloxacillin im/iv", "at least 10 days"],
    "who_clinical_21": ["clinical severe infection", "imci algorithm", "breaths per minute"],
    "who_clinical_24": ["signs of sbis", "advance rapidly", "false positives and false negatives"],
    "who_clinical_33": ["who aware antibiotic classification", "antimicrobial stewardship programmes", "emerging amr in community and hospital"],  # PATCHED 2026-07-25: original anchors ("access/watch/reserve antibiotics") matched 0 real chunks -- that specific AWaRe-tier breakdown text didn't survive extraction; new anchors verified unique to WHO_pg44_resistance-amr-patterns_chunk1, the closest real content (references the AWaRe system without spelling out the three tiers)
    # who_clinical_24: NO WORKING ANCHOR EXISTS. Extensive verification (exact
    # substring, whitespace-normalized substring, and thematic search) found
    # this citation's underlying text ("clinical signs often advance rapidly...
    # false positives and false negatives... poor specificity of clinical
    # syndromes... rational use of antibiotics") nowhere in chunks_clinical_v2.csv.
    # It's real WHO guideline content (see metadata.citation_notes.who_clinical_24
    # in the vignette JSON) but the page it's on apparently didn't survive
    # pdf_extract.py/clean.py's front-matter/TOC/admin-section filtering -- worth
    # checking WHO pages 7/21/32/41, which are the only gaps in the WHO page
    # range covered by the current CSV. Until that's fixed at the ingest level,
    # any real-corpus vignette citing who_clinical_24 will correctly score 0 on
    # that specific citation (this is honest, not a bug) -- v2_lay_005,
    # v2_ambig_005, v2_contra_003, v2_contra_006.
}


def _anchor_hit(chunk_text: str, citation_key: str, min_fraction: float = 0.6,
                 chunk_id: str | None = None) -> bool:
    import math, re as _re
    # Exact-ID fast path: for the real-PDF ingest arm, chunk_id is built by
    # chunk.py as f"{source}_pg{page}_{section-slug}[_recN]_chunkN" (see
    # build_corpus_from_real_ingest / chunk_document), which is the SAME
    # scheme v2's gold_answer.cited_chunks were drawn from (they're literal
    # chunk_ids out of chunks_clinical_v2.csv). When the retrieved chunk's
    # own id equals the gold citation key, that's a certain match -- no
    # need to guess via phrases, and it works even for a citation key that
    # has no ANCHOR_PHRASES entry yet.
    if chunk_id is not None and chunk_id == citation_key:
        return True
    anchors = ANCHOR_PHRASES.get(citation_key)
    if not anchors:
        return False
    # PATCHED 2026-07-25: PyMuPDF preserves the PDF's original non-breaking
    # spaces (U+00A0, "\xa0") wherever the source document used them --
    # overwhelmingly between numbers and their units ("24\xa0hours",
    # "60\xa0breaths per minute", "7\xa0days"). Every anchor phrase built
    # around a number+unit threshold (a large fraction of them) used a
    # regular space and silently never matched, because this used to be a
    # bare `phrase.lower() in chunk_text.lower()` with no whitespace
    # normalization at all. Confirmed live: 2 of 3 nice_clinical_18 anchors
    # failed for exactly this reason on a chunk that visibly contains the
    # phrase. Normalizing all whitespace (nbsp, tabs, newlines, runs of
    # spaces) to a single regular space on both sides fixes it corpus-wide,
    # not just for this one key.
    def _norm_ws(s: str) -> str:
        return _re.sub(r"\s+", " ", s)
    text_low = _norm_ws(chunk_text.lower())
    hits = sum(1 for phrase in anchors if _norm_ws(phrase.lower()) in text_low)
    needed = max(1, math.ceil(len(anchors) * min_fraction))
    return hits >= needed


def _group_results_into_slots(results: list) -> list[list]:
    """Reconstruct retrieval 'slots' (top_k positions) from a flat
    ChunkResult list, using the group_rank tag retrieve_evidence_harness
    sets when it expands a retrieval group: every result sharing the same
    group_rank came from ONE top_k position and is grouped back into one
    slot here. Results with group_rank == 0 (ungrouped -- stub corpus,
    seed-chunk fallback, or any ChunkResult built the old way) each get
    their own singleton slot, exactly matching pre-grouping behavior."""
    slots: dict[int, list] = {}
    order: list[int] = []
    ungrouped_counter = 0
    for r in results:
        rank = getattr(r, "group_rank", 0) or 0
        if rank <= 0:
            ungrouped_counter -= 1
            rank = ungrouped_counter  # unique negative key -> its own slot
        if rank not in slots:
            slots[rank] = []
            order.append(rank)
        slots[rank].append(r)
    return [slots[k] for k in order]


def precision_recall_f1_anchor(results: list, gold_keys: list[str]) -> dict[str, float]:
    """Anchor-phrase equivalent of precision_recall_f1(), taking retrieved
    ChunkResult objects (needs .chunk_text) instead of bare ids. Use this
    (not the exact-id precision_recall_f1) for all RQ1/RQ2 scoring — it's
    correct in stub-corpus mode too (anchor phrases trivially match
    themselves there), so this is now the single scoring path regardless of
    which corpus mode you're running.

    Scores by SLOT, not by raw result: when retrieve_evidence_harness
    expands a retrieval group (a recommendation + its same-page companion
    chunks) into several ChunkResults, they all share one group_rank and
    are treated here as the single top_k position they actually occupied.
    A slot counts as a match if ANY of its member chunk_ids anchor-matches
    a gold key -- i.e. it's enough for the right fact to be present
    somewhere in the group, not necessarily in the top-scoring member."""
    slots = _group_results_into_slots(results)
    if not gold_keys:
        # PATCHED 2026-07-25: this used to require len(results) == 0 to score
        # anything but 0.0 -- but retrieve_evidence_harness always returns up
        # to top_k results by construction, so negative_distractor scored
        # precision=recall=f1=0.0 on every single run regardless of retrieval
        # quality (confirmed: this is why negative_distractor was flat 0.000
        # across the board). The correct question for this stratum is "did
        # retrieval avoid surfacing any OTHER stratum's gold chunk as a false
        # positive", not "did it return literally nothing". A chunk counts as
        # a false positive only if it anchor-matches some OTHER vignette's
        # gold key; distractor-only chunks (or anything with no anchor match
        # at all) are the expected, correct result here.
        all_other_keys = [k for k in ANCHOR_PHRASES if k not in gold_keys]
        false_positive = any(
            any(_anchor_hit(r.chunk_text, key, chunk_id=r.chunk_id)
                for r in slot for key in all_other_keys)
            for slot in slots
        )
        clean = float(not false_positive)
        return {"precision": clean, "recall": 1.0, "f1": clean}
    hit_keys: set[str] = set()
    slot_hit_flags = []
    for slot in slots:
        matched_any = False
        for key in gold_keys:
            if any(_anchor_hit(r.chunk_text, key, chunk_id=r.chunk_id) for r in slot):
                hit_keys.add(key)
                matched_any = True
        slot_hit_flags.append(matched_any)
    tp_retrieved = sum(slot_hit_flags)
    precision = tp_retrieved / len(slots) if slots else 0.0
    recall = len(hit_keys) / len(gold_keys)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def mrr_anchor(results: list, gold_keys: list[str]) -> float:
    if not gold_keys:
        return float("nan")
    slots = _group_results_into_slots(results)
    for rank, slot in enumerate(slots, start=1):
        if any(_anchor_hit(r.chunk_text, key, chunk_id=r.chunk_id) for r in slot for key in gold_keys):
            return 1.0 / rank
    return 0.0


def inspect_anchor_matches(chunks: list, sample_keys: list[str] | None = None) -> None:
    """Diagnostic helper for the caveat above: for each citation key (or a
    given sample), print which real chunks (if any) matched its anchor
    phrases, so you can eyeball whether the curated anchors are behaving
    before trusting real-corpus RQ1 numbers."""
    keys = sample_keys or list(ANCHOR_PHRASES.keys())
    for key in keys:
        matches = [c for c in chunks if _anchor_hit(c.chunk_text, key)]
        print(f"\n[{key}]  anchors={ANCHOR_PHRASES[key]}  -> {len(matches)} chunk(s) matched")
        for c in matches[:2]:
            preview = c.chunk_text[:160].replace("\n", " ")
            print(f"    {c.chunk_id}: {preview}...")


# ─────────────────────────────────────────────────────────────────────────
# 6. ChunkStore + retrieval pipeline — mirrors retrieve.py's algorithm
#    (hybrid BM25+dense -> RRF -> cross-encoder rerank -> guideline nudge
#    -> MMR diversification), with embedding/reranker model swappable for
#    RQ2, and chunking strategy swappable for RQ1 (only meaningful in the
#    real-PDF corpus mode — the stub corpus is already atomic per-claim
#    chunks, since it's built from citation-note-level facts, so RQ1's
#    "chunking strategy" ablation is only run in real-corpus mode; if you
#    are using the stub corpus we still run the full grid but flag this
#    limitation in RQ1's report cell).
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ChunkResult:
    chunk_id: str
    source: str
    source_name: str
    section: str
    chunk_text: str
    score: float
    # Set by retrieve_evidence_harness when a retrieval group (a
    # recommendation + its same-page supporting chunks) is expanded: every
    # member of the same group shares the same group_rank, meaning they
    # occupied exactly ONE position in top_k together. 0 (default) means
    # "ungrouped" -- e.g. the stub corpus, or the rare seed-chunk fallback --
    # and downstream metrics treat each such result as its own slot, exactly
    # as before this feature existed.
    group_rank: int = 0


def _build_retrieval_groups(chunks: list["Chunk"]) -> dict[str, str]:
    """Retrieval-time analogue of chunk.py's merge_page_context() -- groups
    chunks WITHOUT touching the underlying dataset or re-ingesting anything.
    Returns {chunk_id: group_key}; chunks sharing a group_key are returned
    together by retrieve_evidence_harness but occupy exactly ONE position
    in top_k.

    Same validated safety rule as the ingest-time version: a recommendation
    (all chunks sharing its recommendation_id) absorbs its page's
    un-anchored supporting/definitional chunks. If a page has more than one
    distinct recommendation, a supporting chunk only joins one of their
    groups when its `subsection` unambiguously matches exactly one of them;
    otherwise it stays its own singleton group. Chunks with no page/rec
    metadata (stub corpus, distractor seed chunks) each get a singleton
    group keyed to their own chunk_id, so they're never grouped with
    anything.
    """
    group_of: dict[str, str] = {c.chunk_id: c.chunk_id for c in chunks}

    by_page: dict[tuple[str, int], list["Chunk"]] = defaultdict(list)
    for c in chunks:
        if c.page is not None and c.page >= 0:
            by_page[(c.source, c.page)].append(c)

    for (source, page), page_chunks in by_page.items():
        rec_ids = {c.recommendation_id for c in page_chunks if c.recommendation_id}
        if not rec_ids:
            continue  # nothing on this page to anchor supporting text to

        anchor_key: dict[str, str] = {}
        for c in page_chunks:
            if c.recommendation_id:
                key = f"{source}_pg{page}_{c.recommendation_id}"
                anchor_key[c.recommendation_id] = key
                group_of[c.chunk_id] = key

        single_rec = len(rec_ids) == 1
        for c in page_chunks:
            if c.recommendation_id or c.chunk_type == "table":
                continue  # rec pieces already keyed above; tables never absorbed

            target_rid: str | None = None
            if single_rec:
                target_rid = next(iter(rec_ids))
            elif c.subsection:
                matches = {
                    rid for rid in rec_ids
                    if any(pc.recommendation_id == rid and pc.subsection == c.subsection
                           for pc in page_chunks)
                }
                if len(matches) == 1:
                    target_rid = next(iter(matches))

            if target_rid is not None:
                group_of[c.chunk_id] = anchor_key[target_rid]

    return group_of


class HarnessChunkStore:
    def __init__(self, chunks: list[Chunk], embed_fn: Callable[[list[str]], np.ndarray]):
        self.chunks = chunks
        self.embed_fn = embed_fn
        self._tokenized = [self._tokenize(c.chunk_text) for c in chunks]
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi(self._tokenized)
        texts = [c.chunk_text for c in chunks]
        vecs = embed_fn(texts)
        self.embeddings = np.asarray(vecs, dtype=np.float32)

        # Retrieval-time grouping (see _build_retrieval_groups docstring).
        # Precomputed once here rather than per-query since it only depends
        # on the corpus, not the query.
        self.chunk_group_key: dict[str, str] = _build_retrieval_groups(chunks)
        self.group_members: dict[str, list[Chunk]] = defaultdict(list)
        for c in chunks:
            self.group_members[self.chunk_group_key[c.chunk_id]].append(c)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9]+", text.lower())

    def semantic_search(self, query: str, top_k: int, allowed_idx=None) -> list[tuple[Chunk, float]]:
        qvec = np.asarray(self.embed_fn([query])[0], dtype=np.float32)
        emb_norm = self.embeddings / (np.linalg.norm(self.embeddings, axis=1, keepdims=True) + 1e-10)
        q_norm = qvec / (np.linalg.norm(qvec) + 1e-10)
        sims = emb_norm @ q_norm
        # BUGFIX 2026-08c: restrict the candidate universe BEFORE the top-k
        # cut, not after. Previously guideline_hard_filter only trimmed the
        # already-selected top-`retrieval_top_k` (default 10) results, but
        # that selection happens over the WHOLE 489-chunk corpus with zero
        # guideline awareness -- if fewer than top_k of THOSE 10 happened to
        # be on-guideline (observed in practice: WHO queries' top-10 BM25
        # hits were frequently 4/5 AAP chunks), the post-hoc filter had
        # nothing on-guideline left to return and fell back to padding with
        # off-guideline chunks -- reintroducing exactly the contamination
        # the filter was meant to remove. Masking out-of-scope indices
        # here means the top_k selection only ever happens among chunks
        # that were eligible in the first place.
        if allowed_idx is not None:
            mask = np.full(sims.shape, -np.inf, dtype=sims.dtype)
            mask[allowed_idx] = sims[allowed_idx]
            sims = mask
        top_idx = np.argsort(sims)[::-1][:top_k]
        top_idx = top_idx[np.isfinite(sims[top_idx])]
        return [(self.chunks[i], float(sims[i])) for i in top_idx]

    def bm25_search(self, query: str, top_k: int, allowed_idx=None) -> list[tuple[Chunk, float]]:
        tokens = self._tokenize(query)
        scores = self.bm25.get_scores(tokens)
        # BUGFIX 2026-08c: see semantic_search's docstring above -- same
        # pre-top-k masking, same reason.
        if allowed_idx is not None:
            mask = np.full(scores.shape, -np.inf, dtype=float)
            mask[allowed_idx] = scores[allowed_idx]
            scores = mask
        top_idx = np.argsort(scores)[::-1][:top_k]
        top_idx = top_idx[np.isfinite(scores[top_idx])]
        max_score = float(scores[top_idx[0]]) if len(top_idx) > 0 and scores[top_idx[0]] > 0 else 1.0
        return [(self.chunks[i], float(scores[i]) / max(max_score, 1e-10))
                for i in top_idx if scores[i] > 0]


# ─────────────────────────────────────────────────────────────────────────
# 8b. Streak-aware trends — mirrors patient_context_builder.py's
#    SustainedTrends, computed from a vignette's `prior_assessments` list
#    (only trend_deterioration vignettes carry this — the golden set's
#    T0/T1/T2 structure) instead of a live Supabase encounter history.
#    RESOLVED a real gap: query_builder.py's new version prefers
#    `context.query_context_terms()` (streak counts) over a single delta
#    row, but this harness previously only ever passed a flat `deltas` dict
#    (crp_delta/score_delta), so the "sustained trend" half of the new
#    query_builder.py logic was never actually exercised here.
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class SustainedTrendsHarness:
    assessment_count: int = 0
    crp_rising_streak: int = 0
    temp_unstable_streak: int = 0
    score_direction: str = "none"
    score_streak: int = 0
    aki_stage: int = 0

    def describe(self) -> list[str]:
        parts: list[str] = []
        if self.crp_rising_streak >= 2:
            parts.append(f"CRP rising {self.crp_rising_streak} consecutive readings")
        if self.temp_unstable_streak >= 2:
            parts.append(f"temperature instability {self.temp_unstable_streak} consecutive readings")
        if self.score_direction == "deteriorating" and self.score_streak >= 1:
            parts.append("deteriorating trend" if self.score_streak == 1 else
                          f"deteriorating trend {self.score_streak} consecutive assessments")
        elif self.score_direction == "improving" and self.score_streak >= 1:
            parts.append("improving trend")
        if self.aki_stage >= 1:
            parts.append(f"AKI stage {self.aki_stage}")
        if self.assessment_count >= 3:
            parts.append("multiple reassessments")
        return parts


def compute_sustained_trends_harness(prior_assessments: list[dict] | None) -> SustainedTrendsHarness:
    """Vignette-set equivalent of patient_context_builder.py's
    _compute_sustained_trends(). The golden set's `prior_assessments` are
    free-text `findings` strings (T0/T1/T2 narrative), not structured
    crp_level/combined_score numbers per timepoint, so exact streak
    arithmetic isn't reproducible from the vignette JSON as-authored —
    this returns `assessment_count` (always exactly computable) and leaves
    the numeric streak fields at 0 unless the vignette's `patient_context`
    itself carries explicit `crp_trend`/`clinical_status` fields (several
    trend_deterioration vignettes do, e.g. `crp_trend: "rising"` /
    `"falling"`), in which case a single-step streak (1) is recorded. This
    is a disclosed approximation, not a claim of full streak fidelity —
    the real streak arithmetic needs numeric per-timepoint values the
    vignette JSON doesn't carry."""
    trends = SustainedTrendsHarness()
    if not prior_assessments:
        return trends
    trends.assessment_count = len(prior_assessments) + 1  # + the "current" timepoint
    return trends


def build_clinical_query_harness(
    risk_payload: dict[str, Any],
    active_guideline: str,
    crp_delta_threshold: float = 2.0,
    deltas: dict | None = None,
    trends: "SustainedTrendsHarness | None" = None,
) -> str:
    """Adapted from the CURRENT query_builder.py (exhaustive-coverage
    rewrite) — same logic, settings passed as args instead of imported from
    config.py so this module has no backend import. Covers the full
    PatientSnapshot field set (maternal/peripartum, neonatal clinical
    signs, and labs), not just the original 6 threshold checks, and prefers
    streak-aware `trends.describe()` over a single delta row when a
    `SustainedTrendsHarness` is passed in — mirroring query_builder.py's own
    `context.query_context_terms()` vs. `deltas` fallback branch."""
    # PATCHED 2026-07-25: this used to unconditionally lead every query with
    # ["EOS management", "neonatal sepsis guidelines", "<CATEGORY> risk"].
    # Confirmed live (Colab, real MiniLM embeddings + real ms-marco
    # cross-encoder) that this generic prefix systematically outranks the
    # correct chunk: a query for "fast breathing, 2-day-old infant" scored
    # the terse WHO recommendation chunk ("Young infants...fast
    # breathing...oral amoxicillin") at -1.48 against a *generic AAP EOS
    # abstract* at +2.07 in a direct reranker head-to-head, purely because
    # the abstract's long prose repeats "EOS"/"management"/"sepsis"/"risk"/
    # "newborn" many times while the actual target recommendation (terse,
    # specific) barely uses that vocabulary at all. Stripping the prefix and
    # keeping only driver/clinical-sign terms flipped that same comparison
    # to +5.63 vs -9.79 and put both correct WHO chunks in the top 2 results
    # (precision 0.0->0.6, recall 0.0->1.0 on that vignette). The boilerplate
    # is now only used as a fallback when there is truly no clinical signal
    # to query on (e.g. a stratum with no positive findings at all) --
    # otherwise it's dropped in favor of leading with the specific terms.
    parts: list[str] = []
    category = risk_payload.get("category", risk_payload.get("risk_category", ""))
    guideline = active_guideline.upper()
    guideline_tag = {"NICE": "NICE NG195", "AAP": "AAP 2023 early-onset sepsis",
                      "WHO": "WHO newborn sepsis"}.get(guideline)
    if guideline_tag:
        parts.append(guideline_tag)

    # BUGFIX 2026-08: WHO severity-tier term, added unconditionally (not just
    # in the len(parts)<=1 fallback below) whenever the case category maps to
    # a known tier. Previously the query never distinguished "critical
    # illness" (A.2) from "clinical severe infection"/PSBI (A.3) -- both
    # tiers share driver vocabulary (GBS, ROM, fever, ampicillin, gentamicin,
    # referral), so retrieval had no signal to avoid a wrong-tier chunk.
    # This phrase is also how retrieve_evidence_harness/retrieve_evidence_
    # graph recover the case's tier from the query text (see
    # _who_tier_from_query) without changing the shared retrieve_fn
    # interface. Only emitted for WHO -- NICE/AAP don't have this tier split.
    if guideline == "WHO":
        who_tier = _CATEGORY_TO_WHO_TIER.get(str(category).upper())
        if who_tier == "critical":
            parts.append("critical illness")
        elif who_tier == "clinical_severe":
            parts.append("clinical severe infection possible serious bacterial infection PSBI")

        # BUGFIX 2026-08 (query vocabulary mismatch): everything else this
        # function emits (GBS, chorioamnionitis, IAP, CRP, WBC, PCT, Apgar,
        # etc.) is NICE/AAP hospital-NICU terminology, because it's derived
        # straight from the shared PatientSnapshot fields regardless of
        # active_guideline. WHO's PSBI/IMCI guideline describes the same
        # underlying clinical concepts in its own, largely non-overlapping
        # vocabulary (young infant 0-59 days, fast breathing, chest
        # indrawing, feeding/movement/consciousness signs, referral to
        # hospital) -- so a WHO case's query was almost entirely composed of
        # terms that don't lexically occur in the WHO source document, while
        # NICE/AAP cases' queries matched their own documents' phrasing
        # closely. This is a terminology TRANSLATION of clinical concepts
        # already present in risk_payload/patient, not new clinical facts,
        # and is unconditional for any WHO case (same pattern as the
        # `guideline_tag` append above, which is also always-on).
        # Empirically verified against the real corpus/dataset (offline,
        # BM25-only, guideline-masked, top_k=10, no LLM/embedder involved):
        # WHO gold-chunk recall in the retrieval CANDIDATE POOL went from
        # 0.000 (0/56) to 0.286 (16/56) with this addition alone -- see
        # test_bm25_who.py. Semantic/hybrid arms were not re-validated with
        # real embeddings in that offline check and should be re-run before
        # reporting numbers for those arms.
        parts.append(
            "possible serious bacterial infection PSBI young infant 0-59 days "
            "clinical severe infection fast breathing severe chest indrawing "
            "not feeding well movement only when stimulated convulsions "
            "danger signs IMCI referral hospital"
        )

    drivers = risk_payload.get("drivers", [])
    if isinstance(drivers, list):
        for driver in drivers:
            if isinstance(driver, dict):
                if driver.get("name"):
                    parts.append(str(driver["name"]))
                if driver.get("reason"):
                    parts.append(str(driver["reason"]))
            elif isinstance(driver, str):
                parts.append(driver)

    # ── Current-symptom terms — exhaustive, matching PatientSnapshot's full
    # field set (schemas.py), not just the original 6 threshold checks. ────
    patient = risk_payload.get("patient", {})
    if isinstance(patient, dict):
        # Maternal / peripartum
        if patient.get("respiratory_distress") not in (None, "None", "Normal", False):
            parts.extend(["respiratory distress", "tachypnoea", "grunting"])
        if (patient.get("rom_hours") or 0) >= 18:
            parts.extend(["prolonged ROM", "rupture of membranes"])
        if (patient.get("maternal_temperature") or 0) >= 38.0:
            parts.extend(["maternal fever", "intrapartum fever"])
        if patient.get("gbs_positive"):
            parts.extend(["GBS", "group B streptococcus", "intrapartum antibiotics"])
        if patient.get("clinical_chorioamnionitis"):
            parts.extend(["chorioamnionitis", "intra-amniotic infection"])
        if patient.get("adequate_intrapartum_antibiotics") is False and patient.get("gbs_positive"):
            parts.extend(["inadequate prophylaxis", "IAP"])
        if patient.get("delivery_mode") == "Caesarean":
            parts.append("caesarean delivery")

        # Neonatal clinical signs
        if patient.get("oxygen_need") not in (None, "None"):
            parts.extend(["oxygen requirement", "respiratory support", "CPAP"])
        apgar = patient.get("apgar_5_min")
        if apgar is not None and apgar <= 6:
            parts.extend(["low Apgar score", "resuscitation"])
        if patient.get("poor_perfusion"):
            parts.extend(["poor perfusion", "shock", "capillary refill"])
        neuro = patient.get("neurological_status")
        if neuro not in (None, "Normal"):
            parts.extend(["lethargy", "irritability", "neurological status", str(neuro)])
        neo_temp = patient.get("neonatal_temperature")
        if neo_temp is not None:
            if float(neo_temp) < 36.0:
                parts.extend(["hypothermia", "temperature instability"])
            elif float(neo_temp) > 38.0:
                parts.extend(["hyperthermia", "neonatal fever"])
        ga = patient.get("gestational_age_weeks")
        if ga is not None and float(ga) < 37.0:
            parts.extend(["preterm", "prematurity"])

        # Labs — previously only CRP/blood culture were checked at all.
        if patient.get("crp_level") and float(patient["crp_level"]) >= 10:
            parts.extend(["CRP", "inflammatory markers"])
        if patient.get("blood_culture_positive"):
            parts.extend(["blood culture", "bacteremia", "empirical antibiotics"])
        if patient.get("wbc_count") is not None:
            parts.extend(["white blood cell count", "WBC"])
        it_ratio = patient.get("it_ratio")
        if it_ratio is not None and float(it_ratio) >= 0.2:
            parts.extend(["I:T ratio", "immature neutrophil ratio"])
        if patient.get("pct_level") is not None:
            parts.extend(["procalcitonin", "PCT"])
        platelets = patient.get("platelet_count")
        if platelets is not None and float(platelets) < 150000:
            parts.extend(["thrombocytopenia", "platelet count"])
        if patient.get("penicillin_allergy"):
            parts.extend(["penicillin allergy", "alternative antibiotics"])
        if patient.get("birth_weight_g") and float(patient["birth_weight_g"]) < 1500:
            parts.extend(["low birth weight", "LBW"])

    layer1 = risk_payload.get("layer1_score", 0)
    layer2 = risk_payload.get("layer2_score", 0)
    layer3 = risk_payload.get("layer3_score", 0)
    if layer2 and int(layer2) >= 3:
        parts.append("clinical illness")
    if layer3 and int(layer3) >= 2:
        parts.append("laboratory evidence sepsis")
    total = risk_payload.get("total_score", risk_payload.get("combined_score", 0))
    if total and int(total) >= 7:
        parts.extend(["blood culture", "empirical antibiotics", "senior review"])

    # ── Trend terms — prefer streak-aware trends.describe() when available,
    # fall back to the single-delta-row check otherwise (matches
    # query_builder.py's context-vs-deltas branch). ─────────────────────────
    if trends is not None:
        parts.extend(trends.describe())
    elif deltas:
        crp_delta = deltas.get("crp_delta")
        if crp_delta is not None and float(crp_delta) > 0:
            if float(crp_delta) >= crp_delta_threshold:
                parts.extend(["rising CRP", "worsening inflammatory markers"])
            else:
                parts.extend(["CRP trend", "serial CRP monitoring"])
        score_delta = deltas.get("score_delta")
        if score_delta is not None:
            if float(score_delta) > 0:
                parts.extend(["deteriorating", "escalating risk"])
            elif float(score_delta) < 0:
                parts.extend(["improving", "resolving"])
        aki_stage = deltas.get("aki_stage")
        if aki_stage and int(aki_stage) >= 1:
            parts.append(f"AKI stage {int(aki_stage)}")

    # If nothing specific ever got added beyond (optionally) the guideline
    # tag, there's no discriminating signal to lead with -- fall back to the
    # original generic framing so the query isn't just "WHO newborn sepsis"
    # or empty. This preserves prior behavior for genuinely sign-free cases
    # (e.g. some negative_distractor vignettes) without letting it drown out
    # real signal when there is any.
    if len(parts) <= 1:
        category_term = f"{str(category).upper()} risk" if category else None
        parts = ["EOS management", "neonatal sepsis guidelines"] + ([category_term] if category_term else []) + parts

    return " ".join(dict.fromkeys(p.strip() for p in parts if p.strip()))


# ─────────────────────────────────────────────────────────────────────────
# 6b. Metadata-aware reranking helpers (Priority 1 improvements)
#
# These three functions address the evaluation's top-ranked failure mode:
# the cross-encoder only sees (query, chunk_text) and is blind to whether
# a chunk is a *recommendation* or *background*, and whether its age band
# matches the query's. Combined they form the FinalScore formula:
#
#   FinalScore = 0.80 * CrossEncoder
#              + 0.10 * SectionWeight(chunk_type)
#              + 0.05 * AgeMatch(query, chunk_text)
#              + 0.05 * GuidelineBoost   ← kept from original nudge
#
# The weights replicate the formula in the attached suggestion doc.
# Section weights are applied as absolute boosts (not percentages) because
# the cross-encoder outputs are not bounded [0,1], so multiplicative
# scaling would skew toward high-score chunks regardless of section type.
# ─────────────────────────────────────────────────────────────────────────

# Intent keyword patterns (treatment queries should prefer recommendation
# chunks; definition queries can tolerate background/overview chunks).
_INTENT_PATTERNS: dict[str, list[str]] = {
    "treatment": [
        "antibiotic", "treat", "regimen", "dose", "dosing", "ampicillin",
        "gentamicin", "benzylpenicillin", "amoxicillin", "cloxacillin",
        "give", "administer", "prescrib", "start", "initiat", "manage",
        "recommend", "should", "what to do", "how to treat",
    ],
    "diagnosis": [
        "diagnos", "is this", "is it", "criteria", "confirm", "blood culture",
        "identify", "detect", "screen",
    ],
    "risk_assessment": [
        "risk factor", "at risk", "risk of", "likelihood", "probability",
        "prom", "gbs", "chorioamnionitis", "rom", "rupture of membrane",
        "iap", "intrapartum",
    ],
    "monitoring": [
        "monitor", "follow up", "follow-up", "review", "check", "reassess",
        "serial", "watch", "crp", "repeat", "interval",
    ],
    "definition": [
        "what is", "define", "definition", "explain", "overview",
        "background", "epidemiology",
    ],
}

# Section type -> absolute score adjustment (applied to the 0.10 * SectionWeight term)
_SECTION_SCORE: dict[str, float] = {
    "recommendation": +0.15,
    "algorithm":      +0.12,
    "risk_factor":    +0.08,
    "table":          +0.05,
    "remarks":        -0.02,
    "background":     -0.05,
    "overview":       -0.08,
}

# Per-intent multipliers for each section type
# (how much to scale the section weight when intent matches)
_INTENT_SECTION_MULT: dict[str, dict[str, float]] = {
    "treatment":       {"recommendation": 1.20, "algorithm": 1.10,
                        "background": 0.90, "overview": 0.80, "remarks": 0.95},
    "diagnosis":       {"recommendation": 1.10, "background": 1.00,
                        "overview": 0.95},
    "risk_assessment": {"risk_factor": 1.20, "background": 1.05,
                        "recommendation": 1.10},
    "monitoring":      {"recommendation": 1.10, "remarks": 1.05,
                        "background": 0.95},
    "definition":      {"background": 1.10, "overview": 1.05,
                        "recommendation": 0.90},
}

# Age band phrases (0-6 days) — for direct matching in queries + chunks
_AGE_BAND_0_6: frozenset[str] = frozenset([
    "0-6 day", "0 to 6 day", "day 0", "day 1", "day 2", "day 3",
    "day 4", "day 5", "day 6", "first day", "first week",
    "newborn", "neonate", "age < 7",
])
_AGE_BAND_7_59: frozenset[str] = frozenset([
    "7-59 day", "7 to 59 day", "week 1", "week 2", "week 3",
    "day 7", "day 14", "day 21", "day 28", "young infant",
    "1 month", "2 month", "1-2 month",
])


def _detect_query_intent(query: str) -> str:
    """Return the dominant clinical intent of *query* (one of 'treatment',
    'diagnosis', 'risk_assessment', 'monitoring', 'definition', 'other').
    Scores by keyword overlap, returning the highest-scoring category;
    ties go to 'other'. Intentionally cheap — pure string matching,
    no external model, runs in microseconds per query."""
    q_low = query.lower()
    best_label, best_count = "other", 0
    for label, patterns in _INTENT_PATTERNS.items():
        count = sum(1 for p in patterns if p in q_low)
        if count > best_count:
            best_count, best_label = count, label
    return best_label


_BASELINE_CHUNK_PATTERNS = (
    # Mirrors graph_care_plan_retrieval.py's _BASELINE_SECTION_TYPES
    # (WhenToStartAntibiotics, FirstLineAntibiotics, StoppingCriteria,
    # Monitoring, ParentCommunication, NutritionFluid) -- that mechanism
    # keys off a recommendation NODE's section_type, which vector_fn has no
    # equivalent of at chunk granularity (chunks_ingested.csv has no
    # section_type/recommendation_id link for most table-type chunks, e.g.
    # NICE_pg8_chorioamnionitis_chunk1 / NICE_pg9_chorioamnionitis_chunk5 --
    # both NICE_REC_002/FirstLineAntibiotics content -- carry rec_id=NaN at
    # the chunk level). This identifies the same *kind* of always-relevant
    # "what do I actually do" content via the chunk's own text instead.
    #
    # Real, confirmed failure mode this targets: NICE_pg8_chorioamnionitis_
    # chunk1 is gold for 8/9 no-penicillin-allergy NICE cases and was never
    # retrieved by any arm in either the MiniLM or BGE production run
    # (retrieval_only_metrics, both runs) -- its content is a short,
    # generically-relevant baseline answer that longer, more specific
    # chunks keep outranking under raw cross-encoder + BM25 scoring.
    "benzylpenicillin", "first-line antibiot", "first line antibiot", "gentamicin plus",
    "stop antibiotic", "stopping criteri", "discontinue antibiot", "duration of antibiot",
    "monitor for", "monitoring should", "trough level", "peak concentration",

    # AAP/AAP_PRETERM equivalents, added this round -- this list previously
    # had the *same* guideline-vocabulary blind spot already found and fixed
    # twice elsewhere in this file (build_clinical_query_harness's WHO
    # block, graph_care_plan_retrieval.py's _SECTION_TYPE_KEYWORDS): 100%
    # NICE phrasing, zero AAP/WHO terms. Checked chunks_ingested.csv
    # directly -- every phrase below is copied verbatim from real AAP/
    # AAP_PRETERM chunk text, not invented:
    #   AAP_pg6_treatment-of-eos_chunk2 / AAP_pg6_summary-points_chunk3 --
    #     "ampicillin and gentamicin" / "combination of ampicillin and
    #     gentamicin is the appropriate empirical antibiotic regimen"
    #   AAP_pg3_categorical-risk-factor-_chunk1/2 -- "categorical risk
    #     factor assessment"
    #   AAP_pg3_multivariate-risk-assess_chunk1-5 -- "multivariate risk
    #     assessment" / "neonatal early-onset sepsis risk calculator"
    # These are the exact recommendation-equivalent "what do I actually do"
    # content for AAP that graph_care_plan_retrieval.py's coverage-pass fix
    # already targets via _BASELINE_SECTION_TYPES (WhenToStartAntibiotics /
    # FirstLineAntibiotics) -- this brings vector_fn's force-include
    # mechanism to parity for the same guideline.
    "ampicillin and gentamicin", "ampicillin plus gentamicin", "categorical risk factor",
    "multivariate risk assessment", "risk calculator", "empirical antibiotic regimen",
    "clinical signs of illness", "serial examination",

    # WHO/PSBI equivalents, added this round -- same rationale, verified
    # against real WHO chunk text (e.g. WHO_pg2_background-and-definitio_
    # chunk1, WHO_pg15_recommendation-a-4-updat_rec1_chunk1,
    # WHO_pg10_background-and-definitio_chunk1):
    "possible serious bacterial infection", "fast breathing", "chest indrawing",
    "clinical severe infection", "danger sign",

    # Round 4 (this session): found via *production* CSV analysis this
    # time, not offline reproduction -- the person's real Colab run
    # (retrieval_only_metrics__9_.csv) has actual retrieved_chunk_ids per
    # case, so these misses are confirmed against the real cross-encoder
    # output, not a local approximation. Cross-referenced the most
    # frequently-missed anchor phrases across dense_only/vector/sparse_only/
    # hybrid against chunks_ingested.csv directly; every pattern below
    # matches 1-12 real chunks (checked, not assumed) and is copied
    # verbatim from actual chunk text:
    #   NICE_pg14_including-red-flags_chunk3 -- "routine postnatal care" /
    #     "without risk factors" -- the single chunk answering "no risk
    #     factors -> what do I do", confirmed retrieved by graph_fn's
    #     coverage-pass fix but by NO other arm across all of cp_case_017/
    #     029 (both scored anchor_grounding_recall=0.0 on every non-graph
    #     arm in the production run) -- highest-impact single addition here.
    #   NICE_pg26_infection_rec1_chunk1 -- "36 hours after starting
    #     antibiotics" (the actual guideline wording; the anchor phrase
    #     "stop antibiotics at 36 hours" is a paraphrase of this, not a
    #     verbatim quote anywhere in the corpus)
    #   NICE_pg16(ish) CRP-alone stopping-criteria chunk -- "crp results
    #     alone"
    #   NICE AlternativeAntibiotics content (penicillin-allergy pathway) --
    #     "penicillin allergy" / "cephalosporin" / "vancomycin"
    #   WHO_pg44_risk-groups_chunk1 -- "risk groups"
    # NOT added: "referral not possible" and "relapse" -- checked directly,
    # "referral not possible" has zero verbatim matches anywhere in the
    # corpus (the anchor phrase is a paraphrase of WHO_REC_003/004/006's
    # summarized text, not chunk text -- a real ceiling, not a retrieval
    # bug) and "relapse" matches 17 chunks (3.5% of corpus), mostly
    # evidence-review paragraphs rather than the actual recommendation --
    # too broad to add without diluting the mechanism, same judgment call
    # that dropped "young infant" last round.
    "routine postnatal care", "without risk factors", "36 hours after starting antibiotics",
    "crp results alone", "penicillin allergy", "cephalosporin", "vancomycin", "risk groups",

    # Round 5 (this session): diagnosed against retrieval_only_metrics__10_.csv,
    # the person's second real production run with per-case retrieved_chunk_ids.
    # Progress confirmed: cp_case_017/029 moved from 0.0 (Round 3 CSV) to 0.25
    # (this CSV) after the "routine postnatal care"/"without risk factors" fix
    # -- "no risk factors" itself is still missed and stays that way (annotation
    # wording ceiling, documented above, not fixable here). Checked corpus
    # directly for the next round of frequent misses:
    #   AAP_PRETERM_pg6_summary-points_chunk1 -- "low risk" (1 match, real
    #     risk-categorization content, not background epidemiology)
    #   NICE_pg64_why-the-committee-made-t_chunk1/3/4 -- "rupture of membranes
    #     at term" (1 match) / "maternal sepsis" (5 matches) -- both genuinely
    #     the "why this recommendation exists" rationale text the anchors ask
    #     about, not generic filler
    # NOT added, same over-broad judgment call as "young infant"/"relapse"
    # before: "risk stratification" (16 matches, almost all GDG-remarks/PICO
    # evidence-review paragraphs) and "treatment failure" (36 matches, 7.4% of
    # the whole corpus, overwhelmingly generic background text) -- forcing
    # these in would dilute the mechanism far more than it would help.
    # "well-baby"/"well baby" also checked and confirmed: zero matches
    # anywhere in the corpus, another annotation-wording ceiling like
    # "referral not possible".
    "low risk", "maternal sepsis", "rupture of membranes at term",
)


def _is_baseline_treatment_chunk(chunk_text: str, section: str = "") -> bool:
    t = (chunk_text or "").lower()
    return any(p in t for p in _BASELINE_CHUNK_PATTERNS)


# BUGFIX 2026-08i: same tuned value as graph_care_plan_retrieval.py's
# _COVERAGE_ROUNDS (swept 1/2/3/4 there before settling on 2). Reused here
# rather than re-derived, since the empirical shape of the trade-off (more
# rounds displaces non-baseline content for no further gain) is a property
# of shared top_k, not something specific to graph's mechanism.
_COVERAGE_ROUNDS = 2


def _section_weight(chunk_type: str, intent: str) -> float:
    """Compute the section-type bonus/malus for one chunk, scaled by
    query intent.  Returns a value in roughly [-0.08, +0.18]; the caller
    multiplies by 0.10 before adding to the final score."""
    ct = (chunk_type or "").lower().strip()
    # Normalise chunk_type strings that don't exactly match the keys
    for key in _SECTION_SCORE:
        if key in ct:
            ct = key
            break
    base = _SECTION_SCORE.get(ct, 0.0)
    mult = _INTENT_SECTION_MULT.get(intent, {}).get(ct, 1.0)
    return base * mult


def _age_band_match(query: str, chunk_text: str) -> float:
    """Return +0.12 when query and chunk share the same age band, -0.04
    when they belong to *different* age bands (a retrieval error the
    evaluation flagged as clinically dangerous), and 0.0 when the query
    carries no age signal.  The caller multiplies by 0.05."""
    q_low = query.lower()
    c_low = chunk_text.lower()

    q_has_0_6  = any(p in q_low for p in _AGE_BAND_0_6)
    q_has_7_59 = any(p in q_low for p in _AGE_BAND_7_59)

    if not q_has_0_6 and not q_has_7_59:
        return 0.0   # no age signal in query — no boost/penalty

    c_has_0_6  = any(p in c_low for p in _AGE_BAND_0_6)
    c_has_7_59 = any(p in c_low for p in _AGE_BAND_7_59)

    if q_has_0_6 and c_has_0_6 and not c_has_7_59:
        return +0.12
    if q_has_7_59 and c_has_7_59 and not c_has_0_6:
        return +0.12
    if (q_has_0_6 and c_has_7_59 and not c_has_0_6) or \
       (q_has_7_59 and c_has_0_6 and not c_has_7_59):
        return -0.04  # wrong age band — penalise
    return 0.0


def _adaptive_retrieval_top_k(query: str, intent: str, base_top_k: int) -> int:
    """Scale retrieval_top_k based on query complexity so the reranker has
    more candidates on hard queries (multi-hop, cross-guideline) and fewer
    on simple ones, keeping latency reasonable.

    Simple (single-sign) query  → base_top_k  (typically 10)
    Treatment / risk query       → base_top_k * 1.5 (≈15)
    Decomposed / multi-signal    → base_top_k * 2   (≈20)
    Cross-guideline / negative   → base_top_k * 2.5 (≈25)
    """
    q_low = query.lower()
    cross_terms = ["who", "aap", "nice", "compare", "versus", "vs", "both guidelines"]
    multi_terms = ["and", "also", "plus", "as well", "additionally", "combination"]

    n_cross = sum(1 for t in cross_terms if t in q_low)
    n_multi = sum(1 for t in multi_terms if t in q_low)

    if n_cross >= 2:
        return int(base_top_k * 2.5)
    if n_multi >= 2 or intent in ("risk_assessment", "monitoring"):
        return int(base_top_k * 2.0)
    if intent == "treatment":
        return int(base_top_k * 1.5)
    return base_top_k


def _mmr_select(
    pool: list[tuple[Chunk, float]],
    k: int,
    lambda_mult: float = 0.7,
    embed_map: dict[str, np.ndarray] | None = None,
) -> list[tuple[Chunk, float]]:
    """Maximum Marginal Relevance diversification.

    Improvement over the original: when *embed_map* is provided (a dict
    from chunk_id → normalised embedding vector, built cheaply from
    HarnessChunkStore.embeddings), the redundancy term uses **cosine
    similarity** between chunk embeddings instead of Jaccard token overlap.
    Cosine similarity handles synonymous clinical phrases (e.g. "IV
    ampicillin" vs "ampicillin IM/IV") that Jaccard treats as dissimilar
    even though they convey the same fact — exactly the failure mode
    identified in the evaluation (Priority 2, item 4 in the suggestions).
    Falls back to Jaccard when embed_map is absent (stub corpus, test runs).
    """
    if not pool:
        return []
    if len(pool) <= k:
        return pool

    # ── Similarity function: cosine (preferred) or Jaccard (fallback) ──
    if embed_map is not None:
        def _sim(a: Chunk, b: Chunk) -> float:
            va = embed_map.get(a.chunk_id)
            vb = embed_map.get(b.chunk_id)
            if va is None or vb is None:
                return 0.0
            return float(np.dot(va, vb))  # embeddings are pre-normalised
    else:
        def _tokens(c: Chunk) -> set[str]:
            return set(re.findall(r"[a-z0-9]{4,}", c.chunk_text.lower()))

        token_cache: dict[int, set[str]] = {}

        def _sim(a: Chunk, b: Chunk) -> float:
            ta = token_cache.setdefault(id(a), _tokens(a))
            tb = token_cache.setdefault(id(b), _tokens(b))
            if not ta or not tb:
                return 0.0
            return len(ta & tb) / len(ta | tb)

    remaining = list(pool)
    scores = [s for _, s in remaining]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0

    def norm(s: float) -> float:
        return (s - lo) / span

    selected = [remaining.pop(0)]
    while remaining and len(selected) < k:
        best_idx, best_val = 0, float("-inf")
        for i, (chunk, score) in enumerate(remaining):
            max_sim = max((_sim(chunk, sel) for sel, _ in selected), default=0.0)
            mmr_val = lambda_mult * norm(score) - (1 - lambda_mult) * max_sim
            if mmr_val > best_val:
                best_val, best_idx = mmr_val, i
        selected.append(remaining.pop(best_idx))
    return selected


def _fallback_chunks_harness(query: str, active_guideline: str, limit: int) -> list[ChunkResult]:
    """DISCLOSED STAND-IN for retrieve.py's `_fallback_chunks()`, which in
    production scores against `rag/seed_chunks.py` -- a curated, real
    "high-quality seed chunks" module I don't have the contents of (it
    wasn't part of any upload). The MECHANISM here is faithful (keyword
    overlap + guideline-match scoring, same shape as production's version,
    same trigger conditions: empty store, RRF returns nothing, embed model
    unavailable), but the actual seed CONTENT is this harness's own small
    `_DISTRACTOR_CHUNKS` list, not your real seed_chunks.py. If you want
    exact fidelity on this specific (rarely-hit) fallback path, send me
    rag/seed_chunks.py and I'll swap this to score against the real set."""
    query_lower = query.lower()
    scored: list[tuple[Chunk, float]] = []
    seed_source = [
        Chunk(chunk_id=cid, source=src, source_name=_SOURCE_NAME.get(src, src), section=cid, chunk_text=txt)
        for cid, src, txt in _DISTRACTOR_CHUNKS
    ]
    for chunk in seed_source:
        score = 0.0
        text_lower = chunk.chunk_text.lower()
        for word in query_lower.split():
            if len(word) > 3 and word in text_lower:
                score += 1.0
        if chunk.source.upper() == active_guideline.upper():
            score += 2.0
        scored.append((chunk, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit] if scored else [(c, 0.5) for c in seed_source[:limit]]
    return [ChunkResult(c.chunk_id, c.source, c.source_name, c.section, c.chunk_text,
                         round(min(sc / max(10, 1), 1.0), 4))
            for c, sc in top]


def _expand_groups(store: "HarnessChunkStore", selected: list[tuple["Chunk", float]]) -> list[ChunkResult]:
    """Turn a list of (representative chunk, score) -- one per chosen
    retrieval group, already in rank order -- into the full ChunkResult
    list: every member of a chosen group is returned, tagged with the same
    group_rank, so the group as a whole still counts as exactly one of the
    top_k positions (see ChunkResult.group_rank / precision_recall_f1_anchor
    / mrr_anchor)."""
    out: list[ChunkResult] = []
    for rank, (chunk, score) in enumerate(selected, start=1):
        gk = store.chunk_group_key.get(chunk.chunk_id, chunk.chunk_id)
        members = store.group_members.get(gk) or [chunk]
        for m in members:
            out.append(ChunkResult(m.chunk_id, m.source, m.source_name, m.section,
                                    m.chunk_text, score, group_rank=rank))
    return out


def _context_expand_siblings(
    results: list[ChunkResult],
    store: "HarnessChunkStore",
    intent: str,
    max_siblings: int = 2,
) -> list[ChunkResult]:
    """Context expansion: after final selection, append sibling chunks
    (chunks on the same page and recommendation_id as a selected
    recommendation chunk) that were NOT already included.

    This addresses Priority 1 issue #1 (recommendations buried below
    background) from a different angle: even when a recommendation IS
    retrieved, its immediately adjacent remarks/dosing notes often contain
    the practical detail an LLM needs to answer "what antibiotic / what
    dose". Appending up to `max_siblings` of these as un-ranked context
    (group_rank=0, so they don't count against precision/recall slots)
    gives the LLM more usable material without inflating the retrieval k.

    Per the suggestion doc (item 5): siblings are ONLY APPENDED — never
    reranked — so they cannot push the actual recommendations down the
    context window. Only applies when intent is 'treatment' or
    'monitoring'; skipped for risk_assessment / definition / diagnosis
    where additional sibling remarks are usually noise.
    """
    if intent not in ("treatment", "monitoring"):
        return results

    included_ids = {r.chunk_id for r in results}
    siblings_to_add: list[ChunkResult] = []
    seen_sibling_ids: set[str] = set()

    for result in results:
        # BUGFIX: this used to read `result.chunk_type`, but ChunkResult (the
        # type of `result` here) has no chunk_type field -- only the
        # underlying Chunk does -- so this raised AttributeError on every
        # "treatment"/"monitoring"-intent query, i.e. most real clinical
        # questions, silently taking down the whole RAG pipeline before any
        # answer could be generated. The intended check was a no-op filter
        # anyway (see original comment: "still try"), so it's removed rather
        # than reimplemented against a store lookup that would be redundant
        # with the `orig` lookup performed just below.

        # Find same-(source, page, recommendation_id) siblings in the store
        for candidate in store.chunks:
            if candidate.chunk_id in included_ids:
                continue
            if candidate.chunk_id in seen_sibling_ids:
                continue
            # Must share source + page (page >= 0 to exclude stub corpus)
            if candidate.source != result.source or candidate.page < 0:
                continue
            if candidate.page != getattr(result, "_page", -1):
                # result is a ChunkResult; look up its Chunk for page info
                orig = next((c for c in store.chunks if c.chunk_id == result.chunk_id), None)
                if orig is None or orig.page < 0 or orig.page != candidate.page:
                    continue
            # Must share recommendation_id (or at least subsection) with result
            orig = next((c for c in store.chunks if c.chunk_id == result.chunk_id), None)
            if orig is None:
                continue
            if orig.recommendation_id and candidate.recommendation_id == orig.recommendation_id:
                seen_sibling_ids.add(candidate.chunk_id)
                siblings_to_add.append(ChunkResult(
                    candidate.chunk_id, candidate.source, candidate.source_name,
                    candidate.section, candidate.chunk_text,
                    score=result.score * 0.8,  # slightly lower score to preserve order
                    group_rank=0,  # group_rank=0 → not counted as a retrieval slot
                ))
                if len(siblings_to_add) >= max_siblings:
                    break
        if len(siblings_to_add) >= max_siblings:
            break

    return results + siblings_to_add


def retrieve_evidence_harness(
    store: HarnessChunkStore,
    query: str,
    active_guideline: str,
    reranker,
    top_k: int = 2,
    retrieval_top_k: int = 10,
    rrf_k: int = 60,
    guideline_nudge_weight: float = 0.05,   # reduced: now part of 5% GuidelineBoost term
    use_mmr: bool = True,
    semantic_query: str | None = None,
    use_metadata_reranking: bool = True,
    use_context_expansion: bool = True,
    adaptive_top_k: bool = True,
    use_sparse: bool = True,
    use_dense: bool = True,
    guideline_hard_filter: bool = True,
) -> list[ChunkResult]:
    """Mirrors retrieve.py's retrieve_evidence() end-to-end: hybrid search ->
    RRF -> dedupe -> cross-encoder rerank (graceful degrade on failure) ->
    metadata-aware final scoring -> MMR -> context expansion ->
    (empty-candidates fallback).

    Improvements over the original (all additive, all togglable):

    `use_metadata_reranking` (default True): replaces the single-term
    guideline nudge with the compound FinalScore formula:
        FinalScore = 0.80 * CrossEncoder
                   + 0.10 * SectionWeight(chunk_type, intent)
                   + 0.05 * AgeMatch(query, chunk_text)
                   + 0.05 * GuidelineBoost
    This directly addresses Priority 1 items 1–3 (recommendation chunks
    ranked below background; age-specific retrieval errors; intent-blind
    reranking). The cross-encoder weight is deliberately kept at 0.80 so
    semantic relevance still dominates, with the metadata terms acting as
    tie-breakers and edge-case correctors — not overrides.

    `use_context_expansion` (default True): after final selection,
    appends sibling chunks of selected recommendation chunks as extra
    context (group_rank=0 so they don't affect precision/recall scoring).
    Addresses Priority 1 item #1 from a complementary angle.

    `adaptive_top_k` (default True): scales retrieval_top_k up for
    complex / cross-guideline queries so the reranker has more candidates.

    `guideline_hard_filter` (default True, BUGFIX 2026-08a/c): masks
    semantic_search/bm25_search themselves to the active_guideline's own
    chunks (via _guideline_source_match) BEFORE either leg's top-k cut, not
    just after -- see HarnessChunkStore.semantic_search's docstring for why
    filtering only the already-selected top-`retrieval_top_k` candidates
    (the original 2026-08a fix) wasn't enough: on this dataset's WHO
    queries, that small pre-filter pool was frequently dominated by
    off-guideline chunks (e.g. AAP), leaving nothing on-guideline to
    return and forcing a fallback that reintroduced the contamination.
    Falls back to the unfiltered corpus only if active_guideline has zero
    matching chunks at all. Also fixes the boost/conflict-detection match
    itself from a substring test to an exact allow-listed one (see
    _guideline_source_match). Set False to reproduce the original
    soft-nudge-only behavior for ablation comparisons.

    WHO severity-tier scoring (always on, no toggle -- see
    who_severity_tier_score / BUGFIX 2026-08 comment above
    _GUIDELINE_ALLOWED_SOURCES): a +/-1.0 term (weighted like GuidelineBoost)
    that rewards a WHO chunk whose self-identified severity tier (critical
    illness / A.2 vs. clinical severe infection-PSBI / A.3) matches the
    case's category-derived tier, and penalizes a WHO chunk that
    self-identifies as the OTHER tier. Fixes a systematic tier swap found
    on this dataset (CRITICAL cases retrieving the A.3 chunk and vice
    versa, 0% gold overlap on every WHO case checked).

    `semantic_query`: HyDE support (unchanged from original) — when set,
    the SEMANTIC leg embeds `semantic_query` (a hypothetical guideline
    passage) while BM25 still uses the original expanded query.

    `use_sparse` / `use_dense` (both default True): retrieval-stack ablation
    switches. Set exactly one to False to get a sparse-only (BM25Okapi) or
    dense-only (semantic/FAISS) retriever with the rest of the pipeline
    (rerank, metadata scoring, MMR, context expansion) held fixed -- mirrors
    Table 4's I/S/D/H/L ablation design in the ADRE paper. Setting both
    False raises ValueError (nothing to retrieve from).
    """
    if not use_sparse and not use_dense:
        raise ValueError("retrieve_evidence_harness: use_sparse and use_dense cannot both be False")
    # ── Detect intent early — used in section weighting, context expansion,
    # and adaptive k (all three care about what kind of query this is). ────
    intent = _detect_query_intent(query)

    # ── Adaptive retrieval_top_k ──────────────────────────────────────────
    if adaptive_top_k:
        retrieval_top_k = _adaptive_retrieval_top_k(query, intent, retrieval_top_k)

    bm25_query = expand_query(query)
    semantic_search_text = semantic_query if semantic_query else query

    # BUGFIX 2026-08c: compute the on-guideline index mask ONCE, before
    # either retrieval leg runs, and pass it into semantic_search/
    # bm25_search so the top-`retrieval_top_k` cut happens only among
    # eligible candidates -- see those methods' docstrings for why doing
    # this after the cut (the previous approach) didn't work. `None` when
    # guideline_hard_filter=False reproduces the original unfiltered
    # behavior exactly (used by the ablation ladder to isolate this fix's
    # effect).
    guideline_allowed_idx = None
    if guideline_hard_filter:
        guideline_allowed_idx = [i for i, c in enumerate(store.chunks)
                                  if _guideline_source_match(active_guideline, c.source)]
        if not guideline_allowed_idx:
            # No chunk in the whole corpus matches this active_guideline --
            # nothing to gain from filtering, fall back to the full corpus
            # rather than returning nothing.
            guideline_allowed_idx = None

    # Retrieval-stack ablation: skip a leg entirely (rather than fusing an
    # empty list, which reciprocal_rank_fusion also handles fine, but
    # short-circuiting avoids a wasted encoder/BM25 call when that leg is
    # disabled for the whole ablation run).
    semantic_hits = store.semantic_search(semantic_search_text, top_k=retrieval_top_k,
                                           allowed_idx=guideline_allowed_idx) if use_dense else []
    bm25_hits = store.bm25_search(bm25_query, top_k=retrieval_top_k,
                                   allowed_idx=guideline_allowed_idx) if use_sparse else []

    def id_fn(c: Chunk) -> str:
        return c.chunk_id

    if use_sparse and use_dense:
        fusion_lists = [[c for c, _ in semantic_hits], [c for c, _ in bm25_hits]]
    elif use_dense:
        fusion_lists = [[c for c, _ in semantic_hits]]
    else:
        fusion_lists = [[c for c, _ in bm25_hits]]

    merged = reciprocal_rank_fusion(
        fusion_lists,
        k=rrf_k, id_fn=id_fn,
    )[:retrieval_top_k]

    if not merged:
        print("[Retrieval] RRF returned nothing — seed fallback (see _fallback_chunks_harness docstring)")
        return _fallback_chunks_harness(query, active_guideline, top_k)

    seen, candidates = set(), []
    for chunk, _ in merged:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            candidates.append(chunk)

    # BUGFIX 2026-08f: force-include same-guideline baseline-treatment chunks
    # (see _is_baseline_treatment_chunk) that RRF didn't surface, mirroring
    # graph_care_plan_retrieval.py's is_baseline candidacy bypass (a
    # _BASELINE_SECTION_TYPES node is a candidate regardless of entity/
    # section match -- see retrieve_evidence_graph). The 0.05-weighted
    # baseline_bonus added to FinalScore below only helps a chunk that's
    # ALREADY in `candidates` -- it does nothing if the chunk never enters
    # the pool in the first place, which is exactly what was happening here:
    # confirmed NICE_pg8_chorioamnionitis_chunk1 (gold for 8/9 no-allergy
    # NICE cases) absent from the BM25 candidate pool even at top_k=50, and
    # multiple query-term-augmentation attempts at top_k<=40 all still
    # failed to surface it (tested directly, see FINDINGS_AND_NEXT_STEPS.md)
    # -- a short, generically-relevant chunk reliably loses a raw term-
    # overlap contest against longer, more specific same-topic chunks, no
    # matter how the query is worded. Forcing candidacy sidesteps that
    # entirely and lets the cross-encoder (context-aware, unlike BM25/RRF
    # rank position) make the actual relevance call via baseline_bonus below
    # -- same division of labor as the graph arm already uses. Guideline-
    # scoped and capped small (see _is_baseline_treatment_chunk: ~6% of the
    # corpus, only a handful per guideline) so this can't meaningfully
    # inflate reranker cost.
    if guideline_allowed_idx is not None:
        for i in guideline_allowed_idx:
            c = store.chunks[i]
            if c.chunk_id not in seen and _is_baseline_treatment_chunk(c.chunk_text, c.section):
                seen.add(c.chunk_id)
                candidates.append(c)

    if not candidates:
        return _fallback_chunks_harness(query, active_guideline, top_k)

    try:
        pairs = [(query, c.chunk_text) for c in candidates]
        rerank_scores = [float(s) for s in reranker.predict(pairs)]
    except Exception as e:
        # Matches retrieve.py's graceful degradation exactly: RRF order,
        # decaying synthetic scores, no request-ending exception.
        print(f"[Retrieval] Reranker unavailable ({e}) — using RRF order without reranking")
        degraded_items = [(c, 1.0 - i * 0.01) for i, c in enumerate(candidates)]
        seen_groups: dict[str, tuple[Chunk, float]] = {}
        group_order: list[str] = []
        for chunk, score in degraded_items:
            gk = store.chunk_group_key.get(chunk.chunk_id, chunk.chunk_id)
            if gk not in seen_groups:
                seen_groups[gk] = (chunk, score)
                group_order.append(gk)
        selected = [seen_groups[gk] for gk in group_order[:top_k]]
        return _expand_groups(store, selected)

    # ── BUGFIX 2026-08a/c: hard guideline filter, secondary safety net ────
    # Gold_chunk_ids never crosses guideline sources for any of the 30
    # cases (confirmed by direct inspection), so an off-guideline chunk is
    # never a correct retrieval result on this dataset -- it should be
    # excluded outright, not merely soft-nudged down by a few percent
    # against a cross-encoder score it can easily win on generic semantic
    # similarity.
    #
    # The PRIMARY mechanism for this is now upstream: guideline_allowed_idx
    # masks semantic_search/bm25_search themselves (BUGFIX 2026-08c) so
    # off-guideline chunks are never even fetched into `candidates` in the
    # first place. This block is what's left of the original (2026-08a)
    # fix, kept as a defensive no-op double-check for any candidate that
    # reaches this point despite the upstream mask (there currently isn't
    # one, since `candidates` is built entirely from semantic_hits/
    # bm25_hits) -- and it still does something real when
    # guideline_allowed_idx was set to None because the corpus had zero
    # matching chunks (see the guard right after computing it above), in
    # which case candidates legitimately can be off-guideline and this
    # topping-up logic is the only thing standing between "return nothing"
    # and "return the best available off-topic chunks."
    if guideline_hard_filter:
        _pairs = list(zip(candidates, rerank_scores))
        _on = [(c, s) for c, s in _pairs if _guideline_source_match(active_guideline, c.source)]
        if len(_on) >= top_k:
            candidates, rerank_scores = [c for c, _ in _on], [s for _, s in _on]
        else:
            _off = [(c, s) for c, s in _pairs if not _guideline_source_match(active_guideline, c.source)]
            _off.sort(key=lambda cs: cs[1], reverse=True)
            _topped = _on + _off[: max(0, top_k - len(_on))]
            candidates, rerank_scores = [c for c, _ in _topped], [s for _, s in _topped]

    case_who_tier = _who_tier_from_query(query)

    # ── FinalScore computation ────────────────────────────────────────────
    # Original: FinalScore = CrossEncoder + GuidelineNudge (flat 0.15 for
    # on-guideline chunks, 0 otherwise).
    #
    # Improved: FinalScore = 0.75 * CrossEncoder
    #                      + 0.10 * SectionWeight(chunk_type, intent)
    #                      + 0.05 * AgeMatch(query, chunk_text)
    #                      + 0.05 * GuidelineBoost
    #                      + 0.05 * WhoSeverityTierScore
    #
    # The cross-encoder weight is critical: it keeps the reranker in
    # charge of semantic relevance (which is correct) while the metadata
    # terms handle cases it systematically gets wrong (recommendation-vs-
    # background; age-band confusion; WHO tier confusion). The original
    # guideline_nudge_weight parameter is now the GuidelineBoost
    # coefficient.
    #
    # BUGFIX 2026-08b: guideline_boost now uses the exact
    # _guideline_source_match instead of `guideline_upper in source_key`
    # (a substring test that treated "AAP_PRETERM" as matching active_
    # guideline "AAP" because "AAP" is a substring of "AAP_PRETERM" --
    # see the docstring above _GUIDELINE_ALLOWED_SOURCES). With
    # guideline_hard_filter=True (the default) this boost mostly just
    # breaks ties among already-on-guideline candidates; it's kept (rather
    # than removed) so the ablation with guideline_hard_filter=False still
    # gets a correctly-computed soft nudge.
    final_scores: list[float] = []
    for chunk, ce_score in zip(candidates, rerank_scores):
        guideline_boost = guideline_nudge_weight if _guideline_source_match(active_guideline, chunk.source) else 0.0
        who_tier_score = who_severity_tier_score(active_guideline, case_who_tier, chunk.source, chunk.chunk_id, chunk.chunk_text)

        if use_metadata_reranking:
            sw = _section_weight(chunk.chunk_type, intent)
            am = _age_band_match(query, chunk.chunk_text)
            # BUGFIX 2026-08d: who_tier_score weight raised 0.05 -> 0.15 (and
            # cross-encoder correspondingly trimmed 0.75 -> 0.65). At 0.05 the
            # tier signal was too weak to overcome the cross-encoder's own
            # preferences: WHO_pg26 ("Other studies" -- a generic CRP/PCT
            # accuracy review with no tier affiliation, tier_score=0.0) was
            # observed being retrieved for nearly every WHO case regardless
            # of category, out-scoring correctly-tiered chunks on ce_score
            # alone. 0.15 gives a correctly-tiered chunk enough of a lift to
            # beat a same-ce_score tier-agnostic distractor, while a
            # wrong-tier chunk now takes a real penalty instead of a
            # rounding error.
            #
            # BUGFIX 2026-08e: small always-on nudge (0.05) for baseline
            # treatment-decision chunks (see _is_baseline_treatment_chunk
            # docstring) -- deliberately the smallest weight in this formula
            # (same "below any real match" spirit as graph's _BASELINE_SCORE
            # being below its entity/section-match scores), so it acts as a
            # tie-breaker/lift rather than overriding a strong ce_score match.
            baseline_bonus = 1.0 if _is_baseline_treatment_chunk(chunk.chunk_text, chunk.section) else 0.0
            final = (0.65 * ce_score
                     + 0.10 * sw
                     + 0.05 * am
                     + 0.05 * guideline_boost
                     + 0.15 * who_tier_score
                     + 0.05 * baseline_bonus)
        else:
            # Original single-nudge formula (for ablation comparison),
            # extended with the WHO tier term (kept even here since a
            # systematic tier swap is a correctness bug, not a metadata-
            # reranking "improvement" that should be ablatable away).
            final = ce_score + guideline_boost + who_tier_score

        final_scores.append(final)

    order = sorted(range(len(candidates)), key=lambda i: final_scores[i], reverse=True)

    # Retrieval-time grouping: collapse candidates to one representative per
    # group (its best-final-score member) BEFORE the MMR pool is built,
    # so a recommendation and its same-page companion paragraph can't both
    # eat separate top_k slots. Each group is expanded at the very end.
    seen_groups: dict[str, tuple[Chunk, float]] = {}
    group_order: list[str] = []
    for i in order:
        chunk = candidates[i]
        gk = store.chunk_group_key.get(chunk.chunk_id, chunk.chunk_id)
        if gk not in seen_groups:
            seen_groups[gk] = (chunk, final_scores[i])
            group_order.append(gk)

    pool_size = min(len(group_order), max(top_k * 3, top_k + 5))
    pool = [seen_groups[gk] for gk in group_order[:pool_size]]

    # ── MMR with embedding cosine similarity ─────────────────────────────
    # Build a chunk_id → normalised embedding vector lookup from the store
    # so _mmr_select can use cosine similarity instead of Jaccard overlap.
    if use_mmr:
        chunk_id_to_idx: dict[str, int] = {c.chunk_id: i for i, c in enumerate(store.chunks)}
        norms = np.linalg.norm(store.embeddings, axis=1, keepdims=True) + 1e-10
        normed_embs = store.embeddings / norms
        embed_map = {
            chunk.chunk_id: normed_embs[chunk_id_to_idx[chunk.chunk_id]]
            for chunk, _ in pool
            if chunk.chunk_id in chunk_id_to_idx
        } or None
        selected = _mmr_select(pool, k=top_k, embed_map=embed_map)
    else:
        selected = pool[:top_k]

    # ── BUGFIX 2026-08i: coverage guarantee, ported from graph_care_plan_
    # retrieval.py's proven mechanism ────────────────────────────────────
    # graph's coverage-pass took AAP anchor_grounding_recall from 0.239 to
    # 0.863 by GUARANTEEING a top_k slot for each baseline-eligible
    # section_type, not just nudging its score. baseline_bonus above is
    # still the older "nudge, not guarantee" pattern graph itself outgrew:
    # a chunk correctly flagged as baseline-relevant can still lose the
    # cut to several strong but off-topic cross-encoder matches, the same
    # failure mode graph had before its coverage-pass fix. This closes
    # that gap the same way, using chunk.section as vector_fn's analogue
    # of graph's section_type (there's no clean recommendation-level
    # section_type at chunk granularity -- see _is_baseline_treatment_
    # chunk's docstring). _COVERAGE_ROUNDS=2 reuses graph's own tuned
    # value (swept 1-4 there) rather than re-deriving it, since the
    # trade-off shape (more rounds displaces non-baseline content for no
    # further gain) is a property of shared top_k, not graph-specific.
    selected_ids = {c.chunk_id for c, _ in selected}
    represented_sections = {
        c.section for c, _ in selected
        if _is_baseline_treatment_chunk(c.chunk_text, c.section)
    }
    baseline_by_section: dict[str, list[tuple]] = {}
    for i in order:
        c = candidates[i]
        if c.chunk_id in selected_ids or not _is_baseline_treatment_chunk(c.chunk_text, c.section):
            continue
        baseline_by_section.setdefault(c.section, []).append((c, final_scores[i]))

    rounds_done = 0
    for section, cands in baseline_by_section.items():
        if rounds_done >= _COVERAGE_ROUNDS or section in represented_sections:
            continue
        non_baseline_idx = [
            j for j, (c, _) in enumerate(selected)
            if not _is_baseline_treatment_chunk(c.chunk_text, c.section)
        ]
        if not non_baseline_idx:
            break
        worst_j = min(non_baseline_idx, key=lambda j: selected[j][1])
        selected[worst_j] = cands[0]
        selected_ids.add(cands[0][0].chunk_id)
        represented_sections.add(section)
        rounds_done += 1

    results = _expand_groups(store, selected)

    # ── Context expansion (sibling chunks appended after selection) ───────
    if use_context_expansion and store.group_members:
        results = _context_expand_siblings(results, store, intent)

    return results


# ─────────────────────────────────────────────────────────────────────────
# 7. Metrics
# ─────────────────────────────────────────────────────────────────────────

def precision_recall_f1(retrieved_ids: list[str], gold_ids: list[str]) -> dict[str, float]:
    retrieved_set, gold_set = set(retrieved_ids), set(gold_ids)
    if not gold_set:
        # negative_distractor stratum: gold is intentionally empty.
        # "Precision" here is reframed as 1.0 if nothing gold-irrelevant was
        # force-cited, which for pure retrieval we treat as: did retrieval
        # avoid returning any of the *other* strata's gold chunks as top-1.
        return {"precision": float(len(retrieved_set) == 0), "recall": 1.0, "f1": float(len(retrieved_set) == 0)}
    tp = len(retrieved_set & gold_set)
    precision = tp / len(retrieved_set) if retrieved_set else 0.0
    recall = tp / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def mrr(retrieved_ids: list[str], gold_ids: list[str]) -> float:
    gold_set = set(gold_ids)
    if not gold_set:
        return float("nan")
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in gold_set:
            return 1.0 / rank
    return 0.0


# ─────────────────────────────────────────────────────────────────────────
# 8. LLM provider — pluggable, mirrors llm_provider.py's chain concept
# ─────────────────────────────────────────────────────────────────────────

class LLMUnavailableError(Exception):
    pass


# Groq-hosted backbones worth comparing as of Aug 2026. llama-3.3-70b-versatile
# (this harness's long-standing default) was announced deprecated by Groq on
# 2026-06-17 for free/developer-tier usage -- it may already be unavailable
# on your key by the time you run this. Groq's own migration guidance points
# to openai/gpt-oss-120b as the closest replacement; qwen/qwen3.6-27b is
# listed as Groq's current highest-intelligence model. Always check
# https://console.groq.com/docs/models for the live list before a real run --
# Groq's lineup changes frequently and this list will go stale.
GROQ_CANDIDATE_BACKBONES: list[str] = [
    "openai/gpt-oss-120b",     # recommended replacement for llama-3.3-70b-versatile
    "qwen/qwen3.6-27b",        # Groq's current highest-intelligence model (per Artificial Analysis, Aug 2026)
    "openai/gpt-oss-20b",      # smaller/faster third point of comparison
]


def make_call_llm(
    gemini_api_key: str = "",
    groq_api_key: str | list[str] = "",
    groq_model: str = "openai/gpt-oss-120b",
    local_llm_url: str = "",
    local_llm_model: str = "qwen2.5:3b-instruct-q4_K_M",
    local_llm_retries: int = 1,
    mock: bool = False,
):
    """
    Returns a call_llm(prompt, system=...) -> (raw_text, model_version)
    function, mirroring llm_provider.py's call_llm() provider-chain
    signature/behavior (Groq -> Local -> Gemini, else raise
    LLMUnavailableError) but called directly from Colab rather than through
    the app's routes.

    Local LLM support (RESOLVED — this used to be a DIVERGENCE): Colab
    cannot reach localhost:11434 on your machine directly, so
    `local_llm_url` must be a *tunnel* URL exposing your Ollama instance's
    OpenAI-compatible endpoint. On your own machine run:

        ngrok http 11434

    then paste the printed `https://xxxx.ngrok-free.app` forwarding URL in
    as `local_llm_url` (with or without a trailing `/v1` — this function
    normalizes it). The payload POSTed mirrors llm_provider.py's
    _call_local() exactly (model/messages/temperature/max_tokens), since
    Ollama's `/v1/chat/completions` endpoint is OpenAI-compatible.
    `response_format={"type": "json_object"}` is deliberately NOT set here,
    for the same reason documented in llm_provider.py: grammar-constrained
    JSON decoding is a severe (commonly 5-10x) per-token slowdown on a
    CPU-served quantized model like qwen2.5:3b-instruct-q4_K_M. Instead this
    relies on the system-prompt instruction plus one same-provider retry on
    a JSON-parse failure — small local models are meaningfully less
    reliable at strict JSON than Gemini/Groq-class models without
    grammar-constrained decoding, exactly as noted in llm_provider.py's
    call_llm() docstring.

    Provider order: Groq (if key set) -> Local (if local_llm_url set) ->
    Gemini (if key set). For THIS project's actual setup (qwen2.5:3b-instruct-
    q4_K_M via Ollama, no Gemini/Groq), leave gemini_api_key/groq_api_key
    blank — only the Local branch is tried, and LLMUnavailableError is
    raised (rather than silently falling through to an unconfigured cloud
    provider) if the tunnel is unreachable.

    Multi-key Groq fallback: `groq_api_key` accepts either a single string
    or a list of strings. When a list is given, each call tries the keys in
    order and moves to the next key on ANY failure from that key (rate
    limit, invalid key, transient 5xx, etc.) before falling through to
    Local/Gemini -- this is what you want when running the full 65-item
    QA CSV against Groq's free tier, whose per-key daily token cap (100k
    TPD as of this writing) is easy to exceed mid-run on a single key.
    Once a key hits a 429 with a "tokens per day" message, it's marked dead
    for the rest of this call_llm's lifetime (i.e. this notebook session)
    and skipped instantly on every subsequent call instead of being
    re-tried and paying its rate-limit latency again -- free-tier TPD caps
    reset daily, not on the ~10-60s cooldown Groq quotes for per-minute
    limits, so retrying a TPD-exhausted key later in the same run cannot
    succeed.

    Set mock=True to skip all network calls and use a deterministic
    templated stub instead (useful for smoke-testing the harness without
    burning API quota or needing the tunnel up yet).

    `groq_model`: which Groq-hosted model to call when the Groq branch is
    used. Previously hardcoded to "llama-3.3-70b-versatile" -- now
    parameterized so you can run the SAME retrieval arm (vector/graph/
    hybrid) across multiple backbones for a backbone-comparison table
    (mirroring ADRE's Table 2, which reports Qwen3/Gemma3/DeepSeek-R1 side
    by side). See GROQ_CANDIDATE_BACKBONES above for current options, and
    `run_multi_backbone_comparison()` below to sweep all of them in one call.
    NOTE: llama-3.3-70b-versatile was deprecated by Groq on 2026-06-17 for
    free/developer-tier keys -- if this call starts failing with a
    decommissioned-model error, that's why; switch groq_model to one of the
    other GROQ_CANDIDATE_BACKBONES entries.
    """
    if mock:
        def _mock(prompt: str, system: str = "") -> tuple[str, str]:
            return json.dumps({
                "clinical_summary": "[MOCK] Deterministic stub response — no LLM called.",
                "risk_analysis": "[MOCK]", "trend_narrative": "", "driver_breakdown": "[MOCK]",
                "recommended_actions": ["[MOCK] action"],
                "antibiotic_plan": {"required": True, "urgency": "Within 1 hour",
                                     "regimen": ["IV benzylpenicillin", "IV gentamicin"],
                                     "duration": "", "stop_criteria": ""},
                "monitoring_plan": "[MOCK]", "escalation_criteria": "[MOCK]",
                "nutrition_fluid_plan": "", "parent_communication_notes": "",
                "disambiguation_block": "", "contraindication_flags": [],
                "trend_state_change": "", "citation_list": [],
                "confidence_disclaimer": "MOCK RESPONSE — NOT REAL CLINICAL OUTPUT",
            }), "mock-stub"
        return _mock

    groq_api_keys = [groq_api_key] if isinstance(groq_api_key, str) else list(groq_api_key)
    groq_api_keys = [k for k in groq_api_keys if k]
    _dead_groq_keys: set[str] = set()  # keys that hit a daily-TPD 429 this session

    def _post_local(prompt: str, system: str) -> str:
        import httpx
        url = local_llm_url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        payload = {
            "model": local_llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }
        with httpx.Client(timeout=1800.0) as client:
            resp = client.post(f"{url}/chat/completions", json=payload)
            if resp.status_code >= 400:
                # Logging the body turns a bare 4xx/5xx into an actionable
                # message (most commonly: ngrok free-tier interstitial page,
                # wrong model name, or Ollama not actually running/pulled).
                print(f"[LocalLLM] {resp.status_code} from {url}: {resp.text[:1000]}")
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_llm(prompt: str, system: str = "You respond with ONLY valid JSON.") -> tuple[str, str]:
        last_error: Exception | None = None
        # BUGFIX: the Groq and Gemini branches below used to force JSON-mode
        # output (response_format=json_object / response_mime_type=
        # application/json) unconditionally, regardless of what `system`
        # actually asked for. That's correct for the care-plan JSON-schema
        # calls elsewhere in this harness (system defaults to "You respond
        # with ONLY valid JSON."), but generate_rag_answer_harness calls
        # this with _QA_ANSWER_SYSTEM_PROMPT, which deliberately asks for
        # 1-4 sentences of plain prose -- forcing JSON mode there is wrong
        # even where it doesn't error outright, and Groq's API additionally
        # *requires* the literal word "json" somewhere in the messages when
        # response_format=json_object is set, which a prose-only prompt
        # doesn't have, producing a hard 400 on every call. JSON mode is now
        # only requested when `system` itself says JSON is expected.
        want_json = "json" in system.lower()

        if groq_api_keys:
            from groq import Groq
            live_keys = [k for k in groq_api_keys if k not in _dead_groq_keys]
            for gi, key in enumerate(live_keys, start=1):
                try:
                    client = Groq(api_key=key)
                    groq_kwargs: dict[str, Any] = dict(
                        model=groq_model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        temperature=0.1, max_tokens=2048,
                    )
                    if want_json:
                        groq_kwargs["response_format"] = {"type": "json_object"}
                        # BUGFIX: qwen3-family models default to "thinking mode" on
                        # Groq, which interleaves <think>...</think> reasoning
                        # tokens into message.content. Combined with strict
                        # response_format=json_object validation, that produces
                        # Groq's json_validate_failed error on every call (not a
                        # rate-limit issue -- it fails immediately, on every key).
                        # reasoning_effort="none" turns thinking mode off so
                        # content is pure JSON. openai/gpt-oss-* models use a
                        # different reasoning_effort vocabulary (low/medium/high,
                        # no "none") and don't have this failure mode with JSON
                        # mode, so this is scoped to qwen3-family models only.
                        if groq_model.startswith("qwen/qwen3"):
                            groq_kwargs["reasoning_effort"] = "none"
                    resp = client.chat.completions.create(**groq_kwargs)
                    return resp.choices[0].message.content, groq_model
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    # "tokens per day" 429s won't recover within this run (Groq's
                    # free-tier TPD cap resets daily, not on the short per-minute
                    # cooldown it quotes) -- mark this key dead so every later
                    # call in this session skips straight past it.
                    if "429" in err_str and "tokens per day" in err_str.lower():
                        _dead_groq_keys.add(key)
                        note = "daily token cap hit — marking key dead for this session"
                    elif "429" in err_str:
                        note = "rate limited"
                    else:
                        note = "error"
                    remaining = len(live_keys) - gi
                    next_step = f"trying Groq key {gi + 1}/{len(live_keys)}" if remaining else "trying Local"
                    print(f"[LLM] Groq key {gi}/{len(live_keys)} failed ({note}: {e}) — {next_step}")
            if not live_keys and groq_api_keys:
                print(f"[LLM] All {len(groq_api_keys)} Groq key(s) marked dead this session — trying Local")

        if local_llm_url:
            for attempt in range(local_llm_retries + 1):
                try:
                    raw = _post_local(prompt, system)
                    json.loads(strip_json_fences(raw))  # validate; JSONDecodeError triggers retry below
                    print(f"[LLM] Local ({local_llm_model}) succeeded (attempt {attempt + 1})")
                    return raw, local_llm_model
                except (json.JSONDecodeError, ValueError) as e:
                    last_error = e
                    print(f"[LLM] Local ({local_llm_model}) attempt {attempt + 1}/{local_llm_retries + 1} "
                          f"produced invalid JSON — retrying same provider")
                    continue
                except Exception as e:
                    # Connection errors / timeouts / tunnel down: identical
                    # on immediate retry, so move straight to the next
                    # provider (if any) instead of burning the retry budget.
                    last_error = e
                    print(f"[LLM] Local ({local_llm_model}) failed ({type(e).__name__}: {e}) — "
                          f"moving to next provider without retry")
                    break

        if gemini_api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_api_key)
                gen_config = {"temperature": 0.1, "top_p": 0.9, "max_output_tokens": 2048}
                if want_json:
                    gen_config["response_mime_type"] = "application/json"
                model = genai.GenerativeModel(
                    model_name="gemini-2.0-flash",
                    system_instruction=system,
                    generation_config=gen_config,
                )
                resp = model.generate_content(prompt)
                return resp.text, "gemini-2.0-flash"
            except Exception as e:
                last_error = e
                print(f"[LLM] Gemini failed ({e})")

        raise LLMUnavailableError(
            "No LLM provider succeeded. Pass groq_api_key, local_llm_url (an ngrok "
            "tunnel to your Ollama instance running qwen2.5:3b-instruct-q4_K_M), or "
            f"gemini_api_key, or set mock=True for a dry run. Last error: {last_error}"
        )
    return _call_llm


def run_multi_backbone_comparison(
    items: list,
    retrieve_fns: dict[str, Callable],
    groq_api_key: str | list[str],
    backbones: list[str] | None = None,
    run_arm_fn: Callable | None = None,
    checkpoint_dir: str = "./multi_backbone_checkpoints",
    resume: bool = True,
) -> "pd.DataFrame":
    """Sweep every (arm, backbone) combination and return one long-format
    DataFrame, so you can build an ADRE-style Table 2 (rows=variant,
    columns=backbone) directly with a pivot.

    items:        the eval dataset's case items (from
                   neoguard_care_plan_eval_dataset.json).
    retrieve_fns: {"vector": vector_fn, "graph": graph_fn, "hybrid": hybrid_fn}
                  -- e.g. from graph_care_plan_retrieval.make_vector_retrieve_fn /
                  make_graph_retrieve_fn / make_hybrid_retrieve_fn.
    backbones:    list of Groq model strings to sweep; defaults to
                  GROQ_CANDIDATE_BACKBONES (currently 3 models).
    run_arm_fn:   the per-arm runner, e.g.
                  graph_care_plan_retrieval.run_care_plan_evaluation_harness_pluggable.
                  Required -- passed explicitly rather than imported here to
                  avoid a circular import between this module and
                  graph_care_plan_retrieval.py.
    checkpoint_dir: each (backbone, arm) combination is written to its own
                  CSV here as soon as it finishes -- e.g.
                  "openai_gpt-oss-120b__vector.csv" -- so a crash or an
                  exhausted API key partway through does not lose already-
                  completed combinations.
    resume:       if True (default), a combination whose checkpoint CSV
                  already exists is loaded from disk and NOT re-run --
                  this is what lets you re-invoke this function after a
                  quota error and only pay for the remaining combinations.
                  Set False to force re-running everything from scratch.

    NOTE: this function makes real Groq API calls (len(items) * len(arms) *
    len(backbones) of them) and needs network access + a valid groq_api_key.
    It is not executed in this patch -- wire it up and run it in an
    environment with network access to actually produce new results.
    """
    import os
    import pandas as pd

    if backbones is None:
        backbones = GROQ_CANDIDATE_BACKBONES
    if run_arm_fn is None:
        raise ValueError("run_multi_backbone_comparison requires run_arm_fn "
                          "(pass graph_care_plan_retrieval.run_care_plan_evaluation_harness_pluggable)")

    os.makedirs(checkpoint_dir, exist_ok=True)

    def _safe(name: str) -> str:
        return name.replace("/", "_").replace(":", "_")

    all_rows = []
    failed_combinations = []

    for backbone in backbones:
        call_llm = None  # created lazily, only if at least one arm for this backbone still needs running
        for arm_name, retrieve_fn in retrieve_fns.items():
            ckpt_path = os.path.join(checkpoint_dir, f"{_safe(backbone)}__{arm_name}.csv")

            if resume and os.path.exists(ckpt_path):
                print(f"[multi-backbone] arm={arm_name} backbone={backbone} -- "
                      f"found checkpoint, loading from disk (skipping API calls)")
                all_rows.append(pd.read_csv(ckpt_path))
                continue

            if call_llm is None:
                call_llm = make_call_llm(groq_api_key=groq_api_key, groq_model=backbone)

            print(f"[multi-backbone] arm={arm_name} backbone={backbone} -- running {len(items)} items...")
            try:
                out = run_arm_fn(items, retrieve_fn, call_llm, arm_name=arm_name)
            except Exception as e:
                # A quota/auth/rate-limit error here should not take down the
                # combinations already completed (or the ones still to come
                # for OTHER backbones/arms) -- log it, note it, and continue.
                print(f"[multi-backbone] FAILED arm={arm_name} backbone={backbone}: {e!r}")
                print(f"  -> already-completed combinations are safe on disk in {checkpoint_dir}/")
                print(f"  -> re-run this function later with the same checkpoint_dir to pick up where it left off")
                failed_combinations.append((backbone, arm_name, repr(e)))
                continue

            df = pd.DataFrame(out["per_item"])
            df["arm"] = arm_name
            df["backbone"] = backbone
            df.to_csv(ckpt_path, index=False)  # persist immediately -- this combo is now safe
            all_rows.append(df)

    if failed_combinations:
        print(f"\n{len(failed_combinations)} combination(s) failed and were skipped:")
        for backbone, arm_name, err in failed_combinations:
            print(f"  - backbone={backbone} arm={arm_name}: {err}")
        print("Re-run run_multi_backbone_comparison(..., checkpoint_dir=" + repr(checkpoint_dir) +
              ") to retry just these -- completed combinations will be loaded from disk, not re-run.")

    if not all_rows:
        raise RuntimeError("No combinations completed successfully -- check the errors above.")
    return pd.concat(all_rows, ignore_index=True)


def strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text


# ─────────────────────────────────────────────────────────────────────────
# 9. Query refiner — adapted from query_refiner.py, pluggable call_llm
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class RefinedQuery:
    action: Literal["rewrite", "decompose", "HyDE", "pass_through"]
    sub_queries: list[str]
    hyde_passage: str = ""


def refine_query_harness(
    raw_query: str,
    risk_payload: dict,
    call_llm: Callable,
    trends: "SustainedTrendsHarness | None" = None,
) -> RefinedQuery:
    """Adapted from query_refiner.py, with two changes for this experiment:

    1. Context-aware decompose hint (RESOLVED gap): production's
       refine_query() now takes an optional `context` and, when it carries
       trend terms, tells the classifier prompt about them so "decompose"
       can be chosen correctly for a genuinely multi-hop case (e.g. a rising
       -CRP trend AND a new contraindication) rather than judging bare query
       text alone. This harness previously had no equivalent — `trends`
       (a SustainedTrendsHarness, see compute_sustained_trends_harness) now
       plays that role here.

    2. "disambiguate" -> "HyDE" (per this experiment's explicit ask): the
       action space is rewrite / decompose / HyDE / pass_through, not
       rewrite / decompose / disambiguate / pass_through. HyDE
       (Hypothetical Document Embeddings) asks the LLM to draft a short
       HYPOTHETICAL guideline passage that would plausibly answer the
       query, then that hypothetical passage — not the raw query — is what
       gets embedded for the SEMANTIC search leg (see retrieve_evidence_
       harness's `semantic_query` param below); BM25 still runs on the
       original/expanded query, since HyDE is specifically a dense-
       embedding technique (a hypothetical answer's embedding sits closer
       to real document embeddings than a bare question's embedding does —
       keyword overlap doesn't get the same benefit, and could even get
       worse if the hypothetical invents different phrasing than the
       source text uses).

    NOTE: your uploaded production query_refiner.py still uses
    "disambiguate", not HyDE — this change is scoped to this Colab
    ablation harness only. Let me know if you also want HyDE ported into
    the real backend's query_refiner.py; that's a separate, larger change
    (it touches retrieve.py's semantic_search call site too) and I didn't
    assume you wanted it applied there without confirmation.
    """
    category = risk_payload.get("category", risk_payload.get("risk_category", ""))

    context_block = ""
    if trends is not None:
        trend_terms = trends.describe()
        if trend_terms or trends.assessment_count:
            context_block = (
                f"\nPATIENT CONTEXT: {trends.assessment_count} assessment(s) on record"
                + (f"; trends: {', '.join(trend_terms)}" if trend_terms else "")
                + "\n"
            )

    prompt = f"""Classify this clinical retrieval query and produce refined search strings.

RAW QUERY: {raw_query}
RISK CATEGORY: {category}
{context_block}
Choose exactly ONE action:
- "rewrite": improve the query wording (output 1 refined string)
- "decompose": split into 2-5 sub-queries for multi-aspect retrieval --
  consider decomposing if PATIENT CONTEXT shows multiple distinct trend
  signals (e.g. a rising-CRP trend AND an AKI stage) that likely need
  different guideline sections, not just a single symptom
- "HyDE": write a short (2-4 sentence) HYPOTHETICAL guideline passage that
  would plausibly answer this query, in the style of a clinical practice
  guideline recommendation -- output it as the single string in
  "hyde_passage" (not in sub_queries). Choose this when the query is a
  clinical question best matched by what a correct guideline PASSAGE would
  say, rather than by rewording the question itself.
- "pass_through": query is already good (output the original)

Respond with ONLY valid JSON:
{{"action": "<rewrite|decompose|HyDE|pass_through>", "sub_queries": ["<query1>", "..."], "hyde_passage": "<only for HyDE>"}}"""
    try:
        raw, _ = call_llm(prompt, system="You classify clinical search queries. Respond with valid JSON only.")
        data = json.loads(strip_json_fences(raw))
        action = data.get("action", "pass_through")
        if action not in ("rewrite", "decompose", "HyDE", "pass_through"):
            action = "pass_through"
        sub_queries = [str(q).strip() for q in data.get("sub_queries", []) if str(q).strip()]
        hyde_passage = str(data.get("hyde_passage", "")).strip()
        if action == "HyDE" and not hyde_passage:
            # Model chose HyDE but didn't actually produce a passage --
            # fail toward pass_through rather than embed an empty string.
            action = "pass_through"
        if not sub_queries:
            sub_queries = [raw_query.strip()]
        if action == "decompose":
            sub_queries = sub_queries[:5]
        return RefinedQuery(action=action, sub_queries=sub_queries, hyde_passage=hyde_passage)
    except Exception:
        return RefinedQuery(action="pass_through", sub_queries=[raw_query.strip()])


# ─────────────────────────────────────────────────────────────────────────
# 10. Fact-check judge — adapted from fact_check.py
# ─────────────────────────────────────────────────────────────────────────

def build_judge_prompt(draft_summary: str, chunks: list[ChunkResult], active_guideline: str,
                       missed_required_disambiguation: bool = False) -> str:
    chunk_text = "\n\n".join(f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}" for c in chunks) \
        or "(no guideline chunks were retrieved)"
    missed_block = ""
    if missed_required_disambiguation:
        missed_block = ("\n\nDETERMINISTIC ALERT: Cross-guideline conflict was detected in retrieved "
                         "evidence but the draft's disambiguation_block is EMPTY. This is a required "
                         "disclosure failure — set verified=false and flag it.")
    return f"""You are a fact-checking judge reviewing clinical AI output before it reaches a doctor. \
Verify every clinical claim in the draft against the retrieved evidence below only.

ACTIVE GUIDELINE: {active_guideline}

RETRIEVED GUIDELINE EVIDENCE:
{chunk_text}

DRAFT UNDER REVIEW:
{draft_summary}
{missed_block}

Respond with ONLY valid JSON:
{{"verified": <bool>, "confidence": <float 0-1>, "flagged_claims": ["..."], "notes": "<1-2 sentences>"}}"""


def run_fact_check_harness(draft_summary: str, chunks: list[ChunkResult], active_guideline: str,
                            call_llm: Callable, missed_required_disambiguation: bool = False) -> dict:
    prompt = build_judge_prompt(draft_summary, chunks, active_guideline, missed_required_disambiguation)
    try:
        raw, model = call_llm(prompt, system="You are a strict clinical fact-checking judge. Respond with valid JSON only.")
        data = json.loads(strip_json_fences(raw))
        return {"performed": True, "verified": bool(data.get("verified", True)),
                "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                "flagged_claims": [str(c) for c in data.get("flagged_claims", [])],
                "notes": str(data.get("notes", "")), "judge_model": model}
    except Exception as e:
        return {"performed": False, "verified": True, "confidence": 0.0,
                "flagged_claims": [], "notes": f"Fact-check unavailable: {e}", "judge_model": ""}


# ─────────────────────────────────────────────────────────────────────────
# 11. Vignette loading helpers
# ─────────────────────────────────────────────────────────────────────────

def load_vignettes(path: str) -> tuple[dict, list[dict]]:
    with open(path) as f:
        data = json.load(f)
    return data["metadata"], data["vignettes"]


def vignette_to_risk_payload(v: dict) -> dict[str, Any]:
    """Translates a vignette's patient_context into the shape
    build_clinical_query_harness()/query_builder.py expects. This harness
    does not run the actual EOSCAL scoring engine (that lives in the Flutter
    app's eoscal_calculator.dart / EOS risk engine, not in the RAG backend),
    so category/total_score are heuristically approximated from the
    vignette's own stratum + fields — sufficient to exercise retrieval
    query-building, NOT a substitute for the real scorer. Flagged here so
    it's not mistaken for ground truth risk scoring in the paper.

    PATCHED 2026-07-25: this previously dropped every WHO-IMCI clinical sign
    (respiratory_rate, chest_indrawing, feeding, movement, convulsions,
    age_days) on the floor -- they exist in every vignette's patient_context
    but were never copied into `patient`, and `drivers` was hardcoded to []
    with nothing else populating it. build_clinical_query_harness() has no
    other source of clinical-sign terms, so for every vignette WITHOUT a
    free_text_query (i.e. every stratum except lay_clinician_natural), the
    resulting query degraded to a fixed 6-word boilerplate string
    ("EOS management neonatal sepsis guidelines <CATEGORY> risk <GUIDELINE>")
    that was IDENTICAL across dozens of clinically-distinct vignettes sharing
    a guideline+category. That's confirmed as the dominant cause of
    guideline_exact/multi_hop/negative_distractor scoring near-zero while
    lay_clinician_natural (which supplies its own free_text_query and so
    never hit this path) consistently outperformed them -- see the
    2026-07-25 diagnosis. This fix (a) copies the missing fields into
    `patient` so the existing WHO-blind checks below can key off *some* of
    them, and (b) builds `drivers` directly from the IMCI signs, which
    build_clinical_query_harness already knows how to turn into query terms."""
    pc = v["patient_context"]
    category = "HIGH" if v["stratum"] in ("ambiguous_cross_guideline", "contraindication", "trend_deterioration") else "INTERMEDIATE"
    patient = {
        "gestational_age_weeks": pc.get("gestational_age_weeks"),
        "maternal_temperature": pc.get("maternal_temp_c"),
        "rom_hours": pc.get("rom_hours"),
        "gbs_positive": pc.get("gbs_status") == "positive",
        "respiratory_distress": pc.get("respiratory_distress"),
        "crp_level": pc.get("crp_level"),
        "blood_culture_positive": False,
        "birth_weight_g": pc.get("birth_weight_g"),
        "neonatal_temperature": pc.get("neonatal_temperature"),
    }

    drivers: list[dict[str, str]] = []
    age_days = pc.get("age_days")
    rr = pc.get("respiratory_rate")
    if rr is not None:
        band = "0-6 days" if (age_days is not None and age_days <= 6) else "7-59 days"
        if rr >= 60:
            drivers.append({"name": "fast breathing", "reason": f"respiratory rate {rr}/min ({band} band)"})
    if pc.get("chest_indrawing"):
        drivers.append({"name": "chest indrawing", "reason": "severe chest indrawing present"})
    feeding = pc.get("feeding")
    if isinstance(feeding, str) and feeding.lower() in ("poor", "not feeding", "unable to feed"):
        drivers.append({"name": "poor feeding", "reason": f"feeding status: {feeding}"})
    movement = pc.get("movement")
    if isinstance(movement, str) and movement.lower() in ("reduced", "none", "no movement", "lethargic"):
        drivers.append({"name": "reduced movement", "reason": f"movement status: {movement}"})
    if pc.get("convulsions"):
        drivers.append({"name": "convulsions", "reason": "convulsions present"})
    if age_days is not None:
        drivers.append({"name": f"young infant {age_days} days old", "reason": "age band affects applicable recommendation"})

    return {
        "category": category, "total_score": 5, "layer1_score": 2, "layer2_score": 2, "layer3_score": 1,
        "drivers": drivers, "patient": patient,
    }


# ─────────────────────────────────────────────────────────────────────────
# 12. BLEU scoring — no external deps (nltk/sacrebleu not assumed present
#    offline), so this is a self-contained, standard corpus/sentence BLEU
#    (Papineni et al. 2002): modified n-gram precision (n=1..4, clipped
#    counts), brevity penalty, geometric mean of the four precisions. Ties
#    into the harness's own tokenizer convention (HarnessChunkStore._tokenize
#    uses `[a-zA-Z0-9]+`) rather than a different tokenizer, so BLEU numbers
#    are internally consistent with how retrieval/BM25 sees text here.
#
#    Smoothing: short clinical answers (many references here are one or two
#    sentences) frequently have zero 4-gram overlap even when the answer is
#    substantively correct, which would silently floor BLEU at 0.0 for the
#    whole geometric mean. `smoothing=True` (default) applies NLTK's
#    "method1" (epsilon substitution: any zero-count precision is replaced
#    with a small epsilon instead of zeroing the product) so BLEU degrades
#    gracefully instead of cliff-edging to 0 -- report both figures
#    (`bleu` and `bleu_no_smoothing` are both returned by score_bleu_pair)
#    since which one is "correct" to cite is a methods-section choice.
# ─────────────────────────────────────────────────────────────────────────

def _bleu_tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", (text or "").lower())


def _ngram_counts(tokens: list[str], n: int) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = defaultdict(int)
    for i in range(len(tokens) - n + 1):
        counts[tuple(tokens[i:i + n])] += 1
    return counts


def _modified_precision(candidate: list[str], references: list[list[str]], n: int) -> tuple[int, int]:
    """Returns (clipped_match_count, candidate_ngram_count) for order n --
    kept as raw counts (not a ratio) so corpus_bleu can sum them across
    sentences before dividing, per the standard corpus-BLEU definition
    (NOT the mean of per-sentence ratios, which is a common but incorrect
    shortcut)."""
    cand_counts = _ngram_counts(candidate, n)
    if not cand_counts:
        return 0, 0
    max_ref_counts: dict[tuple[str, ...], int] = defaultdict(int)
    for ref in references:
        ref_counts = _ngram_counts(ref, n)
        for gram, cnt in ref_counts.items():
            max_ref_counts[gram] = max(max_ref_counts[gram], cnt)
    clipped = sum(min(cnt, max_ref_counts.get(gram, 0)) for gram, cnt in cand_counts.items())
    total = sum(cand_counts.values())
    return clipped, total


def _brevity_penalty(cand_len: int, ref_lens: list[int]) -> float:
    if cand_len == 0:
        return 0.0
    # Closest reference length (standard BLEU rule; ties broken toward the
    # shorter reference, matching NLTK's closest_ref_length behavior).
    closest = min(ref_lens, key=lambda rl: (abs(rl - cand_len), rl))
    if cand_len >= closest:
        return 1.0
    if closest == 0:
        return 0.0
    return float(np.exp(1 - closest / cand_len))


def _geometric_mean_precisions(
    precisions: list[tuple[int, int]], weights: tuple[float, ...], smoothing: bool,
) -> float:
    import math
    log_sum = 0.0
    for (clipped, total), w in zip(precisions, weights):
        if total == 0:
            return 0.0  # no n-grams of this order in the candidate at all
        if clipped == 0:
            if not smoothing:
                return 0.0
            # NLTK SmoothingFunction.method1: replace a zero-count precision
            # with 1/(2*total) instead of 0, so one missing high-order
            # n-gram doesn't zero out the whole geometric mean.
            p = 1.0 / (2.0 * total)
        else:
            p = clipped / total
        log_sum += w * math.log(p)
    return float(np.exp(log_sum))


def sentence_bleu(
    candidate_text: str,
    reference_texts: list[str],
    max_n: int = 4,
    weights: tuple[float, ...] | None = None,
    smoothing: bool = True,
) -> float:
    """BLEU-N for one candidate against one or more references (usually one
    -- this dataset has a single reference_answer per question). Returns a
    score in [0, 1] (multiply by 100 for the conventional 0-100 display)."""
    weights = weights or tuple(1.0 / max_n for _ in range(max_n))
    candidate = _bleu_tokenize(candidate_text)
    references = [_bleu_tokenize(r) for r in reference_texts]
    if not candidate or not any(references):
        return 0.0
    precisions = [_modified_precision(candidate, references, n) for n in range(1, max_n + 1)]
    geo_mean = _geometric_mean_precisions(precisions, weights, smoothing)
    bp = _brevity_penalty(len(candidate), [len(r) for r in references])
    return bp * geo_mean


def corpus_bleu(
    candidate_texts: list[str],
    reference_texts_list: list[list[str]],
    max_n: int = 4,
    weights: tuple[float, ...] | None = None,
    smoothing: bool = True,
) -> float:
    """Standard corpus-level BLEU: n-gram match/total counts are summed
    across ALL candidate/reference pairs before computing precision ratios
    (not averaged per-sentence), matching sacrebleu/NLTK corpus_bleu
    semantics rather than macro-averaging sentence_bleu scores."""
    weights = weights or tuple(1.0 / max_n for _ in range(max_n))
    totals = [[0, 0] for _ in range(max_n)]  # [clipped, total] per order
    cand_len_sum, ref_len_sum = 0, 0
    for cand_text, ref_texts in zip(candidate_texts, reference_texts_list):
        candidate = _bleu_tokenize(cand_text)
        references = [_bleu_tokenize(r) for r in ref_texts]
        cand_len_sum += len(candidate)
        ref_len_sum += min((len(r) for r in references), key=lambda rl: (abs(rl - len(candidate)), rl)) \
            if references else 0
        if not candidate:
            continue
        for n in range(1, max_n + 1):
            clipped, total = _modified_precision(candidate, references, n)
            totals[n - 1][0] += clipped
            totals[n - 1][1] += total
    geo_mean = _geometric_mean_precisions([tuple(t) for t in totals], weights, smoothing)
    bp = _brevity_penalty(cand_len_sum, [ref_len_sum]) if ref_len_sum else 0.0
    return bp * geo_mean


def score_bleu_pair(candidate_text: str, reference_text: str) -> dict[str, float]:
    """Convenience wrapper for the common one-candidate/one-reference case
    used by run_bleu_evaluation_harness below. Reports both the smoothed
    score (recommended headline metric for short clinical answers) and the
    unsmoothed score (the "textbook" BLEU-4, which floors to 0.0 far more
    often on 1-2 sentence references -- included for transparency, not as
    the recommended figure)."""
    return {
        "bleu": sentence_bleu(candidate_text, [reference_text], smoothing=True),
        "bleu_no_smoothing": sentence_bleu(candidate_text, [reference_text], smoothing=False),
        "bleu_1": sentence_bleu(candidate_text, [reference_text], max_n=1, smoothing=True),
        "bleu_2": sentence_bleu(candidate_text, [reference_text], max_n=2, smoothing=True),
    }


# ─────────────────────────────────────────────────────────────────────────
# 13. BLEU evaluation harness — grounded on the neonatal EOS QA eval
#    dataset (neonatal_eos_qa_eval_dataset.csv: qa_id, question,
#    reference_answer, source_guideline, category, difficulty,
#    gold_chunk_id, page, section, chunk_type). This is a *generation*-
#    level metric (does the RAG pipeline's final answer text resemble the
#    guideline-grounded reference answer), complementary to the anchor-
#    phrase retrieval metrics in §5b/§7 (did retrieval find the right
#    chunk) -- a run can retrieve the correct gold_chunk_id and still score
#    low BLEU if the LLM paraphrases heavily, or retrieve the wrong chunk
#    and still score nonzero BLEU by coincidence/prior knowledge. Both
#    numbers are reported per-item so you can see where they diverge.
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class BleuEvalItem:
    qa_id: str
    question: str
    reference_answer: str
    source_guideline: str
    category: str
    difficulty: str
    gold_chunk_id: str


def load_qa_eval_dataset(csv_path: str) -> list[BleuEvalItem]:
    """Loads neonatal_eos_qa_eval_dataset.csv (the grounded QA set) into
    BleuEvalItem records. Tolerant of extra/missing optional columns (page/
    section/chunk_type) since only the five fields above are needed for
    BLEU scoring; gold_chunk_id is kept so retrieval-hit can be reported
    alongside BLEU per item."""
    import csv as _csv
    items: list[BleuEvalItem] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            items.append(BleuEvalItem(
                qa_id=row["qa_id"],
                question=row["question"],
                reference_answer=row["reference_answer"],
                source_guideline=row.get("source_guideline", ""),
                category=row.get("category", ""),
                difficulty=row.get("difficulty", ""),
                gold_chunk_id=row.get("gold_chunk_id", ""),
            ))
    print(f"[BLEU eval] Loaded {len(items)} QA items from {csv_path}")
    return items


_QA_ANSWER_SYSTEM_PROMPT = (
    "You are a clinical evidence assistant answering neonatal early-onset sepsis (EOS) "
    "questions for a paediatric clinician. Answer ONLY using the retrieved guideline "
    "excerpts provided below -- do not use outside knowledge, and do not invent numbers, "
    "durations, or thresholds that are not present in the excerpts. If the excerpts do not "
    "contain the answer, say so explicitly. Respond with a concise, direct answer of 1-4 "
    "sentences in plain prose -- no markdown, no preamble, no repeating the question."
)


def build_qa_answer_prompt(question: str, chunks: list[ChunkResult]) -> str:
    chunk_block = "\n\n".join(
        f"[{c.chunk_id}] {c.source_name} — {c.section}:\n{c.chunk_text}" for c in chunks
    ) or "(no guideline chunks were retrieved)"
    return f"""QUESTION: {question}

RETRIEVED GUIDELINE EXCERPTS:
{chunk_block}

Answer the question in 1-4 sentences, grounded only in the excerpts above."""


def generate_rag_answer_harness(
    item: BleuEvalItem,
    store: HarnessChunkStore,
    reranker,
    call_llm: Callable,
    top_k: int = 4,
    retrieval_top_k: int = 10,
    use_mmr: bool = True,
) -> tuple[str, list[ChunkResult]]:
    """Runs the SAME retrieval pipeline as RQ1/RQ2 (retrieve_evidence_harness
    -- hybrid BM25+dense -> RRF -> rerank -> guideline nudge -> MMR) using
    the QA item's question as the query and its source_guideline as the
    active guideline, then asks call_llm for a short grounded prose answer
    (not the full care-plan JSON schema used elsewhere in this harness --
    a plain-text answer is what BLEU against reference_answer needs).
    Returns (answer_text, retrieved_chunks) so callers can score both
    generation (BLEU) and retrieval (gold_chunk_id hit) from one call.
    """
    chunks = retrieve_evidence_harness(
        store, item.question, item.source_guideline, reranker,
        top_k=top_k, retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
    )
    prompt = build_qa_answer_prompt(item.question, chunks)
    try:
        raw, _model = call_llm(prompt, system=_QA_ANSWER_SYSTEM_PROMPT)
        answer = raw.strip()
        # Mock/JSON-only providers (make_call_llm(mock=True), or a real
        # provider that ignores the plain-prose instruction and wraps its
        # answer in JSON anyway) shouldn't silently corrupt BLEU scoring
        # with raw JSON text -- best-effort unwrap a couple of common shapes
        # before falling back to using the raw text as-is.
        if answer.startswith("{"):
            try:
                parsed = json.loads(strip_json_fences(answer))
                for key in ("answer", "clinical_summary", "text"):
                    if isinstance(parsed.get(key), str) and parsed[key]:
                        answer = parsed[key]
                        break
            except (json.JSONDecodeError, AttributeError):
                pass
    except LLMUnavailableError as e:
        answer = ""
        print(f"[BLEU eval] {item.qa_id}: LLM unavailable ({e}) — scoring empty answer (BLEU=0)")
    return answer, chunks


def run_bleu_evaluation_harness(
    qa_items: list[BleuEvalItem],
    store: HarnessChunkStore,
    reranker,
    call_llm: Callable,
    top_k: int = 4,
    retrieval_top_k: int = 10,
    use_mmr: bool = True,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Runs the RAG pipeline over every item in qa_items and scores each
    generated answer against reference_answer with BLEU. Also records
    whether gold_chunk_id was among the retrieved chunks for that question,
    so you can see BLEU broken out by retrieval-hit vs retrieval-miss (a
    low BLEU with a correct retrieval hit points at a generation/
    paraphrasing problem; a low BLEU with a retrieval miss points at a
    retrieval problem instead -- conflating the two is the most common
    mistake in RAG BLEU reporting).

    Returns one dict per item (see keys below); pass the result straight to
    summarize_bleu_results() or a pandas.DataFrame for further slicing.
    """
    results: list[dict[str, Any]] = []
    t0 = time.time()
    for i, item in enumerate(qa_items, start=1):
        answer, chunks = generate_rag_answer_harness(
            item, store, reranker, call_llm,
            top_k=top_k, retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
        )
        scores = score_bleu_pair(answer, item.reference_answer)
        retrieved_ids = {c.chunk_id for c in chunks}
        retrieval_hit = item.gold_chunk_id in retrieved_ids if item.gold_chunk_id else None
        row = {
            "qa_id": item.qa_id,
            "source_guideline": item.source_guideline,
            "category": item.category,
            "difficulty": item.difficulty,
            "gold_chunk_id": item.gold_chunk_id,
            "retrieval_hit": retrieval_hit,
            "retrieved_chunk_ids": sorted(retrieved_ids),
            "question": item.question,
            "reference_answer": item.reference_answer,
            "generated_answer": answer,
            **scores,
        }
        results.append(row)
        if verbose:
            hit_str = "HIT " if retrieval_hit else ("miss" if retrieval_hit is False else " n/a")
            print(f"[{i:>3}/{len(qa_items)}] {item.qa_id:<14} retrieval={hit_str}  "
                  f"BLEU={scores['bleu']:.3f}  ({time.time() - t0:.0f}s elapsed)")
    return results


def summarize_bleu_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates run_bleu_evaluation_harness() output: corpus-level BLEU
    (the statistically preferred aggregate -- NOT a mean of per-item BLEU
    scores), mean sentence-level BLEU for comparison, and breakdowns by
    source_guideline / difficulty / retrieval_hit. Prints a compact report
    and returns the same numbers as a dict for programmatic use (e.g.
    writing to the paper's results table)."""
    if not results:
        print("[BLEU eval] No results to summarize.")
        return {}

    candidates = [r["generated_answer"] for r in results]
    references = [[r["reference_answer"]] for r in results]
    corpus_score = corpus_bleu(candidates, references)
    mean_sentence_bleu = float(np.mean([r["bleu"] for r in results]))

    def _group_mean(key: str) -> dict[str, float]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in results:
            groups[str(r.get(key))].append(r["bleu"])
        return {g: round(float(np.mean(v)), 4) for g, v in sorted(groups.items())}

    hits = [r["bleu"] for r in results if r["retrieval_hit"] is True]
    misses = [r["bleu"] for r in results if r["retrieval_hit"] is False]
    n_hit = sum(1 for r in results if r["retrieval_hit"] is True)
    n_miss = sum(1 for r in results if r["retrieval_hit"] is False)

    summary = {
        "n_items": len(results),
        "corpus_bleu": round(corpus_score, 4),
        "mean_sentence_bleu": round(mean_sentence_bleu, 4),
        "bleu_by_source_guideline": _group_mean("source_guideline"),
        "bleu_by_difficulty": _group_mean("difficulty"),
        "bleu_by_category": _group_mean("category"),
        "retrieval_hit_rate": round(n_hit / (n_hit + n_miss), 4) if (n_hit + n_miss) else float("nan"),
        "mean_bleu_when_retrieval_hit": round(float(np.mean(hits)), 4) if hits else float("nan"),
        "mean_bleu_when_retrieval_miss": round(float(np.mean(misses)), 4) if misses else float("nan"),
    }

    print("\n" + "=" * 60)
    print(f"BLEU evaluation summary — {summary['n_items']} questions")
    print("=" * 60)
    print(f"Corpus BLEU:              {summary['corpus_bleu']:.4f}")
    print(f"Mean sentence BLEU:       {summary['mean_sentence_bleu']:.4f}")
    print(f"Retrieval hit rate:       {summary['retrieval_hit_rate']:.4f}")
    print(f"  mean BLEU | hit:        {summary['mean_bleu_when_retrieval_hit']:.4f}  (n={n_hit})")
    print(f"  mean BLEU | miss:       {summary['mean_bleu_when_retrieval_miss']:.4f}  (n={n_miss})")
    print("BLEU by source guideline:", summary["bleu_by_source_guideline"])
    print("BLEU by difficulty:      ", summary["bleu_by_difficulty"])
    print("=" * 60)
    return summary


# ─────────────────────────────────────────────────────────────────────────
# 14. METEOR + BERTScore generation evaluation — same (question, RAG
#    pipeline, reference_answer) triples as §13's BLEU harness above,
#    extended with two more generation-quality metrics scored on the SAME
#    generated answers (one RAG pass per item, reused for all three
#    metric families -- this module never re-generates per metric):
#
#      - METEOR (nltk): unigram precision/recall with stemming + WordNet
#        synonym matching, more forgiving of clinical paraphrase than
#        BLEU's strict n-gram overlap. Needs the nltk 'wordnet'/'omw-1.4'
#        corpora, downloaded lazily on first call (or up front in the
#        notebook's §0.3 install cell).
#      - BERTScore (Zhang et al. 2020, `bert-score` pkg): contextual-
#        embedding P/R/F1 between candidate and reference, computed here
#        with THREE backbones so a paper can report whichever one fits its
#        compute budget without re-running the whole pipeline:
#            "roberta-large"           -- bert_score's own default scorer;
#                                          strongest human-correlation in
#                                          the original paper; slowest /
#                                          most memory (~1.3 GB).
#            "distilbert-base-uncased" -- ~40% of RoBERTa-large's params;
#                                          fast CPU-friendly sanity-check
#                                          arm (~0.3 GB).
#            "microsoft/deberta-large" -- disentangled-attention encoder;
#                                          in Zhang et al.'s own updated
#                                          backbone rankings this is
#                                          typically the strongest-
#                                          correlating BERTScore backbone,
#                                          ahead of roberta-large (~0.9 GB).
#
#    DIVERGENCE: BERTScore's paper baseline-rescales scores per backbone/
#    language for readability; this harness reports RAW P/R/F1
#    (rescale_with_baseline=False) so the three backbones stay on the same
#    absolute 0-1 scale and are directly comparable to each other -- do not
#    compare these raw numbers to rescaled BERTScore figures published
#    elsewhere without accounting for that difference.
# ─────────────────────────────────────────────────────────────────────────

_METEOR_READY = False


def _ensure_meteor_deps() -> None:
    """Lazily downloads the WordNet corpora METEOR's synonym matching
    needs. No-ops after the first successful call in a process."""
    global _METEOR_READY
    if _METEOR_READY:
        return
    import nltk
    for corpus, path in (("wordnet", "corpora/wordnet"), ("omw-1.4", "corpora/omw-1.4")):
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(corpus, quiet=True)
    _METEOR_READY = True


def score_meteor_pair(candidate_text: str, reference_text: str) -> float:
    """Single-pair METEOR score (nltk.translate.meteor_score.meteor_score).
    Returns 0.0 for an empty candidate (e.g. an LLM-unavailable row from
    generate_rag_answer_harness) instead of raising, matching score_bleu_pair's
    treatment of the same case."""
    if not candidate_text or not candidate_text.strip():
        return 0.0
    if not reference_text or not reference_text.strip():
        return 0.0
    _ensure_meteor_deps()
    from nltk.translate.meteor_score import meteor_score
    cand_tokens = _bleu_tokenize(candidate_text)
    ref_tokens = _bleu_tokenize(reference_text)
    if not cand_tokens or not ref_tokens:
        return 0.0
    return float(meteor_score([ref_tokens], cand_tokens))


BERTSCORE_BACKBONES: dict[str, str] = {
    "roberta-large": "roberta-large",
    "distilbert-base-uncased": "distilbert-base-uncased",
    "deberta-large": "microsoft/deberta-large",
}


def score_bertscore_batch(
    candidates: list[str],
    references: list[str],
    model_type: str,
    device: str | None = None,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Batched BERTScore for ONE backbone across the whole candidate/
    reference list. Always call this once per backbone over ALL items in a
    single batch -- never per item in a loop: bert_score's IDF weighting
    and internal batching both operate corpus-wide, so per-item calls
    silently degrade both score quality (no real IDF) and speed (no
    batching) with no error raised to warn you.

    Empty candidates (LLM-unavailable rows) are swapped for a single space
    before scoring, since bert_score raises on a fully empty string; their
    P/R/F1 are then forced to 0.0 in the returned arrays afterwards so an
    LLM outage doesn't get silently averaged in as if it were a real (bad)
    answer.
    """
    from bert_score import score as _bert_score_fn
    import torch as _torch

    if device is None:
        device = "cuda" if _torch.cuda.is_available() else "cpu"

    empty_mask = [not (c and c.strip()) for c in candidates]
    safe_candidates = [" " if e else c for e, c in zip(empty_mask, candidates)]

    P, R, F1 = _bert_score_fn(
        cands=safe_candidates,
        refs=references,
        model_type=model_type,
        device=device,
        batch_size=batch_size,
        rescale_with_baseline=False,
        verbose=False,
    )
    P, R, F1 = P.tolist(), R.tolist(), F1.tolist()
    for i, empty in enumerate(empty_mask):
        if empty:
            P[i] = R[i] = F1[i] = 0.0

    return {
        "model_type": model_type,
        "precision": float(np.mean(P)) if P else float("nan"),
        "recall": float(np.mean(R)) if R else float("nan"),
        "f1": float(np.mean(F1)) if F1 else float("nan"),
        "per_item_precision": P,
        "per_item_recall": R,
        "per_item_f1": F1,
    }


def run_generation_evaluation_harness(
    qa_items: list[BleuEvalItem],
    store: HarnessChunkStore,
    reranker,
    call_llm: Callable,
    top_k: int = 4,
    retrieval_top_k: int = 10,
    use_mmr: bool = True,
    bertscore_backbones: dict[str, str] | None = None,
    bertscore_device: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Runs the SAME RAG pipeline as run_bleu_evaluation_harness (retrieval
    via retrieve_evidence_harness -> generation via generate_rag_answer_harness)
    exactly once per item, then scores each generated answer against
    reference_answer with BLEU, METEOR, and BERTScore across
    bertscore_backbones (default BERTSCORE_BACKBONES: RoBERTa-large /
    DistilBERT / DeBERTa).

    Returns {"per_item": [...], "bertscore": {backbone_label: {...}}}.
    BERTScore is computed once per backbone in a single batched call over
    ALL items (see score_bertscore_batch) -- this function generates every
    answer first and only scores BERTScore afterwards, so a slow LLM
    provider doesn't get interleaved with model-loading time for three
    separate transformer backbones.
    """
    if bertscore_backbones is None:
        bertscore_backbones = BERTSCORE_BACKBONES

    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for i, item in enumerate(qa_items, start=1):
        answer, chunks = generate_rag_answer_harness(
            item, store, reranker, call_llm,
            top_k=top_k, retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
        )
        bleu_scores = score_bleu_pair(answer, item.reference_answer)
        meteor = score_meteor_pair(answer, item.reference_answer)
        retrieved_ids = {c.chunk_id for c in chunks}
        retrieval_hit = item.gold_chunk_id in retrieved_ids if item.gold_chunk_id else None
        rows.append({
            "qa_id": item.qa_id,
            "source_guideline": item.source_guideline,
            "category": item.category,
            "difficulty": item.difficulty,
            "gold_chunk_id": item.gold_chunk_id,
            "retrieval_hit": retrieval_hit,
            "retrieved_chunk_ids": sorted(retrieved_ids),
            "question": item.question,
            "reference_answer": item.reference_answer,
            "generated_answer": answer,
            "meteor": meteor,
            **bleu_scores,
        })
        if verbose:
            hit_str = "HIT " if retrieval_hit else ("miss" if retrieval_hit is False else " n/a")
            print(f"[{i:>3}/{len(qa_items)}] {item.qa_id:<14} retrieval={hit_str}  "
                  f"BLEU={bleu_scores['bleu']:.3f}  METEOR={meteor:.3f}  "
                  f"({time.time() - t0:.0f}s elapsed)")

    candidates = [r["generated_answer"] for r in rows]
    references = [r["reference_answer"] for r in rows]

    bertscore_results: dict[str, Any] = {}
    for label, model_name in bertscore_backbones.items():
        print(f"[BERTScore] {label} ({model_name}) -- scoring {len(candidates)} pairs…")
        try:
            res = score_bertscore_batch(candidates, references, model_name, device=bertscore_device)
            bertscore_results[label] = res
            print(f"  P={res['precision']:.4f}  R={res['recall']:.4f}  F1={res['f1']:.4f}")
        except Exception as e:
            print(f"  FAILED ({label}): {e}")
            bertscore_results[label] = {
                "model_type": model_name, "precision": float("nan"), "recall": float("nan"),
                "f1": float("nan"), "per_item_precision": [], "per_item_recall": [], "per_item_f1": [],
            }

    for label, res in bertscore_results.items():
        safe_label = label.replace("-", "_").replace(".", "_")
        per_f1 = res.get("per_item_f1") or []
        for i, row in enumerate(rows):
            row[f"bertscore_f1_{safe_label}"] = per_f1[i] if i < len(per_f1) else float("nan")

    return {"per_item": rows, "bertscore": bertscore_results}


def summarize_generation_results(eval_out: dict[str, Any]) -> dict[str, Any]:
    """Aggregates run_generation_evaluation_harness() output: corpus-level
    BLEU, mean METEOR, and per-backbone BERTScore P/R/F1, each broken out
    by source_guideline / difficulty / category / retrieval_hit. Prints a
    compact report and returns the same numbers as a dict for programmatic
    use (e.g. writing to the paper's results table)."""
    rows = eval_out.get("per_item", [])
    if not rows:
        print("[Generation eval] No results to summarize.")
        return {}

    candidates = [r["generated_answer"] for r in rows]
    references = [[r["reference_answer"]] for r in rows]
    corpus_bleu_score = corpus_bleu(candidates, references)
    mean_meteor = float(np.mean([r["meteor"] for r in rows]))

    def _group_mean(key: str, value_key: str) -> dict[str, float]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            v = r.get(value_key)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                groups[str(r.get(key))].append(v)
        return {g: round(float(np.mean(v)), 4) for g, v in sorted(groups.items()) if v}

    n_hit = sum(1 for r in rows if r["retrieval_hit"] is True)
    n_miss = sum(1 for r in rows if r["retrieval_hit"] is False)

    summary: dict[str, Any] = {
        "n_items": len(rows),
        "corpus_bleu": round(corpus_bleu_score, 4),
        "mean_meteor": round(mean_meteor, 4),
        "meteor_by_source_guideline": _group_mean("source_guideline", "meteor"),
        "meteor_by_difficulty": _group_mean("difficulty", "meteor"),
        "meteor_by_category": _group_mean("category", "meteor"),
        "retrieval_hit_rate": round(n_hit / (n_hit + n_miss), 4) if (n_hit + n_miss) else float("nan"),
        "bertscore": {},
    }

    print("\n" + "=" * 72)
    print(f"Generation-axis evaluation summary — {summary['n_items']} questions")
    print("=" * 72)
    print(f"Corpus BLEU:        {summary['corpus_bleu']:.4f}")
    print(f"Mean METEOR:        {summary['mean_meteor']:.4f}")
    print(f"Retrieval hit rate: {summary['retrieval_hit_rate']:.4f}  (n_hit={n_hit}, n_miss={n_miss})")
    print("-" * 72)
    for label, res in eval_out.get("bertscore", {}).items():
        summary["bertscore"][label] = {
            "precision": round(res["precision"], 4),
            "recall": round(res["recall"], 4),
            "f1": round(res["f1"], 4),
        }
        print(f"BERTScore [{label:<24}] P={res['precision']:.4f}  R={res['recall']:.4f}  F1={res['f1']:.4f}")
    print("=" * 72)
    return summary


# Example wiring (not executed on import):
#
#   chunks = load_corpus_from_csv("chunks_clinical_v2.csv")
#   store = HarnessChunkStore(chunks, embed_fn=your_embed_fn)
#   reranker = your_cross_encoder  # e.g. CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
#   call_llm = make_call_llm(mock=True)  # or gemini/groq/local kwargs for a real run
#
#   qa_items = load_qa_eval_dataset("neonatal_eos_qa_eval_dataset.csv")
#
#   results = run_bleu_evaluation_harness(qa_items, store, reranker, call_llm, top_k=4)
#   summary = summarize_bleu_results(results)
#
#   eval_out = run_generation_evaluation_harness(qa_items, store, reranker, call_llm, top_k=4)
#   gen_summary = summarize_generation_results(eval_out)
#
#   import pandas as pd
#   pd.DataFrame(eval_out["per_item"]).to_csv("generation_eval_results.csv", index=False)
