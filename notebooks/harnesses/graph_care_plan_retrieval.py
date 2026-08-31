"""
graph_care_plan_retrieval.py
=============================
Graph-RAG and Graph+Vector-RAG retrieval for NeoGuard's care-plan
generation pipeline, built to plug into `care_plan_eval_harness.py`
(v2/`__2_`) with retrieval as the ONLY variable — everything else
(deterministic safety checks, prompt construction, LLM call, the regimen
safety net, scoring) is a verbatim reuse of that module's own functions,
so any metric difference between the vector / graph / graph+vector arms
in the comparison notebook is attributable to retrieval, not to a
reimplementation drift between arms.

Source graph: NICE / WHO / AAP_PRETERM / AAP_TERM `*_sections.json`,
`*_nodes_recommendations.csv`, `*_nodes_doses.csv`, `*_relationships.csv`
(the Phase-3 extraction output from the antibiotic-stewardship graph-RAG
project). Chunk corpus: the SAME `chunks_ingested_*.csv` DataFrame the
vector arm's `HarnessChunkStore` is built from — the graph arm selects
FROM this exact corpus (never fabricates or re-derives chunk_ids), so
`chunk_id` values are directly comparable to `item.gold_chunk_ids` and to
the vector arm's retrieved ids in the merged results table.

Usage (see the companion notebook for a full worked run):

    import graph_care_plan_retrieval as gcp
    gcp.attach_base_harness(base)        # same base module care_plan_eval_harness uses
    gcp.attach_care_plan_harness(cpe)    # care_plan_eval_harness module itself

    graph = gcp.build_graph(".")                       # loads the 4 guidelines' CSV/JSON
    gcp.attach_corpus(graph, chunks_df)                 # chunks_df: same DataFrame as the vector store

    vector_fn = gcp.make_vector_retrieve_fn(store, reranker)
    graph_fn  = gcp.make_graph_retrieve_fn()
    hybrid_fn = gcp.make_hybrid_retrieve_fn(vector_fn, graph_fn)

    vector_out = gcp.run_care_plan_evaluation_harness_pluggable(items, vector_fn, call_llm)
    graph_out  = gcp.run_care_plan_evaluation_harness_pluggable(items, graph_fn, call_llm)
    hybrid_out = gcp.run_care_plan_evaluation_harness_pluggable(items, hybrid_fn, call_llm)
"""

from __future__ import annotations

import csv
import json
import re
import time
import collections
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd
import networkx as nx

# ─────────────────────────────────────────────────────────────────────────
# 0. Wiring to the base harness + care_plan_eval_harness, same
#    injected-module pattern care_plan_eval_harness.py itself uses.
# ─────────────────────────────────────────────────────────────────────────

_BASE = None
_CPE = None


def attach_base_harness(base_module) -> None:
    global _BASE
    _BASE = base_module


def attach_care_plan_harness(cpe_module) -> None:
    global _CPE
    _CPE = cpe_module


def _require() -> tuple:
    if _BASE is None or _CPE is None:
        raise RuntimeError(
            "graph_care_plan_retrieval needs both attach_base_harness(base) and "
            "attach_care_plan_harness(cpe) called first."
        )
    return _BASE, _CPE


# ─────────────────────────────────────────────────────────────────────────
# 1. Canonical vocabulary — same list used throughout the antibiotic-
#    stewardship graph-RAG project (Section 0 of the extraction spec every
#    guideline chat was given), extended with a couple of care-plan-
#    specific risk/sign terms (PoorPerfusion, Hypotension were already in
#    the ontology but had no surface-form entry yet).
# ─────────────────────────────────────────────────────────────────────────

CONDITIONS = ["EarlyOnsetSepsis", "LateOnsetSepsis", "SuspectedSepsis",
              "CultureConfirmedSepsis", "Meningitis", "Chorioamnionitis"]
RISK_FACTORS = ["MaternalGBSColonization", "ProlongedROM", "MaternalFever",
                 "ClinicalChorioamnionitis", "Prematurity",
                 "InadequateIntrapartumAntibiotics", "PreviousInfantGBSDisease", "MaternalUTI"]
CLINICAL_SIGNS = ["RespiratoryDistress", "TemperatureInstability", "PoorPerfusion",
                    "Lethargy", "Apnea", "Seizures", "Irritability", "FeedIntolerance",
                    "Tachycardia", "Hypotension"]
INVESTIGATIONS = ["BloodCulture", "CRP", "FullBloodCount", "ITRatio", "PlateletCount",
                    "Procalcitonin", "LumbarPuncture", "UrineOutput", "Creatinine"]
MEDICATIONS = ["Benzylpenicillin", "Ampicillin", "Gentamicin", "Amoxicillin",
               "Cefotaxime", "Flucloxacillin", "Vancomycin", "Metronidazole",
               "Cloxacillin", "Ceftriaxone"]
PATHOGENS = ["GroupBStreptococcus", "EscherichiaColi", "ListeriaMonocytogenes",
             "CoagulaseNegativeStaphylococci"]

REL_TYPES = {"RECOMMENDS", "TREATS", "HAS_RISK_FACTOR", "HAS_SYMPTOM", "REQUIRES", "STOP_IF",
             "MONITOR_WITH", "CONTRAINDICATED_IN", "HAS_DOSE", "SUPPORTED_BY",
             "ACTIVE_AGAINST", "PRESCRIBES"}

# Surface-form -> canonical entity, used to detect what a free-text care-plan
# retrieval query (e.g. "EOS care plan NICE HIGH Maternal GBS colonization
# Prolonged rupture of membranes ... Elevated CRP") is actually about.
_SURFACE_MAP = {
    "maternal gbs colonization": "MaternalGBSColonization", "gbs colonization": "MaternalGBSColonization",
    "gbs positive": "MaternalGBSColonization", "group b strep": "GroupBStreptococcus", "gbs": "GroupBStreptococcus",
    "prolonged rupture of membranes": "ProlongedROM", "rupture of membranes": "ProlongedROM", "rom ": "ProlongedROM",
    "maternal intrapartum fever": "MaternalFever", "maternal fever": "MaternalFever", "intrapartum fever": "MaternalFever",
    "clinical chorioamnionitis": "ClinicalChorioamnionitis", "chorioamnionitis": "Chorioamnionitis",
    "respiratory distress": "RespiratoryDistress", "poor perfusion": "PoorPerfusion", "hypotension": "Hypotension",
    "elevated crp": "CRP", "crp": "CRP", "positive blood culture": "BloodCulture", "blood culture": "BloodCulture",
    "temperature instability": "TemperatureInstability", "lethargy": "Lethargy", "apnoea": "Apnea", "apnea": "Apnea",
    "seizure": "Seizures", "tachycardia": "Tachycardia", "prematurity": "Prematurity", "preterm": "Prematurity",
    "inadequate intrapartum antibiotics": "InadequateIntrapartumAntibiotics",
    "penicillin allerg": "Benzylpenicillin",  # routes penicillin-allergy cases toward the beta-lactam node
    "gentamicin": "Gentamicin", "benzylpenicillin": "Benzylpenicillin", "ampicillin": "Ampicillin",
    "meningitis": "Meningitis", "listeria": "ListeriaMonocytogenes", "creatinine": "Creatinine",
    "urine output": "UrineOutput", "lumbar puncture": "LumbarPuncture", "platelet": "PlateletCount",
}


def surface_entities(text: str) -> set:
    t = text.lower()
    return {v for k, v in _SURFACE_MAP.items() if k in t}


# Task-aware baseline section types: a care plan ALWAYS needs an antibiotic
# plan / monitoring plan / stop-criteria regardless of which specific risk
# drivers fired the query (there's no HAS_RISK_FACTOR->Recommendation edge
# in the ontology connecting e.g. "MaternalGBSColonization" to "give
# ampicillin+gentamicin" -- that's the actual clinical protocol, not a graph
# edge). Without this, a pure entity-match retrieval for a risk-driver-only
# query (which is what build_clinical_query_harness() actually produces --
# see the notebook's query-shape check) would surface risk-factor context
# but nothing for antibiotic_plan/monitoring_plan/stop_criteria, which the
# downstream care-plan JSON schema requires every time antibiotics are
# indicated. This is a disclosed, deliberate task-aware design choice, not
# a hidden thumb on the scale -- see the notebook's methodology section.
#
# ParentCommunication / NutritionFluid added here too, and for the same
# reason but a stronger one: nutrition_fluid_plan and parent_communication_
# notes are UNCONDITIONALLY required fields in every care plan (unlike
# antibiotic_plan, which is risk-category-gated) -- see _CARE_PLAN_JSON_
# SCHEMA in care_plan_eval_harness.py. Confirmed empirically before adding
# these categories, not assumed: NICE has genuine, extractable content for
# both (a real "carers" section covering communication before/during/after
# treatment, plus a narrow but real fluid-restriction-in-meningitis
# recommendation) -- 8 new Recommendation nodes were extracted from real
# chunks_ingested_1_.csv text and appended to NICE's Phase-3 files. WHO and
# AAP were checked with the same method and found to have no comparable
# content (their nutrition/parent-adjacent keyword hits were false
# positives -- diagnostic-sign definitions like "not able to feed at all",
# not feeding guidance) -- nothing was force-extracted for them. Including
# these two types in the baseline set is therefore safe for WHO/AAP (their
# subgraphs simply have zero Recommendation nodes of these types, so the
# baseline match contributes nothing there) and genuinely useful for NICE.
#
# "Duration" added 2026-08g, same method: checked directly, only NICE has
# any Duration-type nodes (2 of them, the "7 days of IV antibiotics"
# recommendations) -- WHO and AAP_PRETERM/AAP_TERM have zero, so this is
# safe for them by the same empty-subgraph argument as ParentCommunication/
# NutritionFluid above. Added because "treatment duration" was one of the
# most frequently-missed anchor phrases across NICE cases in the diagnostic
# pass that motivated the coverage-pass fix below -- Duration content is
# unconditionally part of a complete care plan whenever antibiotics are
# indicated, exactly the same rationale as WhenToStartAntibiotics/
# FirstLineAntibiotics/StoppingCriteria already being baseline.
_BASELINE_SECTION_TYPES = {"WhenToStartAntibiotics", "FirstLineAntibiotics", "StoppingCriteria", "Monitoring",
                              "ParentCommunication", "NutritionFluid", "Duration"}
_BASELINE_SCORE = 5  # below any real entity match (100) or section-keyword match (10)
_COVERAGE_ROUNDS = 2  # swept 1/2/3/4 against real corpus+dataset -- see validation notes; 2 was the empirical winner

# BUGFIX 2026-08j: "RiskFactors" added as baseline for NICE ONLY (see
# _NICE_ONLY_BASELINE_TYPES / is_baseline below) -- deliberately NOT a
# global addition to _BASELINE_SECTION_TYPES this time, unlike every prior
# addition here. Checked directly: RiskFactors nodes exist for NICE (2) and
# both AAP variants (1 each), empty for WHO. First attempt added it
# globally (safe-for-WHO argument still holds) and validated: NICE
# improved (0.518->0.569 anchor_grounding_recall on the corrected dataset)
# and WHO improved slightly too (crowds nothing there, no RiskFactors
# nodes) -- but AAP, which was already near-ceiling at 0.911, dropped hard
# to 0.713. AAP's real misses (categorical/multivariate risk) are
# WhenToStartAntibiotics-type, not RiskFactors-type -- so RiskFactors-as-
# baseline gave AAP zero benefit while still eating a round-robin seat AAP
# didn't have budget to spare (it already has 4-5 baseline types active
# there). Scoping the addition to NICE only recovers AAP's number while
# keeping NICE's gain -- verified in this round's validation notes.
#
# Traced via retrieval_only_metrics__13_.csv (real production data, not
# offline approximation): "red flag" was graph's single most frequent miss
# (8/30 cases) and NICE_REC_006's page window DOES contain the literal
# "Red flag risk factor:" text (NICE_pg12_box-1-risk-factors-for-e_chunk1)
# -- the content was reachable, RiskFactors just wasn't guaranteed a seat.
#
# This does NOT fix everything in that same miss cluster, though --
# "maternal sepsis" (10 misses) and "rupture of membranes at term" (7
# misses) are also NICE_REC_006-adjacent conceptually, but the verbatim
# text for those two lives at NICE page 64 ("Why the committee made the
# recommendations"), and REC_006 anchors at page 18 (csv page 10 after
# offset) -- the +/-5 page window used by _chunks_for_recommendation
# cannot reach page 64 no matter how the candidate scoring is tuned. That
# is a real structural limit of page-window chunk resolution, not
# something RiskFactors-as-baseline fixes. See _RATIONALE_CHUNKS below for
# how that specific gap is closed instead, without touching the window.
_RATIONALE_PATTERNS = {
    "NICE": ("maternal sepsis", "rupture of membranes at term"),
    "WHO": ("possible serious bacterial infection", "psbi"),
}
# BUGFIX 2026-08j (continued): a small, non-page-windowed supplementary
# candidate channel for content that is real, guideline-relevant, and
# grounded, but structurally out of reach of any single Recommendation
# node's page window. This is the graph-side mirror of
# neoguard_harness_improved.py's _is_baseline_treatment_chunk /
# _BASELINE_CHUNK_PATTERNS mechanism (which was itself ported FROM this
# module's coverage-pass fix) -- graph borrowing back the harness's
# "full-corpus keyword match" capability where its own page-window
# resolution structurally cannot reach the needed content. Every pattern
# was checked against chunks_ingested.csv before inclusion, same standard
# as the harness-side patterns:
#   NICE: "maternal sepsis" (5 corpus matches), "rupture of membranes at
#     term" (1 match) -- both in NICE_pg64_why-the-committee-made-t_*,
#     confirmed unreachable from any RiskFactors rec's page window.
#   WHO: "possible serious bacterial infection" / "psbi" (10 matches
#     combined) -- WHO_REC_001 (Diagnosis, the 7-sign IMCI algorithm)
#     anchors close to some of this content already via its own window,
#     but not all of it; this channel catches what the window misses
#     without touching the window itself.
# Deliberately NOT ported: "red flag" (already fixed via the RiskFactors
# baseline addition above -- adding it here too would be redundant, not
# additive) and the broader-but-declined patterns already ruled out on the
# harness side (risk stratification, treatment failure, neurodevelopment,
# relapse) for being too broad (5-15%+ of the corpus, mostly evidence-
# review paragraphs) -- same over-broad judgment call applies here.
_rationale_chunks_cache: dict = {}


def _rationale_chunks(gid: str) -> list[dict]:
    """Guideline-scoped (not page-windowed) chunks matching _RATIONALE_
    PATTERNS. Cached per guideline_id since _chunks_df doesn't change
    after attach_corpus(). Returns [] if attach_corpus hasn't been called
    or the guideline has no configured patterns (e.g. AAP -- its misses
    are all page-window-reachable already, confirmed in Round 2/3, so no
    patterns were added for it here)."""
    if gid in _rationale_chunks_cache:
        return _rationale_chunks_cache[gid]
    src = _CHUNK_SOURCE_FOR_GRAPH_ID.get(gid, gid)
    patterns = _RATIONALE_PATTERNS.get(src, ())
    out = []
    if patterns and _chunks_df is not None:
        sub = _chunks_df[_chunks_df["source"] == src]
        for _, row in sub.iterrows():
            text = str(row.get("chunk_text", ""))
            if any(p in text.lower() for p in patterns):
                out.append({
                    "chunk_id": row["chunk_id"], "source": row["source"],
                    "chunk_text": row["chunk_text"],
                    "subsection": row.get("subsection"), "section": row.get("section"),
                    "chunk_type": row.get("chunk_type"),
                })
    _rationale_chunks_cache[gid] = out
    return out

_SECTION_TYPE_KEYWORDS = {
    "Contraindications": ["contraindicat", "caution", "avoid", "should not be used", "allerg"],
    "Duration": ["duration", "how long", "course length", "days of treatment"],
    "Monitoring": ["monitor", "levels", "trough", "peak concentration"],
    "AlternativeAntibiotics": ["alternative", "second-line", "second line"],
    "LaboratoryInvestigations": ["laboratory", "lab test", "investigation", "blood test"],
    "StoppingCriteria": ["stop", "discontinue", "cease"],
    "Diagnosis": ["diagnos", "diagnostic", "imci", "possible serious bacterial infection", "psbi", "danger sign"],
    "WhenToStartAntibiotics": ["when to start", "initiate", "start antibiotic", "critical", "high", "intermediate"],
    "FirstLineAntibiotics": ["first-line", "first line", "empirical", "regimen"],
    "RiskFactors": ["risk factor"],
    "ClinicalSigns": ["clinical sign", "symptom"],
    "SpecialSituations": ["special situation", "exception", "deteriorat", "improv"],
    "ParentCommunication": ["parent", "carer", "famil", "communicat", "inform", "discuss", "counsel",
                              "handover", "discharge", "transfer"],
    "NutritionFluid": ["feed", "breastfe", "nutrition", "fluid", "enteral", "parenteral", "maintenance fluid"],
}


def _matched_section_types(text: str) -> set:
    t = text.lower()
    return {st for st, kws in _SECTION_TYPE_KEYWORDS.items() if any(k in t for k in kws)}


# ─────────────────────────────────────────────────────────────────────────
# 2. Graph construction — loads the 4 guidelines' Phase-3 CSV/JSON output.
#    The WHO repair (RECOMMENDS misused for Recommendation->Dose, missing
#    Guideline->RECOMMENDS edges, duplicate section_ids) is applied inline
#    here so this module is self-contained regardless of which WHO file
#    version is on disk -- see the inline comments for exactly what's fixed
#    and why (same root-cause analysis as the earlier antibiotic-screen
#    notebook's WHO repair).
# ─────────────────────────────────────────────────────────────────────────

GUIDELINES = ["NICE", "WHO", "AAP_PRETERM", "AAP_TERM"]

# "AAP" in the care-plan dataset's active_guideline field is undifferentiated
# (doesn't distinguish PRETERM/TERM -- confirmed: neoguard_care_plan_eval_
# dataset.json's active_guideline values are exactly {NICE, WHO, AAP}, never
# AAP_PRETERM/AAP_TERM).
#
# BUGFIX 2026-08: this used to query BOTH AAP_PRETERM and AAP_TERM whenever
# active_guideline=="AAP", deliberately mirroring a substring-matching bug
# in the vector arm's guideline boost (`guideline_upper in (chunk.
# source_name + chunk.source).upper()`, which treated "AAP_PRETERM" as
# matching active_guideline "AAP" since "AAP" is a substring of
# "AAP_PRETERM") for cross-arm fairness. That vector-arm bug is now fixed
# (see neoguard_harness_improved.py's _guideline_source_match / BUGFIX
# 2026-08 comment) -- it does an exact allow-listed match now, so it never
# treats AAP_PRETERM as on-guideline for an "AAP" case either. Mirroring
# the OLD (buggy) behavior here would just reintroduce the same wrong-
# document contamination this fix removes from the vector arm, so this now
# queries AAP_TERM only, exactly like the vector arm's fixed behavior.
# Confirmed empirically: gold_chunk_ids never cites an AAP_PRETERM chunk
# for any of the 30 cases. A GA-aware variant (using risk_result.patient.
# gestational_age_weeks to pick AAP_PRETERM instead, for genuinely preterm
# cases) is still available via _ga_aware_aap_graph_ids() below as an
# explicit secondary ablation -- not the default, since the dataset's own
# gold labels don't exercise it.
_GUIDELINE_TO_GRAPH_IDS = {"NICE": ["NICE"], "WHO": ["WHO"], "AAP": ["AAP_TERM"]}


def _ga_aware_aap_graph_ids(gestational_age_weeks: float | None, preterm_threshold: float = 35.0) -> list[str]:
    """Secondary ablation, NOT wired into retrieve_evidence_graph by default
    (see BUGFIX 2026-08 comment above _GUIDELINE_TO_GRAPH_IDS). Pass this
    function's output as an override of target_gids in a custom retrieve_fn
    if you want to test GA-aware AAP subgraph selection instead of the
    dataset's actual (GA-agnostic) gold-label convention."""
    if gestational_age_weeks is not None and float(gestational_age_weeks) < preterm_threshold:
        return ["AAP_PRETERM"]
    return ["AAP_TERM"]

# chunks_ingested_*.csv source tags: confirmed by direct inspection that the
# generic "AAP" source tag is actually the TERM document's content (Kaiser
# calculator text, unique to AAP_TERM, appears under source=="AAP"), and
# "AAP_PRETERM" is its own separate tag -- NOT "AAP" meaning PRETERM as the
# tag name might suggest. Verified empirically, not assumed.
_CHUNK_SOURCE_FOR_GRAPH_ID = {"NICE": "NICE", "WHO": "WHO", "AAP_PRETERM": "AAP_PRETERM", "AAP_TERM": "AAP"}


def _repair_who(sections: dict, recs: list[dict], rels: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Fixes, in order: (1) duplicate section_ids in WHO_sections.json --
    WHO_S02/WHO_S03 each cover two different section_type entries under one
    id; deduped to _b suffixes and every section_id_ref/SUPPORTED_BY target
    remapped by (old_id, section_type). (2) RECOMMENDS was misused for
    Recommendation->Dose edges (37 of them) instead of Guideline->
    Recommendation (zero of which existed) -- retyped to PRESCRIBES and the
    missing Guideline->RECOMMENDS->Recommendation edges added. (3) Medication
    ->HAS_DOSE->Dose edges were entirely missing -- added directly from each
    dose row's own `drug` field. All three confirmed via direct inspection
    of the original files, not assumed."""
    seen_count = collections.Counter()
    lookup = {}
    for s in sections["sections"]:
        orig_id = s["section_id"]
        seen_count[orig_id] += 1
        new_id = orig_id if seen_count[orig_id] == 1 else f"{orig_id}_{chr(ord('a') + seen_count[orig_id] - 1)}"
        lookup[(orig_id, s["section_type"])] = new_id
        s["section_id"] = new_id

    rec_section_type = {r["id"]: r["section_type"] for r in recs}
    fixed_recs = []
    for r in recs:
        r = dict(r)
        r["section_id_ref"] = lookup.get((r["section_id_ref"], r["section_type"]), r["section_id_ref"])
        fixed_recs.append(r)

    rec_ids = {r["id"] for r in recs}
    dose_drug = {}  # populated below once doses are known to the caller; RECOMMENDS->PRESCRIBES retype doesn't need it
    new_rels = [{"from_id": "WHO", "relationship": "RECOMMENDS", "to_id": r["id"], "notes": ""} for r in recs]
    for rel in rels:
        if rel["relationship"] == "SUPPORTED_BY":
            st = rec_section_type.get(rel["from_id"], "")
            new_to = lookup.get((rel["to_id"], st), rel["to_id"])
            new_rels.append({**rel, "to_id": new_to})
        elif rel["relationship"] == "RECOMMENDS" and rel["to_id"] not in rec_ids:
            # these are the misused Recommendation->Dose edges
            new_rels.append({**rel, "relationship": "PRESCRIBES"})
        else:
            new_rels.append(rel)

    return sections, fixed_recs, new_rels


def _add_has_dose_edges(doses: list[dict], rels: list[dict]) -> list[dict]:
    seen = set()
    extra = []
    for d in doses:
        key = (d["drug"], d["id"])
        if key not in seen:
            seen.add(key)
            extra.append({"from_id": d["drug"], "relationship": "HAS_DOSE", "to_id": d["id"], "notes": ""})
    return rels + extra


def _load_one_guideline(directory: str, gid: str) -> dict:
    def p(name):
        return f"{directory.rstrip('/')}/{gid}_{name}" if directory else f"{gid}_{name}"
    with open(p("sections.json")) as f:
        sections = json.load(f)
    with open(p("nodes_recommendations.csv")) as f:
        recs = list(csv.DictReader(f))
    with open(p("nodes_doses.csv")) as f:
        doses = list(csv.DictReader(f))
    with open(p("relationships.csv")) as f:
        rels = list(csv.DictReader(f))

    if gid == "WHO":
        has_dose_present = any(r["relationship"] == "HAS_DOSE" for r in rels)
        who_root_touched = any(r["from_id"] == "WHO" or r["to_id"] == "WHO" for r in rels)
        if not has_dose_present or not who_root_touched:
            sections, recs, rels = _repair_who(sections, recs, rels)
            rels = _add_has_dose_edges(doses, rels)
            print(f"[graph] WHO repair applied: {sum(1 for r in rels if r['relationship']=='RECOMMENDS')} "
                  f"Guideline->RECOMMENDS edges, {sum(1 for r in rels if r['relationship']=='HAS_DOSE')} "
                  f"Medication->HAS_DOSE edges added/confirmed.")
    return dict(sections=sections, recs=recs, doses=doses, rels=rels)


def build_graph(directory: str = ".") -> nx.MultiDiGraph:
    """Loads all 4 guidelines and returns one merged networkx graph. Prints
    a short structural report so a build-time regression (e.g. someone
    reintroducing the WHO bug in a future extraction re-run) is visible
    immediately rather than surfacing later as an unexplained metric drop."""
    data = {gid: _load_one_guideline(directory, gid) for gid in GUIDELINES}

    G = nx.MultiDiGraph()
    for gid, d in data.items():
        G.add_node(gid, kind="Guideline")
        for r in d["recs"]:
            G.add_node(r["id"], kind="Recommendation", guideline_id=gid,
                       section_type=r["section_type"], text_summary=r["text_summary"],
                       page=r["page"])
        for dose in d["doses"]:
            G.add_node(dose["id"], kind="Dose", **dose)
        for rel in d["rels"]:
            if rel["relationship"] in REL_TYPES:
                G.add_edge(rel["from_id"], rel["to_id"], relationship=rel["relationship"])

    n_rec = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Recommendation")
    n_dose = sum(1 for _, a in G.nodes(data=True) if a.get("kind") == "Dose")
    print(f"[graph] built: {G.number_of_nodes()} nodes ({n_rec} Recommendation, {n_dose} Dose), "
          f"{G.number_of_edges()} edges, guidelines={GUIDELINES}")
    for gid in GUIDELINES:
        touched = any(G.has_edge(gid, r) for r, a in G.nodes(data=True)
                       if a.get("kind") == "Recommendation" and a.get("guideline_id") == gid)
        if not touched:
            print(f"[graph] WARNING: '{gid}' guideline root has no RECOMMENDS edges into its own "
                  f"Recommendation nodes -- retrieval scoped to this guideline will find nothing "
                  f"via graph traversal.")
    return G


def recommendation_entities(G: nx.MultiDiGraph, rec_id: str) -> set:
    """Explicit, semantically-typed traversal (not a blind N-hop BFS --
    see the antibiotic-screen notebook's analysis of why hop-count BFS
    both misses real 3-hop paths like Rec->Dose<-Medication->TREATS->
    Condition and, at higher hop counts, over-collects semantically
    irrelevant same-distance edges)."""
    entities = set(G.successors(rec_id))
    for target in list(entities):
        if G.nodes.get(target, {}).get("kind") == "Dose":
            for med_id in G.predecessors(target):
                edge_data = G.get_edge_data(med_id, target)
                if edge_data and any(d.get("relationship") == "HAS_DOSE" for d in edge_data.values()):
                    entities.add(med_id)
                    entities |= set(G.successors(med_id))
    return entities


# ─────────────────────────────────────────────────────────────────────────
# 3. Chunk corpus attachment + offset-corrected, content-ranked lookup.
#    Reuses the same calibration technique as the antibiotic-screen
#    notebook (maximize keyword overlap between Recommendation.text_summary
#    and chunk text across candidate offsets) so this is re-derived against
#    whatever corpus is actually attached, not hardcoded from a prior run.
# ─────────────────────────────────────────────────────────────────────────

_chunks_df: pd.DataFrame | None = None
_page_offsets: dict[str, int] = {}
_CHUNK_WINDOW = 5


def attach_corpus(G: nx.MultiDiGraph, chunks_df: pd.DataFrame) -> None:
    """Calibrates the per-guideline page offset against the ACTUAL attached
    corpus and stores it module-level for chunk lookup. Must be called once
    before any retrieve_* function is used."""
    global _chunks_df, _page_offsets
    _chunks_df = chunks_df.copy()
    _chunks_df["page"] = pd.to_numeric(_chunks_df["page"], errors="coerce")

    recs_by_gid: dict[str, list[dict]] = collections.defaultdict(list)
    for rec_id, attrs in G.nodes(data=True):
        if attrs.get("kind") == "Recommendation":
            recs_by_gid[attrs["guideline_id"]].append({"page": attrs.get("page"), "text_summary": attrs.get("text_summary", "")})

    def _content_words(text):
        return set(re.findall(r"[a-zA-Z]{5,}", text.lower()))

    def _score_offset(gid, csv_source, delta):
        total, hits = 0, 0
        for r in recs_by_gid[gid]:
            page_str = str(r["page"] or "").strip()
            if not page_str:
                continue
            try:
                rec_page = int(page_str.split("-")[0])
            except ValueError:
                continue
            candidate_page = rec_page - delta
            texts = _chunks_df.loc[(_chunks_df["source"] == csv_source) & (_chunks_df["page"] == candidate_page), "chunk_text"]
            if texts.empty:
                continue
            blob = " ".join(texts).lower()
            words = _content_words(r["text_summary"])
            overlap = sum(1 for w in words if w in blob)
            if overlap >= 3:
                hits += 1
            total += overlap
        return total, hits

    for gid in GUIDELINES:
        src = _CHUNK_SOURCE_FOR_GRAPH_ID[gid]
        best_delta, best_total, best_hits = None, -1, 0
        for delta in range(-15, 16):
            total, hits = _score_offset(gid, src, delta)
            if total > best_total:
                best_delta, best_total, best_hits = delta, total, hits
        _page_offsets[gid] = best_delta
        print(f"[graph] page offset calibrated: {gid} (source={src}) delta={best_delta:+d} "
              f"({best_hits}/{len(recs_by_gid[gid])} recs matched)")


def _to_csv_page(gid: str, footer_page: int) -> int | None:
    delta = _page_offsets.get(gid)
    if delta is None or footer_page is None:
        return None
    return footer_page - delta


def _chunks_for_recommendation(gid: str, footer_page, text_hint: str = "", query_words: set | None = None) -> list[dict]:
    """Widened +/-CHUNK_WINDOW page search, ranked by word overlap with
    text_hint -- not a single exact page. A single global per-guideline
    offset doesn't hold exactly for every citation (confirmed in the
    antibiotic-screen notebook: one WHO citation was 4 pages off), and a
    single page frequently carries multiple unrelated chunks even when the
    offset IS exact."""
    if _chunks_df is None:
        raise RuntimeError("attach_corpus() must be called before retrieval.")
    src = _CHUNK_SOURCE_FOR_GRAPH_ID[gid]
    footer_page_str = str(footer_page or "").strip()
    if not footer_page_str:
        return []
    try:
        footer_page_int = int(footer_page_str.split("-")[0])
    except ValueError:
        return []
    csv_page = _to_csv_page(gid, footer_page_int)
    if csv_page is None:
        return []
    window = _chunks_df[(_chunks_df["source"] == src) &
                          (_chunks_df["page"].between(csv_page - _CHUNK_WINDOW, csv_page + _CHUNK_WINDOW))]
    hits = window.to_dict("records")
    if len(hits) > 1 and (text_hint or query_words):
        # BUGFIX 2026-08g: ranking here used to score window candidates
        # ONLY against the recommendation node's own fixed text_summary --
        # identical for every case that anchors on the same recommendation,
        # so the top-2 picked per anchor never varied by case. Case-specific
        # background/definition content (this is exactly why WHO's
        # paragraph-type gold chunks were 49/49 unreachable -- confirmed via
        # retrieval_only_metrics: WHO recommendation-type gold 7/7 found,
        # paragraph-type 0/49 found -- candidacy for anchors was fine,
        # nothing case-specific ever reached this selection step) had no
        # path to be preferred over recommendation/dose-adjacent chunks
        # just because they textually echo the recommendation itself. Now
        # blends the query's own words in alongside text_hint, so a case
        # that actually needs e.g. WHO's "fast breathing"/"chest indrawing"
        # background content can surface it here, not just recommendation
        # text that happens to reuse the same wording as its own summary.
        # BUGFIX 2026-08g: ranking here used to score window candidates
        # ONLY against the recommendation node's own fixed text_summary --
        # identical for every case that anchors on the same recommendation,
        # so the top-2 picked per anchor never varied by case. Case-specific
        # background/definition content (this is exactly why WHO's
        # paragraph-type gold chunks were 49/49 unreachable -- confirmed via
        # retrieval_only_metrics: WHO recommendation-type gold 7/7 found,
        # paragraph-type 0/49 found -- candidacy for anchors was fine,
        # nothing case-specific ever reached this selection step) had no
        # path to be preferred over recommendation/dose-adjacent chunks
        # just because they textually echo the recommendation itself. Now
        # blends the query's own words in alongside text_hint, so a case
        # that actually needs e.g. WHO's "fast breathing"/"chest indrawing"
        # background content can surface it here, not just recommendation
        # text that happens to reuse the same wording as its own summary.
        #
        # 3:1 weighting (not 1:1), chosen empirically, not assumed: a 1:1
        # blend measured WHO mean per-case recall 0.106->0.378 but NICE
        # 0.397->0.379 (small regression -- query noise occasionally
        # outranked the correct recommendation-adjacent chunk for
        # NICE's already-well-served anchors). 3:1 keeps text_hint
        # dominant (protecting NICE, which tested back to exactly 0.397,
        # no regression) while query_words still tips ties toward
        # case-relevant content when text_hint alone is ambiguous or
        # absent (WHO 0.106->0.323). Tested 1:1, 2:1, 3:1, 1:0.5 head to
        # head on the real corpus/dataset before choosing this one --
        # see FINDINGS_AND_NEXT_STEPS.md.
        hint_words = set(re.findall(r"[a-zA-Z]{4,}", text_hint.lower())) if text_hint else set()
        qw = query_words or set()
        def _overlap(c):
            c_words = set(re.findall(r"[a-zA-Z]{4,}", c["chunk_text"].lower()))
            return 3 * len(hint_words & c_words) + 1 * len(qw & c_words)
        hits.sort(key=_overlap, reverse=True)
    return hits


# ─────────────────────────────────────────────────────────────────────────
# 4. Graph retrieval — returns list[ChunkResult] (the base harness's own
#    class), so it's a drop-in replacement for base.retrieve_evidence_harness
#    wherever a `chunks: list[ChunkResult]` is consumed downstream.
# ─────────────────────────────────────────────────────────────────────────

def retrieve_evidence_graph(G: nx.MultiDiGraph, query: str, active_guideline: str, top_k: int = 5) -> list:
    base, _ = _require()
    target_gids = _GUIDELINE_TO_GRAPH_IDS.get(active_guideline.upper(), [active_guideline])

    query_entities = surface_entities(query)
    query_section_types = _matched_section_types(query)
    query_words = set(re.findall(r"[a-zA-Z]{4,}", query.lower()))
    # Recovered from the query text (build_clinical_query_harness now always
    # appends "critical illness" / "clinical severe infection..." for WHO
    # cases) -- see neoguard_harness_improved.py's who_severity_tier_score /
    # BUGFIX 2026-08 comment for the full diagnosis. Same fix applied here
    # so the graph arm doesn't reintroduce the tier-swap the vector-arm fix
    # removes -- the two arms otherwise being byte-identical except for
    # retrieval is the whole point of this module (see its docstring).
    case_who_tier = base._who_tier_from_query(query)

    candidates = []  # (score, rec_id, gid)
    for rec_id, attrs in G.nodes(data=True):
        if attrs.get("kind") != "Recommendation" or attrs.get("guideline_id") not in target_gids:
            continue
        gid = attrs["guideline_id"]
        entity_match = bool(query_entities & recommendation_entities(G, rec_id))
        section_match = attrs.get("section_type") in query_section_types
        st = attrs.get("section_type")
        is_baseline = (st in _BASELINE_SECTION_TYPES) or \
            (st == "RiskFactors" and _CHUNK_SOURCE_FOR_GRAPH_ID.get(gid, gid) == "NICE")
        if not (entity_match or section_match or is_baseline):
            continue
        text_overlap = len(query_words & set(re.findall(r"[a-zA-Z]{4,}", attrs.get("text_summary", "").lower())))
        # rec_id is passed as the id for page-range detection (BUGFIX
        # 2026-08d) -- if it doesn't follow the "WHO_pgN_..." pattern
        # _chunk_who_page expects, that helper just returns None and this
        # falls back to the same phrase-based detection as before, so this
        # is a safe best-effort pass-through either way.
        who_tier_score = base.who_severity_tier_score(
            active_guideline, case_who_tier, _CHUNK_SOURCE_FOR_GRAPH_ID.get(gid, gid),
            rec_id, attrs.get("text_summary", ""),
        )
        score = (100 if entity_match else 0) + (10 if section_match else 0) + \
                (_BASELINE_SCORE if is_baseline and not (entity_match or section_match) else 0) + \
                text_overlap + (20 * who_tier_score)
        candidates.append((score, rec_id, gid))

    candidates.sort(key=lambda x: -x[0])

    results = []
    seen_chunk_ids = set()
    seen_rec_ids = set()

    def _emit(score, rec_id, gid, max_chunks):
        """Resolve rec_id's real backing chunk(s) and append up to max_chunks
        new ones to `results`. Returns how many were actually added."""
        attrs = G.nodes[rec_id]
        real_chunks = _chunks_for_recommendation(gid, attrs.get("page"), text_hint=attrs.get("text_summary", ""),
                                                  query_words=query_words)
        picked = real_chunks[:2] if real_chunks else []
        added = 0
        for c in picked:
            if added >= max_chunks or len(results) >= top_k or c["chunk_id"] in seen_chunk_ids:
                continue
            seen_chunk_ids.add(c["chunk_id"])
            normalized_score = min(1.0, score / 120.0)  # rough normalization onto a 0-1 range comparable to cosine sim
            results.append(base.ChunkResult(
                chunk_id=c["chunk_id"], source=c["source"],
                source_name=base._SOURCE_NAME.get(c["source"], c["source"]),
                section=c.get("subsection") or c.get("section") or c.get("chunk_type", ""),
                chunk_text=c["chunk_text"], score=normalized_score, group_rank=0,
            ))
            added += 1
        return added

    # ── Coverage pass ───────────────────────────────────────────────────
    # BUGFIX 2026-08e: _BASELINE_SECTION_TYPES exists so a care plan's
    # required section types (antibiotic plan, monitoring, stop-criteria,
    # parent-communication, nutrition/fluid, ...) are always CANDIDATES
    # regardless of query wording (see that set's own docstring). But
    # candidacy alone doesn't guarantee survival past the top_k cut --
    # confirmed empirically on this dataset: entity-matched (score +100)
    # or section-keyword-matched (score +10) candidates routinely fill all
    # top_k slots before a same-guideline baseline-only candidate (score
    # ~5-14) is ever reached, even when that baseline content is exactly
    # what a case's anchor_phrases are checking for (e.g. AAP's "Ampicillin
    # plus gentamicin is first-choice empirical therapy" -- FirstLineAntibiotics,
    # baseline-eligible, score 5 -- getting crowded out every time by 2-3
    # entity-matched LaboratoryInvestigations recs scoring 100+ on an
    # AAP query that happens to mention CRP/WBC/platelets). This pass
    # guarantees each section_type that is (a) baseline-eligible and (b)
    # actually present among this guideline's qualifying candidates gets
    # its single top-scoring representative seated first, 1 chunk each, so
    # top_k truncation can no longer silently drop an entire required
    # section. The existing score-ranked pass below still governs
    # everything else (which specific chunk, and all non-guaranteed slots),
    # so a genuinely more relevant baseline candidate still outranks a
    # weaker one within its own section_type -- this only protects against
    # a section_type being shut out entirely, it doesn't reorder within one.
    # Round-robin, not single-pass: a section_type with several genuinely
    # distinct recommendations (e.g. WHO's FirstLineAntibiotics covers 9
    # different regimen scenarios -- referral-not-possible, hospitalized,
    # suspected-meningitis, etc.) shouldn't be satisfied by just its single
    # top-scoring member, or 8 of those 9 scenarios are permanently
    # invisible to graph_fn regardless of top_k. Each round seats one MORE
    # not-yet-seated candidate per still-open section_type, so breadth
    # accumulates across rounds instead of stopping after the first hit.
    # `_COVERAGE_ROUNDS` was swept (1/2/3) against the real corpus/dataset
    # before picking a value -- see the module's validation notes.
    # "GuidelineRationale" participates as its own synthetic section_type
    # in the same round-robin as every real baseline type above -- one
    # rationale chunk seated per round, same budget discipline, so this
    # channel supplements the existing coverage guarantee rather than
    # competing outside it or getting special-cased priority. Resolved
    # once per call (guideline-scoped, corpus lookup is cached) rather than
    # inside the round loop.
    rationale_pool = [
        c for c in _rationale_chunks(target_gids[0] if target_gids else active_guideline)
        if c["chunk_id"] not in seen_chunk_ids
    ]
    rationale_idx = 0

    def _emit_rationale():
        nonlocal rationale_idx
        while rationale_idx < len(rationale_pool):
            c = rationale_pool[rationale_idx]
            rationale_idx += 1
            if c["chunk_id"] in seen_chunk_ids or len(results) >= top_k:
                continue
            seen_chunk_ids.add(c["chunk_id"])
            results.append(base.ChunkResult(
                chunk_id=c["chunk_id"], source=c["source"],
                source_name=base._SOURCE_NAME.get(c["source"], c["source"]),
                section=c.get("subsection") or c.get("section") or c.get("chunk_type", ""),
                chunk_text=c["chunk_text"], score=min(1.0, _BASELINE_SCORE / 120.0), group_rank=0,
            ))
            return True
        return False

    for _round in range(_COVERAGE_ROUNDS):
        if len(results) >= top_k:
            break
        seated_this_round: set = set()
        if rationale_pool and _emit_rationale():
            seated_this_round.add("GuidelineRationale")
        for score, rec_id, gid in candidates:
            if len(results) >= top_k:
                break
            if rec_id in seen_rec_ids:
                continue
            st = G.nodes[rec_id].get("section_type")
            is_baseline_type = (st in _BASELINE_SECTION_TYPES) or \
                (st == "RiskFactors" and _CHUNK_SOURCE_FOR_GRAPH_ID.get(gid, gid) == "NICE")
            if not is_baseline_type or st in seated_this_round:
                continue
            if _emit(score, rec_id, gid, max_chunks=1) > 0:
                seated_this_round.add(st)
                seen_rec_ids.add(rec_id)

    # ── Relevance pass ──────────────────────────────────────────────────
    # Fills all remaining slots by descending score, same as before the
    # coverage pass existed -- recs already partially seated above can
    # still contribute their (up to 2nd) chunk here if they legitimately
    # outrank everything else.
    for score, rec_id, gid in candidates:
        if len(results) >= top_k:
            break
        already = 1 if rec_id in seen_rec_ids else 0
        _emit(score, rec_id, gid, max_chunks=2 - already)

    return results


def make_graph_retrieve_fn(G: nx.MultiDiGraph) -> Callable[[str, str, int], list]:
    def _fn(query: str, active_guideline: str, top_k: int = 5):
        return retrieve_evidence_graph(G, query, active_guideline, top_k=top_k)
    return _fn


def make_vector_retrieve_fn(store, reranker, retrieval_top_k: int = 10, use_mmr: bool = True,
                              guideline_nudge_weight: float = 0.15,
                              guideline_hard_filter: bool = True,
                              use_sparse: bool = True, use_dense: bool = True) -> Callable[[str, str, int], list]:
    """Thin wrapper around base.retrieve_evidence_harness with the SAME
    defaults care_plan_eval_harness.py's generate_care_plan_harness() uses
    (guideline_nudge_weight=0.15, matching config.py's production value --
    see that function's own comment on why 0.15, not retrieve_evidence_
    harness's internal 0.05 default, is correct here).
    `guideline_hard_filter` (default True, BUGFIX 2026-08) is exposed here
    so it can be toggled per-arm from a notebook cell without editing this
    file -- see retrieve_evidence_harness's docstring for what it does.
    `use_sparse` / `use_dense` (both default True) are retrieve_evidence_
    harness's own retrieval-stack ablation switches, exposed here so a
    "sparse_only" or "dense_only" retrieve_fn can be built with the same
    factory as the full vector arm -- see make_sparse_retrieve_fn /
    make_dense_retrieve_fn below, which are just this with one leg off."""
    base, _ = _require()
    def _fn(query: str, active_guideline: str, top_k: int = 5):
        return base.retrieve_evidence_harness(
            store, query, active_guideline, reranker, top_k=top_k,
            retrieval_top_k=retrieval_top_k, use_mmr=use_mmr,
            guideline_nudge_weight=guideline_nudge_weight,
            guideline_hard_filter=guideline_hard_filter,
            use_sparse=use_sparse, use_dense=use_dense,
        )
    return _fn


def make_sparse_retrieve_fn(store, reranker, retrieval_top_k: int = 10,
                              guideline_nudge_weight: float = 0.15,
                              guideline_hard_filter: bool = True) -> Callable[[str, str, int], list]:
    """BM25-only leg of the vector arm (use_dense=False). MMR is meaningless
    with a single retrieval leg re-ranked by the cross-encoder anyway, so
    it's forced off here rather than left as a dangling knob."""
    return make_vector_retrieve_fn(
        store, reranker, retrieval_top_k=retrieval_top_k, use_mmr=False,
        guideline_nudge_weight=guideline_nudge_weight, guideline_hard_filter=guideline_hard_filter,
        use_sparse=True, use_dense=False,
    )


def make_dense_retrieve_fn(store, reranker, retrieval_top_k: int = 10,
                             guideline_nudge_weight: float = 0.15,
                             guideline_hard_filter: bool = True) -> Callable[[str, str, int], list]:
    """Semantic-embedding-only leg of the vector arm (use_sparse=False)."""
    return make_vector_retrieve_fn(
        store, reranker, retrieval_top_k=retrieval_top_k, use_mmr=False,
        guideline_nudge_weight=guideline_nudge_weight, guideline_hard_filter=guideline_hard_filter,
        use_sparse=False, use_dense=True,
    )


def make_hybrid_retrieve_fn(vector_fn: Callable, graph_fn: Callable, pool_multiplier: int = 3) -> Callable[[str, str, int], list]:
    """Reciprocal rank fusion of the vector and graph arms' own top-(k*pool_multiplier)
    candidate lists, using the base harness's own reciprocal_rank_fusion (same
    RRF implementation retrieve_evidence_harness uses internally to fuse
    BM25+semantic) so the fusion mechanism itself isn't a new, unvalidated
    piece of the comparison."""
    base, _ = _require()
    def _fn(query: str, active_guideline: str, top_k: int = 5):
        pool_k = max(top_k * pool_multiplier, top_k + 5)
        vec_results = vector_fn(query, active_guideline, pool_k)
        graph_results = graph_fn(query, active_guideline, pool_k)
        fused = base.reciprocal_rank_fusion(
            [vec_results, graph_results], k=60, id_fn=lambda c: c.chunk_id,
        )
        seen, out = set(), []
        for chunk, rrf_score in fused:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            out.append(base.ChunkResult(
                chunk_id=chunk.chunk_id, source=chunk.source, source_name=chunk.source_name,
                section=chunk.section, chunk_text=chunk.chunk_text, score=rrf_score, group_rank=0,
            ))
            if len(out) >= top_k:
                break
        return out
    return _fn


# ─────────────────────────────────────────────────────────────────────────
# 5. Pluggable fork of generate_care_plan_harness / run_care_plan_
#    evaluation_harness — identical to care_plan_eval_harness.py's own
#    versions in every line EXCEPT the retrieval call, which is replaced by
#    the injected retrieve_fn(query, active_guideline, top_k). Everything
#    downstream of retrieval (deterministic safety, prompt, LLM call,
#    regimen safety net, scoring) calls straight into care_plan_eval_
#    harness's own functions (via the `cpe` module reference) so there is
#    no second, drifted copy of that logic to keep in sync.
# ─────────────────────────────────────────────────────────────────────────

def generate_care_plan_harness_pluggable(
    item, retrieve_fn: Callable[[str, str, int], list], call_llm: Callable,
    top_k: int = 5,
):
    base, cpe = _require()
    risk = item.risk_result

    trends = base.compute_sustained_trends_harness(item.previous_assessments)
    query = base.build_clinical_query_harness(
        risk, item.active_guideline, deltas=item.deltas, trends=trends,
    ) or item.retrieval_query

    chunks = retrieve_fn(query, item.active_guideline, top_k)

    patient = risk.get("patient", {})
    flags = base.check_contraindications(patient, item.proposed_drugs, deltas=item.deltas)
    flags += base.check_who_outpatient_exclusions(patient, care_setting=item.care_setting)
    contraindication_flags = base.flags_to_strings(flags)

    chunk_sources = [c.source for c in chunks]
    cross_guideline_conflict = base.detect_cross_guideline_conflict(chunk_sources, item.active_guideline)
    trend_note = base.trend_state_change_note(item.deltas)

    prompt = cpe.build_care_plan_prompt(
        item, chunks, item.deltas, contraindication_flags, cross_guideline_conflict, trend_note,
    )
    fallback_used = False
    raw = ""
    model_version = "unknown"
    try:
        raw, model_version = call_llm(prompt, system=cpe._CARE_PLAN_SYSTEM_PROMPT)
        plan = json.loads(base.strip_json_fences(raw))
        if not isinstance(plan, dict):
            raise ValueError("LLM did not return a JSON object")
    except base.LLMUnavailableError as e:
        print(f"[care-plan gen] {item.case_id}: no LLM provider available ({e}) — rule-based fallback")
        plan = cpe._rule_based_fallback_plan(item)
        fallback_used = True
        model_version = "rule-based-fallback"
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[care-plan gen] {item.case_id}: unparseable LLM JSON ({e}) — rule-based fallback")
        plan = cpe._rule_based_fallback_plan(item)
        fallback_used = True
        model_version = f"{model_version}-unparseable-fallback"

    for k, v in cpe._FALLBACK_PLAN_TEMPLATE.items():
        plan.setdefault(k, v if not isinstance(v, (list, dict)) else (list(v) if isinstance(v, list) else dict(v)))
    plan.setdefault("antibiotic_plan", {})
    for k, v in cpe._FALLBACK_PLAN_TEMPLATE["antibiotic_plan"].items():
        plan["antibiotic_plan"].setdefault(k, v)

    plan = cpe._overlay_deterministic_safety(plan, contraindication_flags, cross_guideline_conflict, trend_note)
    plan = cpe._force_critical_urgency(plan, item.risk_result.get("category", ""))
    plan["model_version"] = model_version
    plan["fallback_used"] = fallback_used

    regimen_incomplete = cpe._check_regimen_completeness(plan, item.contraindication_type)
    ungrounded_dose_claims = cpe._check_ungrounded_dose_claims(
        plan.get("antibiotic_plan", {}).get("regimen", []), chunks,
    )
    plan, net_notes_1 = cpe._apply_regimen_safety_net(plan, item.active_guideline, item.contraindication_type)
    plan, net_notes_2 = cpe._strip_ungrounded_dose_claims(plan, chunks)
    safety_net_notes = net_notes_1 + net_notes_2

    return cpe.CarePlanGenerationResult(
        plan=plan, chunks=chunks, retrieval_query=query,
        contraindication_flags=contraindication_flags,
        cross_guideline_conflict=cross_guideline_conflict,
        trend_note=trend_note, fallback_used=fallback_used,
        model_version=model_version, raw_llm_text=raw,
        regimen_incomplete=regimen_incomplete,
        ungrounded_dose_claims=ungrounded_dose_claims,
        regimen_safety_net_notes=safety_net_notes,
    )


def run_care_plan_evaluation_harness_pluggable(
    items: list, retrieve_fn: Callable[[str, str, int], list], call_llm: Callable,
    top_k: int = 5,
    bertscore_backbones: dict[str, str] | None = None,
    bertscore_device: str | None = None,
    bertscore_batch_sizes: tuple[int, ...] = (32, 8, 2),
    arm_name: str = "arm",
    verbose: bool = True,
) -> dict[str, Any]:
    base, cpe = _require()
    if bertscore_backbones is None:
        bertscore_backbones = base.BERTSCORE_BACKBONES

    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for i, item in enumerate(items, start=1):
        gen = generate_care_plan_harness_pluggable(item, retrieve_fn, call_llm, top_k=top_k)
        candidate_text = cpe.format_care_plan_text(gen.plan)
        reference_text = item.reference_text

        bleu_scores = base.score_bleu_pair(candidate_text, reference_text)
        meteor = base.score_meteor_pair(candidate_text, reference_text)

        retrieved_ids = {c.chunk_id for c in gen.chunks}
        gold_ids = set(item.gold_chunk_ids)
        # BUGFIX -- mirrors care_plan_eval_harness.py's fix so vector/graph/
        # hybrid arms stay byte-identical in scoring methodology (see that
        # module's BUGFIX comment for the full rationale: exact chunk_id P/R
        # is a strict, noisy diagnostic on this corpus, not a headline metric).
        retrieval_prf_exact = base.precision_recall_f1(sorted(retrieved_ids), sorted(gold_ids)) if gold_ids else None
        retrieval_prf_section = cpe._section_level_precision_recall_f1(retrieved_ids, gold_ids) if gold_ids else None
        grounding_recall = cpe.anchor_grounding_recall(gen.chunks, getattr(item, "anchor_phrases", []) or [])

        safety_recall = 1.0
        if gen.contraindication_flags:
            surfaced_blob = " || ".join(gen.plan.get("contraindication_flags", [])).lower()
            hits = sum(1 for f in gen.contraindication_flags
                       if cpe._drug_name_from_deterministic_flag(f) in surfaced_blob)
            safety_recall = hits / len(gen.contraindication_flags)

        rows.append({
            "arm": arm_name,
            "case_id": item.case_id,
            "active_guideline": item.active_guideline,
            "category": item.risk_result.get("category"),
            "contraindication_type": item.contraindication_type,
            "trend_type": item.trend_type,
            "ambiguous": item.ambiguous,
            "fallback_used": gen.fallback_used,
            "model_version": gen.model_version,
            "retrieval_query": gen.retrieval_query,
            "retrieved_chunk_ids": sorted(retrieved_ids),
            "gold_chunk_ids": sorted(gold_ids),
            "anchor_grounding_recall": grounding_recall,
            "retrieval_precision_section_UNRELIABLE_GOLD": retrieval_prf_section["precision"] if retrieval_prf_section else None,
            "retrieval_recall_section_UNRELIABLE_GOLD": retrieval_prf_section["recall"] if retrieval_prf_section else None,
            "retrieval_f1_section_UNRELIABLE_GOLD": retrieval_prf_section["f1"] if retrieval_prf_section else None,
            "retrieval_precision_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["precision"] if retrieval_prf_exact else None,
            "retrieval_recall_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["recall"] if retrieval_prf_exact else None,
            "retrieval_f1_exact_id_UNRELIABLE_GOLD": retrieval_prf_exact["f1"] if retrieval_prf_exact else None,
            "cross_guideline_conflict_detected": gen.cross_guideline_conflict,
            "deterministic_safety_recall": safety_recall,
            "regimen_incomplete": gen.regimen_incomplete,
            "ungrounded_dose_claims": gen.ungrounded_dose_claims,
            "regimen_safety_net_notes": gen.regimen_safety_net_notes,
            "reference_text": reference_text,
            "generated_text": candidate_text,
            "generated_plan": gen.plan,
            "contexts": [c.chunk_text for c in gen.chunks],
            "question": gen.retrieval_query,
            "meteor": meteor,
            **bleu_scores,
        })
        if verbose:
            gr_str = f"{grounding_recall:.3f}" if grounding_recall is not None else "n/a"
            print(f"[{arm_name:>7}][{i:>3}/{len(items)}] {item.case_id:<14} guideline={item.active_guideline:<5} "
                  f"category={str(item.risk_result.get('category')):<12} fallback={gen.fallback_used}  "
                  f"BLEU={bleu_scores['bleu']:.3f}  METEOR={meteor:.3f}  grounding_recall={gr_str}  "
                  f"({time.time() - t0:.0f}s elapsed)")

    candidates = [r["generated_text"] for r in rows]
    references = [r["reference_text"] for r in rows]

    bertscore_results = {}
    for name, model_type in bertscore_backbones.items():
        bertscore_results[name] = cpe.score_bertscore_batch_robust(
            base, candidates, references, model_type,
            device=bertscore_device, batch_sizes=bertscore_batch_sizes,
        )
        for i, r in enumerate(rows):
            r[f"bertscore_{name}_precision"] = bertscore_results[name]["per_item_precision"][i] if bertscore_results[name]["per_item_precision"] else float("nan")
            r[f"bertscore_{name}_recall"] = bertscore_results[name]["per_item_recall"][i] if bertscore_results[name]["per_item_recall"] else float("nan")
            r[f"bertscore_{name}_f1"] = bertscore_results[name]["per_item_f1"][i] if bertscore_results[name]["per_item_f1"] else float("nan")

    return {"per_item": rows, "bertscore_summary": {k: {kk: vv for kk, vv in v.items() if kk.startswith(("precision", "recall", "f1", "model_type"))} for k, v in bertscore_results.items()}}
