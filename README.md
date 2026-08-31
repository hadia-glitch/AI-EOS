

# NeoGuard AI

**An AI-powered clinical decision support platform for Early-Onset Neonatal Sepsis (EOS) — the Neonatology module of a broader paediatric decision-support platform.**

NeoGuard AI combines a trained, calibrated, SHAP-explainable machine learning risk model with a retrieval-augmented generation (RAG) pipeline and a multi-agent LLM verification system to give clinicians personalised, evidence-grounded, and continuously-updated sepsis risk assessments — with full transparency into *why* the system reached its conclusions and *where* every recommendation comes from.

---

## Table of Contents

- [Clinical Problem](#clinical-problem)
- [Project Objectives](#project-objectives)
- [System Architecture](#system-architecture)
- [Risk Assessment Engine (ML Model)](#risk-assessment-engine-ml-model)
- [User Workflow](#user-workflow)
- [Patient Timeline & Longitudinal Record Maintenance](#patient-timeline--longitudinal-record-maintenance)
- [De-identification & Privacy](#de-identification--privacy)
- [Explainable AI](#explainable-ai)
- [Data Engineering & Guideline Ingestion Pipeline](#data-engineering--guideline-ingestion-pipeline)
- [Evidence & Literature Integration (RAG Pipeline)](#evidence--literature-integration-rag-pipeline)
- [Clinical Explanation Generation](#clinical-explanation-generation)
- [Care Plan Generation](#care-plan-generation)
- [Multi-Agent Trustworthiness Layer](#multi-agent-trustworthiness-layer)
- [Clinical Evidence Search Tab](#clinical-evidence-search-tab)
- [How This Differs From Existing EOS Calculators](#how-this-differs-from-existing-eos-calculators)
- [Technical Stack](#technical-stack)
- [Technical Competencies Demonstrated](#technical-competencies-demonstrated)
- [Evaluation Methodology](#evaluation-methodology)
- [Deployment](#deployment)
- [Scope & Limitations](#scope--limitations)
- [Roadmap](#roadmap)

---

## Clinical Problem

Early-Onset Neonatal Sepsis (EOS) remains a leading cause of neonatal morbidity and mortality worldwide. Early recognition is difficult because clinical signs are often subtle and non-specific. Current practice frequently results in either:

- **Delayed identification** of infected neonates, or
- **Unnecessary investigations and antibiotic exposure** in low-risk infants

NeoGuard AI is designed to help clinicians balance these risks by providing personalised, continuously-updating risk assessment alongside evidence-based, guideline-grounded management recommendations.

## Project Objectives

- Identify neonates at risk of Early-Onset Sepsis
- Calculate individualised, calibrated risk estimates
- Support evidence-based clinical decision-making
- Promote antibiotic stewardship
- Provide transparent, explainable recommendations — not a black box
- Continuously update risk assessment as new clinical information becomes available across an infant's course

---

## System Architecture

NeoGuard AI has four layers that compose into a single clinical request:

1. **ML Risk Assessment Engine** — a trained, calibrated, SHAP-explainable model produces the individualised risk score and category. This is treated as ground truth by everything downstream; the LLM layers never re-derive it.
2. **RAG retrieval layer** — hybrid search over ingested guideline corpora (NICE NG195, AAP, WHO, EOSCAL literature), producing ranked, source-diverse, page-attributed evidence for the current patient.
3. **Two-agent LLM generation layer** — an independent **generator** and **judge** produce and verify clinical explanations and care plans grounded in that retrieved evidence.
4. **Deterministic resolution/safety-net layer** — a non-LLM control loop that actively re-searches for grounding before ever falling back to a labeled clinical default.

```
Clinical Data Entry
        │
        ▼
ML Risk Assessment Engine  ──►  risk_payload (score, category, layer breakdown, SHAP drivers)
        │
        ▼
RAG Retrieval (hybrid BM25 + semantic, multi-guideline corpus)
        │
        ▼
Generator LLM  ──►  draft explanation / care plan (evidence-labeled)
        │
        ▼
Judge LLM  ──►  fact-check against risk_payload + evidence
        │
        ▼
Resolution Agent (Care Plan only)  ──►  iterative re-ground → re-verify
        │
        ▼
Clinician-facing output, every claim traceable to source
```

---

## Risk Assessment Engine (ML Model)

The risk engine is a trained, calibrated, explainable machine learning pipeline — **not** a static rule-based calculator. It is a near drop-in for the app's existing risk payload contract, so the downstream RAG/explanation/care-plan pipeline and existing risk-driver UI need minimal changes to consume it.

### Why a trained model, not a fixed calculator

Fixed tools such as the Kaiser Permanente EOS Calculator have meaningfully improved neonatal antibiotic stewardship, but they use a static prediction model, focus predominantly on maternal/perinatal factors, generate a one-time estimate at birth, and don't continuously update as new clinical information arrives. NeoGuard's ML engine is designed to be retrained, recalibrated, and to support dynamic reassessment as an infant's clinical picture evolves.

### Development pipeline

```
Data → Leakage audit → Feature selection (Lasso + Boruta + domain-expert override)
     → Dynamic 80:20 split (×5) → Model benchmark (GBM / XGBoost / SVM / RF / Ensemble)
     → Champion selection → Isotonic calibration → Prevalence prior-correction
     → SHAP explainability → App-contract payload → Backend + on-device export
```

### Benchmarked against real published clinical research

Built using the same schema as **Kainth et al., *BMJ Paediatrics Open* 2026** (Delhi Neonatal Infection Study, n=2,924) — dual Lasso + Boruta feature selection with domain-expert override, dynamic stratified splitting, five-architecture benchmarking, and sensitivity-targeted threshold selection.

After Bayes-correcting for prevalence, the model's **PPV (26.6%)** and **NPV (94.0%)** land within 1–2 points of that published study's external-validation numbers (24.0% / 95.7%) — despite being trained on a completely different dataset. This is strong evidence the *calibration methodology itself* is sound, independent of the specific training data.

### Leakage audit

A leakage audit — cross-checked via both Lasso coefficients and Random Forest feature importance — identified and excluded a lab feature and six symptom flags that were statistical artifacts of the training data rather than genuine predictive signal. This is exactly the kind of check that prevents a model from looking strong in testing and then failing in the field.

### Performance

- **AUC: 0.809** on a fully independent holdout set
- **87.7% sensitivity** at the clinical screening threshold

### Feature set (9 features, birth/perinatal)

```
gestational_age_weeks, birth_weight_kg, maternal_age, apgar_1min, apgar_5min,
prom, maternal_infection, resuscitation_needed, is_csection
```

Lab features (`wbc_count`, `crp_mg_l`) are already scaffolded in the preprocessing pipeline (`preprocess(df, stage='B')`) for a future revision once richer serial lab data is incorporated — see [Roadmap](#roadmap).

### Output contract

The model emits a JSON payload compatible with the app's existing risk-result schema, so every downstream consumer (RAG query builder, explanation/care-plan generators, XAI driver chart) works unchanged:

```json
{
  "total_score": 51.3,
  "layer1_score": 12.1, "layer2_score": 8.4, "layer3_score": 0.0,
  "category": "INTERMEDIATE",
  "risk_category": "Intermediate",
  "probability_per_1000": 513.0,
  "below_eoscal_scope": false,
  "drivers": [
    {
      "name": "Gestational age",
      "points": -6.2,
      "reason": "Gestational age = 39.1",
      "contribution_percent": 24.3,
      "layer": 2
    }
  ],
  "combination_note": "Model: gbm, calibrated + prevalence-corrected to 41.0%.",
  "model_version": "ml-gbm-v2"
}
```

`layer1` / `layer2` / `layer3` map to Maternal / Clinical-Birth / Laboratory factor groups. `risk_category` thresholds are pending clinical sign-off before production use.

### Dual deployment

- **Backend**: the calibrated model plus the raw model (for full SHAP computation), served via `joblib`, behind a dedicated risk-assessment endpoint.
- **On-device**: a distilled logistic-regression model exported as `eos_risk_model_lite.json`, with closed-form SHAP approximation (`coefficient × (value − mean)`) and **no ML runtime dependency** — matching the app's offline-first design so risk assessment keeps working without connectivity.

---

## User Workflow

### Step 1 — Clinical Data Entry

Healthcare professionals enter structured maternal, neonatal, and laboratory information.

**Maternal parameters:** gestational age · maternal temperature · duration of rupture of membranes · GBS status · intrapartum antibiotic administration · suspected chorioamnionitis / intra-amniotic infection · mode of delivery · multiple pregnancy · maternal infection history · relevant laboratory investigations

**Neonatal parameters:** birth weight · Apgar scores · respiratory status · oxygen requirement · need for respiratory support · feeding difficulties · temperature instability · lethargy · perfusion abnormalities · hypoglycaemia · seizures

**Laboratory parameters:** complete blood count · CRP · procalcitonin · blood gas analysis · blood culture results · serial laboratory trends

### Step 2 — Risk Assessment

The ML Risk Assessment Engine (above) computes an individualised probability of EOS, a risk category, and a SHAP-based driver breakdown.

### Step 3 — Clinical Outputs

The platform generates:

**Risk score** — e.g. *"Estimated EOS Risk: 2.1 per 1000 live births"*

**Risk category** — Low · Intermediate · High · Critical

**Recommended actions** — e.g. routine observation, enhanced monitoring, blood culture, serial laboratory investigations, empirical antibiotic therapy, NICU admission

**Clinical summary** — e.g. *"The infant demonstrates increased EOS risk due to maternal pyrexia, prolonged rupture of membranes and evolving respiratory distress. Current evidence supports blood culture sampling and consideration of empirical antibiotic therapy."*

### Step 4 — Continuous Reassessment

Risk prediction evolves throughout the infant's clinical course as new information arrives — repeat clinical examination findings, oxygen requirements, feeding tolerance, and serial CRP/procalcitonin/culture trends all feed back into a fresh assessment, rather than the static, birth-only estimate produced by traditional calculators.

---

## Patient Timeline & Longitudinal Record Maintenance

Continuous reassessment (above) requires the platform to actually **maintain a longitudinal record per patient**, not just process one assessment in isolation. This is a dedicated subsystem spanning frontend and backend:

- **Timeline UI**: a dedicated patient timeline screen renders every historical assessment for an encounter in chronological order — score, category, and captured parameters at each point — so a clinician can see the trajectory, not just the current snapshot.
- **Two deliberately separate data representations**, built from the same assessment history:
  - A **compact trend summary** for retrieval queries — short phrases like *"CRP rising 3 consecutive readings"* or *"AKI stage 1"* — kept intentionally terse because diluting a search query with a paragraph of raw history measurably hurts retrieval quality.
  - A **full history summary** (up to the last 10 assessments, every captured field per entry) for LLM generation prompts, where more context genuinely helps the model narrate a real trajectory instead of a generic one.
- **Streak-aware trend computation**: rather than comparing only the last two readings, the system walks the full history backward to compute genuine streaks — consecutive CRP-rising readings, consecutive temperature-instability readings, and the current score direction (deteriorating / improving / stable) with its own streak length — surfaced both in the UI as a trend chip and fed into retrieval/generation.
- **Delta-aware clinical safety checks**: AKI staging (used by the nephrotoxicity contraindication check) is computed from a dedicated assessment-deltas view over the full history — including an explicit, documented limitation that a patient's very first-ever reading can never trigger a rise-based flag, since there's nothing yet to compare it against.
- **Offline-safe**: timeline data is cached locally so the history remains viewable without connectivity, consistent with the platform's offline-first design.

## De-identification & Privacy

No personally identifying information ever leaves the clinician's device as part of an AI request. A dedicated de-identification layer sits between the patient record and every backend/RAG/LLM call:

- A structured `DeidentifiedPatient` representation strips names, MRNs, and any other direct identifiers before a risk payload is built — only clinical parameters (gestational age, temperatures, labs, respiratory/perfusion status, birth weight, etc.) are transmitted.
- This applies uniformly across **every** AI-facing endpoint — risk explanation, care-plan generation, and evidence retrieval all operate exclusively on the de-identified payload, never on the underlying patient record.
- De-identification coverage is kept in lockstep with the clinical parameter set itself: when new fields are captured in the app (e.g. birth weight, care setting), they're added to both the local patient model and the de-identified payload together, so nothing captured on-device is silently missing from — or silently leaking beyond — what the AI layer is allowed to see.
- Positions the platform to meet HIPAA/GDPR-style data-minimisation requirements by construction, not as an after-the-fact compliance pass (see [Technical Stack](#technical-stack)).

---

## Explainable AI

Transparency is a first-class design goal, not an add-on. Every prediction ships with:

- **Key risk drivers**, ranked by contribution
- **Relative contribution** of each variable (SHAP-based)
- **Confidence level** of the prediction
- **Factors increasing risk** — e.g. maternal fever, prolonged rupture of membranes, positive GBS status, respiratory distress, laboratory abnormalities
- **Factors reducing risk** — e.g. adequate intrapartum antibiotics, well-appearing infant, normal serial examinations
- **Evidence supporting each recommendation**, traceable to a specific guideline passage

SHAP explainability is implemented across every candidate model architecture in the benchmark, and maps directly into the app's existing risk-driver visualisation (waterfall chart, per-driver detail screen) — clinicians see not just *what* the risk is, but *why*.

---

## Data Engineering & Guideline Ingestion Pipeline

The RAG layer's evidence base doesn't come from raw PDFs dumped into a vector store — it's built through a dedicated ingestion pipeline that turns messy clinical PDFs into clean, structured, citable chunks:

- **Structure-aware PDF extraction**: uses real typography metadata (font size, weight, per-line positioning) rather than regex-only heuristics, so heading detection reflects how the document actually presents itself; tables are extracted and kept structurally separate so they can be serialized as clean key:value prose instead of being flattened into noise.
- **Noise removal**: front-matter pages (ISBN/copyright/licensing/contributors), tables of contents, and repeated running headers/footers are detected and stripped — running headers/footers via cross-page frequency analysis of normalized text, not a fixed keyword list.
- **Paragraph reconstruction before filtering**: PDF line-wraps are rejoined into real paragraphs *before* any length-based filtering runs — an ordering fix for a real bug class where naive short-line filters silently discard most genuine content, since wrapped PDF lines are typically 30–50 characters.
- **Content-based document classification**: source guideline, region, and version/era metadata are inferred by sniffing document content with a confidence score, rather than trusting filenames.
- **Configurable chunking strategies**: fixed-size, semantic, proposition-level, and a custom clinical-structure-aware adaptive strategy — selectable per-ingestion run to support retrieval-quality ablation experiments.
- **Vector index management**: embeddings generated at ingestion time, stored in Postgres via `pgvector`, with a dedicated rebuild path for the IVFFlat approximate-nearest-neighbour index after bulk re-ingestion.

---

## Evidence & Literature Integration (RAG Pipeline)

The platform uses Retrieval-Augmented Generation to ground every recommendation in real, citable guideline text, drawn from:

- NICE Neonatal Infection Guidelines (NG195)
- American Academy of Pediatrics recommendations
- WHO guidance
- Peer-reviewed neonatal sepsis literature (EOSCAL cohort studies)
- Locally uploaded institutional protocols

### How retrieval works

- **Hybrid search**: BM25 (lexical) + dense semantic embeddings, merged via Reciprocal Rank Fusion, then cross-encoder reranked with MMR diversity to avoid returning near-duplicate passages
- **Patient-aware querying**: a structured query is built from the full clinical parameter set (not just headline symptoms), enriched with streak-aware trend descriptors from prior assessments (e.g. *"CRP rising 3 consecutive readings"*) when a patient's history is available
- **Clinical synonym expansion**: a curated vocabulary layer bridges plain clinical phrasing and formal guideline wording (e.g. "newborn blood infection" ↔ "early-onset neonatal sepsis")
- **Multi-guideline support**: the active guideline (NICE / AAP / WHO / local institutional) is a soft reranking preference, never a hard filter — the full corpus stays searchable, so a fact missing from one guideline can still be found and cited from another
- **Chunk → full section reassembly**: retrieved fragments are reassembled into complete guideline sections (not shown as disconnected snippets), with the specific matched passages highlighted in context
- **Direct source jump**: every citation carries file and page metadata, opening directly in an in-app PDF viewer at the exact highlighted passage

---

## Clinical Explanation Generation

Triggered from **"View Clinical Explanation"** — a lighter-weight, single-pass flow for explaining a specific risk result:

1. Evidence retrieved and labeled with short citation tags (`E1`, `E2`, …) — never raw internal identifiers
2. A generator LLM produces a clinical summary, a per-driver explanation for each risk factor, recommended actions, and an evidence summary — every specific claim required to cite its evidence label
3. Output is sanitised — any hallucinated citation ID is dropped, any stray raw identifier the model prints anyway is rewritten back to a proper citation tag
4. A fact-checking judge LLM runs (for High/Critical risk) and flags unsupported claims for clinician review
5. If no LLM provider is reachable, a fully deterministic, evidence-linked fallback explanation is generated instead — always clearly labeled as such

---

## Care Plan Generation

Triggered from **"AI Care Plan"** — a structured, 13-section management plan, generated and verified through a deeper, iterative pipeline:

**Sections**: patient development summary (with trend across prior assessments) · immediate actions · antibiotic plan (with explicit conditional branches for culture-negative / culture-positive / labs-unavailable pathways) · monitoring schedule · escalation criteria · driver breakdown · guideline sources · nutrition & fluid plan · parent communication notes · disambiguation notes · contraindication flags · trend-state change

### Retrieve-first, default-last

Sections that used to be static hardcoded content (monitoring schedule, escalation criteria, nutrition/fluid guidance) are now actively grounded: the system first checks already-retrieved evidence prioritising the active guideline, then runs several genuinely distinct widened searches (including across other guidelines) if nothing is found, and only falls back to a clearly-labeled standard-practice default after retrieval has been legitimately exhausted.

### Deterministic clinical safety nets

Independent of the LLM layer, deterministic rule checks run on every plan:

- Penicillin-allergy substitution logic
- KDIGO-staged AKI nephrotoxicity checks (real staged criteria, not a flat threshold)
- Antibiotic regimen completeness — flags a first-line agent present without its expected partner
- Cross-guideline conflict detection with explicit disclosure to the clinician

---

## Multi-Agent Trustworthiness Layer

This is the core of how NeoGuard AI keeps generated clinical content grounded rather than hallucinated:

- **Generator agent** drafts the explanation or care plan, citing every specific claim to a retrieved evidence passage
- **Judge agent** is architecturally independent — a separate prompt and call, explicitly told it did not author the draft — and cross-checks specific factual claims (drug doses, thresholds, causal statements) against the deterministic risk result and the retrieved evidence, distinguishing material checkable claims from reasonable stylistic synthesis
- **Resolution agent** (Care Plan only) takes whatever the judge flags and actively tries to fix it — re-running retrieval, not restricted to the active guideline — before ever reaching for a hardcoded, transparently-labeled default; it then re-invokes the judge to re-verify, looping until clean or until a bounded number of attempts is exhausted
- **Shared citation registry** assigns stable evidence labels across an entire generation run, including chunks discovered only during the resolution loop, so every citation the clinician can see — no matter which stage produced it — resolves to a real, verifiable source

---

## Clinical Evidence Search Tab

A standalone exploration tool over the same guideline corpus, for clinicians who want to search the literature directly rather than review a generated plan:

- Free-text or patient-context-biased search over the full multi-guideline corpus
- Retrieved chunks reassembled into complete, readable guideline sections with matched passages highlighted
- Optional AI overview and per-passage summary cards, always shown alongside — never in place of — the real underlying text
- Every result opens directly to its exact page in-app
- Offline fallback to a bundled local corpus when connectivity is unavailable

---

## How This Differs From Existing EOS Calculators

| | Traditional calculators (e.g. Kaiser Permanente EOS Calculator) | NeoGuard AI |
|---|---|---|
| Risk model | Fixed prediction model | Trained, calibrated, retrainable ML model |
| Risk factors | Predominantly maternal/perinatal | Maternal, neonatal, and laboratory, with trend data |
| Update cadence | Static estimate at birth | Continuously reassessed as new data arrives |
| Explanation | Limited | Full SHAP-based driver breakdown |
| Evidence synthesis | None | Real-time RAG over NICE/AAP/WHO/local literature |
| Guideline support | Single, fixed | Multi-guideline, locally extensible |
| Trust verification | None | Independent LLM judge + iterative resolution agent |

---

## Technical Stack

- **Frontend**: Flutter (iOS + Android), Riverpod state management, offline-first architecture with local caching for guideline PDFs and pre-synthesized protocols
- **Backend**: Python / FastAPI
- **Database**: Supabase (Postgres) with `pgvector` for embedding storage
- **ML risk engine**: scikit-learn / XGBoost-class models, isotonic calibration, SHAP explainability, `joblib` deployment; distilled on-device variant with closed-form explanations and no ML runtime
- **AI/LLM layer**: multi-provider chain (local Ollama/vLLM → Gemini → Groq) with automatic failover to a deterministic rule-based fallback if every provider is unavailable
- **RAG**: hybrid BM25 + semantic retrieval, Reciprocal Rank Fusion, cross-encoder reranking, MMR diversity
- **Security/compliance**: designed for HIPAA/GDPR-compliant cloud infrastructure

---

## Technical Competencies Demonstrated

Grounded strictly in what's actually implemented above — no cluster item below is listed unless a corresponding section of this document describes it.

**Core AI/ML**: trained/calibrated/benchmarked ML risk model, dual feature-selection methodology (Lasso + Boruta), leakage auditing, isotonic calibration with prevalence correction, five-architecture model benchmarking, independent holdout evaluation.

**NLP / LLM & Retrieval-Augmented Generation**: hybrid BM25 + semantic retrieval, Reciprocal Rank Fusion, cross-encoder reranking, MMR diversity, configurable document chunking strategies, clinical synonym/vocabulary expansion, structured + LLM-based query construction, multi-guideline evidence retrieval, prompt design for citation-constrained generation.

**Agentic AI / Multi-Agent Systems**: an architecturally independent generator/judge agent pair, an iterative resolution agent that autonomously re-searches and re-verifies rather than stopping at the first fact-check failure, and a shared citation-registry mechanism coordinating state across the whole multi-step agent run.

**Trustworthy & Explainable AI**: LLM-as-judge fact-checking against retrieved evidence, hallucination/unsupported-claim mitigation via retrieve-first-default-last correction logic, SHAP-based model explainability, deterministic (non-LLM) clinical safety nets running independently of the generative layer, transparent labeling whenever a fallback (rather than grounded evidence) is used.

**Data Engineering**: PDF structure extraction using typography metadata, noise/boilerplate removal, paragraph reconstruction, content-based document classification, configurable ingestion pipeline, vector index construction and rebuild.

**Data Science**: leakage auditing via dual-method feature-importance cross-checking, external-cohort calibration validation, dynamic stratified data splitting, NLG evaluation metrics (METEOR, BERTScore, RAGAS, BLEU).

**Software / Mobile Engineering**: cross-platform Flutter frontend, offline-first architecture (local PDF/protocol caching, graceful degradation without connectivity), FastAPI backend, Postgres/`pgvector` database design, REST API integration, multi-provider LLM failover chain.

**Privacy / Security-Oriented Engineering**: structured de-identification layer enforced at every AI-facing boundary, data-minimisation-by-construction design.

**HCI / Explainability UX**: interactive risk-driver visualisation (waterfall chart, per-driver detail view), hover/tap evidence-citation interaction with direct jump-to-source-page navigation, patient timeline visualisation, offline-state UI affordances.

---

## Evaluation Methodology

**ML Risk Model**: independent holdout evaluation (AUC 0.809, 87.7% sensitivity), calibration cross-validated against an external published cohort (Kainth et al., BMJ Paediatrics Open 2026), dual-method leakage audit (Lasso + Random Forest importance).

**RAG / LLM pipeline**: a stratified golden vignette evaluation set spanning multiple clinical risk strata, a dedicated retrieval-quality ablation harness for chunking/retrieval configuration comparisons, and care-plan generation quality scored with METEOR, BERTScore, RAGAS, and BLEU.

---

## Deployment

- **Backend inference**: calibrated ML model + full SHAP computation, served behind a dedicated risk-assessment API endpoint
- **On-device inference**: distilled logistic-regression model with closed-form explainability, zero ML-runtime dependency, for offline operation
- **LLM generation**: local-first provider chain (Ollama/vLLM) with cloud fallback (Gemini, then Groq), and a final deterministic rule-based fallback if no provider is reachable

---

## Scope & Limitations

- The current ML risk model is trained on a **synthetic neonatal sepsis dataset**, used to validate the full pipeline architecture (feature selection, calibration, explainability, dual deployment) end-to-end. Retraining on real patient data is the next milestone — expect performance numbers to shift once that happens; this is expected, and is exactly why the calibration *methodology* (already validated against a real external cohort) matters more long-term than the current specific AUC figure.
- `risk_category` thresholds are pending formal clinical sign-off before any production deployment.
- Lab features (WBC, CRP) are scaffolded in preprocessing but not yet part of the active feature set — awaiting richer serial lab data collection.

---

## Roadmap

**Risk model**
1. Retrain on real serial/longitudinal patient data
2. Incorporate additional app-collected fields (GBS status, procalcitonin, blood gas, etc.) once available
3. Clinical sign-off on `risk_category` thresholds
4. Prospective clinical validation
5. Temporal trend features (deltas/slopes across serial assessments) to fully realise dynamic, continuously-updating risk assessment

**Platform**
1. Expand from the Neonatology module into the two remaining planned modules of the integrated paediatric platform: **Paediatric Cardiology** and **Community Paediatrics**
2. Multicentre data collection to support advanced ML development: culture-positive sepsis prediction, severe sepsis prediction, mortality prediction, NICU admission prediction, and antibiotic optimisation pathways
