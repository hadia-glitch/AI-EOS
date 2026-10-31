# NeoGuard AI
### Explainable Neonatal Sepsis Risk Prediction, Evidence-Grounded RAG & Multi-Agent Clinical Decision Support

**NeoGuard AI** is an AI-powered neonatal early-onset sepsis (EOS) research and clinical decision-support platform that combines **calibrated machine learning, SHAP explainability, graph-enhanced retrieval-augmented generation (RAG), and Multi-Agent LLM verification** to support personalized risk assessment and guideline-grounded care-plan generation.

Developed as part of a research internship at **TUKL (Technical University of Kaiserslautern) Lab, National University of Sciences & Technology (NUST)**, beginning in June 2026.

**Project status:** Research prototype · Experimental evaluation completed for initial cohorts · Research paper under preparation/review

**Core stack:** `Flutter` · `Python` · `FastAPI` · `Supabase` · `PostgreSQL/pgvector` · `Ollama` · `LlamaIndex` · `XGBoost` · `scikit-learn` · `SHAP` · `BM25` · `Graph RAG` · `Multi-Agent AI`


[Watch the short demo](https://drive.google.com/file/d/1txVzJRD7wyvlbKZAkRwLaSvrSlsM-RIy/view?usp=sharing)

For developements beyond Aug 2026 visit https://github.com/hadia-glitch/eosRAG (**contains comparison of risk prediction models based on different neonatal sepsis deatasets, RAG refinements, improved performance metrics and the final research paper**)

RESEARCH PAPER UPDATED (contains findings from the above mentioned repo too):https://drive.google.com/file/d/1yqYmS4B5Aia3irZY26Eluns2DE1bnBqc/view?usp=sharing


## Key Achievements

| Component | Research Achievement |
|---|---|
| **ML Risk Prediction** | **0.809 ROC-AUC**, **87.7% sensitivity** |
| **Explainable AI** | SHAP-based feature contributions and calibrated predictions |
| **Retrieval Benchmarking** | Five architectures evaluated over **100 clinical cases** |
| **Graph Retrieval** | **0.809 Exact-ID MRR@10** |
| **Graph Anchor Grounding** | **0.859 Recall** |
| **Hybrid Retrieval** | **0.598 Section Precision**, **0.552 Section F1** |
| **LLM Semantic Evaluation** | Hybrid achieved **0.950 Semantic MRR** and **0.913 Semantic NDCG** in the initial evaluation |
| **Generation Safety** | Regimen-incomplete cases reduced from **6.7% to 0%** |
| **Drug-Dose Grounding** | Ungrounded-dose cases reduced from **3.3% to 0%** |
| **Multi-Agent Resolution** | Average resolution steps reduced from **3.00 to 2.27** |
| **LLM-Based Clinical Usability** | Hybrid received **5.0/5** on the previously reported automated safety assessment |

> **Evaluation note:** Results are drawn from different experimental phases. The 100-case retrieval evaluation, initial 30-case semantic evaluation, ML holdout evaluation, and 30-case generation/Multi-Agent evaluations are reported separately. LLM-based clinical usability is an automated assessment, not independent clinician validation.

---

## 1. Overview

Early-onset neonatal sepsis is a major neonatal healthcare challenge because early symptoms can be nonspecific and clinical deterioration may occur rapidly.

Risk assessment requires integrating maternal history, neonatal examination findings, laboratory measurements, and evolving patient conditions.

NeoGuard investigates a unified clinical AI workflow that combines:

1. **Machine-learning risk prediction** to estimate individualized neonatal sepsis risk.
2. **Explainable AI** to identify the factors influencing the prediction.
3. **Evidence-grounded RAG** to retrieve relevant clinical guideline recommendations.
4. **Personalized care-plan generation** based on risk predictions and patient history.
5. **Multi-Agent verification and resolution** to detect and correct unsupported recommendations.
6. **Deterministic safety controls** to identify important contraindication and treatment-completeness issues.
7. **Longitudinal patient monitoring** to incorporate changing clinical conditions.

The central objective is to move beyond isolated risk scoring toward **explainable, evidence-grounded, and verifiable clinical decision support**.

---

## 2. System Architecture

NeoGuard consists of four integrated components:

- Calibrated ML risk-assessment engine.
- Guideline-aware RAG retrieval engine.
- LLM-based generation and verification agents.
- Deterministic safety and resolution layer.

```text
                     Flutter Mobile Application
                                |
                                v
                    Structured Clinical Data
                                |
                                v
                   Client-Side De-identification
                                |
                                v
                    ML Risk Prediction Engine
                 GBM | XGBoost | SVM | RF
                                |
                                v
                    Calibrated Risk Estimate
                                |
                                v
                       SHAP Explanations
                                |
                                v
                     Clinical Context Builder
               Risk + Patient History + Trends
                                |
                                v
                   Guideline-Aware RAG Engine
                                |
                +---------------+---------------+
                |               |               |
                v               v               v
              BM25           Vector           Graph
                |               |               |
                +---------------+---------------+
                                |
                                v
                     Hybrid Evidence Fusion
                    Reciprocal Rank Fusion
                                |
                                v
                     Cross-Encoder Reranking
                                |
                                v
                      MMR Diversity Selection
                                |
                                v
                      Context Reassembly
                                |
                                v
                        Generator LLM
                                |
                                v
                       Fact-Check Judge
                                |
                                v
                       Resolution Agent
                                |
                                v
                    Deterministic Safety Checks
                                |
                                v
                   Personalized Clinical Care Plan
```

---

## 3. Machine-Learning Risk Prediction

### 3.1 Clinical feature engineering

The clinical feature set was developed in collaboration with an **NHS clinician** to incorporate clinically relevant maternal, neonatal, and laboratory parameters.

**Maternal and perinatal features:**

- Gestational age.
- Maternal temperature.
- Duration of rupture of membranes.
- Group B Streptococcus status.
- Intrapartum antibiotic exposure.
- Suspected chorioamnionitis.
- Delivery characteristics.

**Neonatal features:**

- Respiratory distress.
- Oxygen requirements.
- Temperature instability.
- Apgar scores.
- Feeding difficulties.
- Perfusion abnormalities.
- Neurological status.

**Laboratory features:**

- White blood cell count.
- C-reactive protein.
- Procalcitonin.
- Platelet count.
- Blood culture results.
- Longitudinal laboratory trends.

### 3.2 Model-development pipeline

```text
Clinical Dataset
       |
       v
Data Preprocessing
       |
       v
Feature Selection
(Lasso + Boruta + Clinical Review)
       |
       v
Stratified Train/Test Evaluation
       |
       v
Model Benchmarking
       |
       +-- Gradient Boosting
       +-- XGBoost
       +-- Support Vector Machine
       +-- Random Forest
       +-- Ensemble
       |
       v
Model Selection
       |
       v
Isotonic Calibration
       |
       v
Prevalence Adjustment
       |
       v
SHAP Explainability
       |
       v
Backend / On-Device Inference
```

### 3.3 Phase 1 — Risk prediction results

| Metric | Result |
|---|---:|
| **ROC-AUC** | **0.809** |
| **Sensitivity** | **87.7%** |
| Model architectures compared | 5 |
| Probability calibration | Isotonic |
| Explainability | SHAP |
| Evaluation | Recorded independent holdout |

The trained risk-prediction pipeline achieved **0.809 ROC-AUC and 87.7% sensitivity** at the selected clinical screening threshold.

### 3.4 SHAP explainability

NeoGuard provides interpretable explanations for each risk prediction using SHAP-based feature contributions.

The application displays:

- Principal risk drivers.
- Relative feature contributions.
- Factors increasing predicted risk.
- Factors decreasing predicted risk.
- Patient-specific risk explanations.

This makes the prediction process more transparent and supports interpretation of model-generated risk estimates.

---

## 4. Clinical Decision-Support Application

NeoGuard is implemented as a Flutter-based mobile application supported by a FastAPI backend.

### Clinical data entry

Clinicians enter structured maternal, neonatal, and laboratory parameters through a dedicated assessment interface.

### Risk stratification

The ML engine produces:

- Individualized EOS risk estimates.
- Risk categories.
- Ranked contributing features.
- SHAP-based explanations.

### Longitudinal patient history

NeoGuard maintains historical assessments to support repeated risk evaluation as clinical information changes.

The timeline subsystem tracks:

- Previous risk scores.
- Changes in risk categories.
- Laboratory trajectories.
- Respiratory and clinical status.
- Deteriorating or improving trends.
- Patient-specific changes across assessments.

### Personalized care plans

Based on clinical requirements defined with clinician input, NeoGuard generates a structured **13-section clinical care-plan framework** covering:

- Clinical and developmental summary.
- Risk interpretation.
- Immediate clinical actions.
- Antibiotic management.
- Laboratory investigations.
- Monitoring.
- Escalation criteria.
- Risk-driver explanations.
- Guideline evidence.
- Nutrition and fluid considerations.
- Parent communication.
- Contraindication and ambiguity handling.
- Longitudinal changes.

---

## 5. Privacy-Preserving Architecture

NeoGuard incorporates privacy safeguards into its clinical data pipeline.

Implemented design features include:

- Schema-validated client-side de-identification.
- Removal of direct patient identifiers before AI processing.
- Server-side PHI rejection.
- Structured AI-facing clinical payloads.
- Separation of patient identity from model inference data.
- Offline-capable components and local caching.

The system is designed around healthcare data minimization principles. Formal regulatory compliance and production security certification remain separate validation requirements.

---

## 6. Guideline Ingestion and Knowledge Engineering

The RAG corpus is built from clinical guidelines and related evidence sources.

### Guideline sources

- **NICE NG195** — Neonatal infection guidance.
- **AAP** — American Academy of Pediatrics EOS recommendations.
- **AAP_PRETERM** — Preterm-focused recommendations.
- **WHO** — Young-infant serious bacterial infection guidance.

The broader application also supports integrating additional literature and institutional protocols.

### Ingestion pipeline

```text
Clinical Guideline PDFs
           |
           v
Structure-Aware Extraction
           |
           v
Noise and Header/Footer Removal
           |
           v
Paragraph Reconstruction
           |
           v
Section and Table Processing
           |
           v
Clinical-Aware Chunking
           |
           v
Metadata + Source Attribution
           |
           v
Embedding Generation
           |
           v
PostgreSQL / pgvector
           |
           v
Searchable Guideline Evidence
```

The ingestion system supports fixed-size, semantic, proposition-level, and clinically structured chunking strategies.

Metadata preserves guideline identity, source location, section information, and chunk identifiers for evidence traceability.

---

## 7. Retrieval-Augmented Generation

NeoGuard's retrieval engine combines several complementary information-retrieval techniques.

### Sparse retrieval

BM25-based lexical retrieval is used to identify passages containing relevant clinical terms and exact terminology.

### Dense retrieval

Embedding-based retrieval identifies evidence based on semantic similarity.

### Vector retrieval

A separate vector-search configuration provides an additional semantic retrieval baseline.

### Graph retrieval

Graph retrieval uses structured relationships between guideline evidence and clinical recommendations.

### Custom Vector–Graph Hybrid

The custom Hybrid combines vector-based evidence retrieval with graph-oriented retrieval signals.

The broader retrieval architecture incorporates:

- **Reciprocal Rank Fusion (RRF)** to combine ranked candidate sets.
- **Cross-encoder reranking** for more detailed query–evidence relevance assessment.
- **Maximal Marginal Relevance (MMR)** to improve evidence diversity.
- **Clinical synonym expansion** to bridge terminology differences.
- **Guideline-aware ranking** to prioritize applicable evidence.
- **Context expansion** to recover related evidence beyond isolated chunks.

These techniques form the primary retrieval-engineering contribution of NeoGuard.

---

## 8. Multi-Agent Verification Architecture

NeoGuard uses functionally separated generation, fact-checking, and resolution stages.

### Generator agent

Produces a personalized clinical care plan using:

- Predicted risk.
- SHAP risk drivers.
- Patient history.
- Active guideline context.
- Retrieved evidence.

### Fact-checking agent

Reviews generated claims against the available evidence.

It checks:

- Citation grounding.
- Unsupported statements.
- Antibiotic recommendations.
- Numeric drug dosages.
- Guideline consistency.
- Contraindication handling.

### Resolution agent

Attempts to correct flagged issues through targeted evidence retrieval and iterative revision.

```text
Generated Clinical Plan
          |
          v
     Fact-Check Judge
          |
          v
   Unsupported Claims?
          |
          v
    Targeted Retrieval
          |
          v
     Resolution Agent
          |
          v
       Reverification
          |
          v
 Deterministic Safety Checks
          |
          v
       Final Care Plan
```

### Deterministic safety checks

Safety-oriented controls address:

- Regimen completeness.
- Allergy-related recommendations.
- Renal and nephrotoxicity considerations.
- Unsupported numeric doses.
- Cross-guideline conflicts.
- Explicit fallback disclosure.

The Multi-Agent results measure the complete verification-and-resolution pipeline, not the isolated effect of any single agent.

---

# 9. Research Evaluation

NeoGuard follows a three-tier evaluation methodology.

| Tier | Evaluation | Objective |
|---|---|---|
| **Tier 1** | Exact Retrieval | Measure designated evidence ranking and grounding |
| **Tier 2** | LLM-Assisted Semantic Evaluation | Measure semantic relevance and evidence utility |
| **Tier 3** | LLM-Based Clinical Usability | Assess the quality, completeness, and safety of generated plans |

Automatic generation metrics and Multi-Agent verification are evaluated separately as downstream components.

### Experimental cohorts

| Component | Evaluation cohort |
|---|---|
| ML risk prediction | Independent holdout |
| Tier 1 — Exact retrieval | **100 cases × 5 arms** |
| Tier 2 — LLM semantic retrieval | Initial 30-case cohort |
| Tier 3 — LLM clinical usability | Previously reported 30-case cohort |
| Generation | 30 Dense + 30 Hybrid outputs |
| Multi-Agent | Dense-MA and Hybrid-MA, 30 cases each |

The first-phase results and expanded retrieval rerun are not combined into one artificial benchmark.

---

# 10. Tier 1 — Exact Retrieval Evaluation

Five architectures were benchmarked using 100 structured neonatal EOS cases.

## Exact-ID ranking

| Retrieval Arm | Exact-ID MRR@10 | Reported NDCG@10* |
|---|---:|---:|
| Dense | 0.491 | 0.609 |
| Sparse | 0.613 | 0.665 |
| Vector | 0.547 | 0.630 |
| **Graph** | **0.809** | **0.795** |
| Hybrid | 0.805 | 0.778 |

**Graph achieved the strongest reported exact evidence-ranking performance:**

- **Exact-ID MRR@10: 0.809**
- **Retrieved-hit-normalized NDCG@10: 0.795**

Hybrid followed closely with **MRR@10 = 0.805**.



## Anchor-grounding recall

Anchor phrases represent case-specific clinical evidence concepts, including risk factors, antimicrobial recommendations, blood cultures, monitoring, referral, and treatment duration.

| Retrieval Arm | Anchor-Grounding Recall |
|---|---:|
| Dense | 0.834 |
| Sparse | 0.821 |
| Vector | 0.813 |
| **Graph** | **0.859** |
| Hybrid | 0.837 |

**Graph achieved the highest anchor-grounding recall at 0.859.**

This suggests strong recovery of predefined clinically relevant evidence concepts.



# 11. Tier 2 — LLM-Assisted Semantic Retrieval Evaluation

Exact chunk IDs do not always capture semantic equivalence or clinical evidence utility.

NeoGuard therefore uses an **LLM-as-a-Judge framework** to evaluate retrieved evidence beyond exact gold-chunk matching.

The initial semantic evaluation recorded:

- **1,500 retrieved chunk positions**
- **831 exact gold-evidence misses**

### Evaluation criteria

The judge assesses:

- Clinical relevance.
- Guideline alignment.
- Scenario applicability.
- Evidence sufficiency.
- Semantic equivalence.
- Useful non-gold evidence.
- Redundancy.
- Safety relevance.

## Initial LLM-as-Judge results

| Retriever | Semantic Recall@10 | Utility Precision@10 | Semantic MRR | Semantic NDCG@10 |
|---|---:|---:|---:|---:|
| Dense | 0.335 | **0.820** | 0.820 | 0.877 |
| Sparse | 0.322 | 0.717 | 0.803 | 0.849 |
| Vector | 0.348 | 0.760 | 0.812 | 0.885 |
| Graph | 0.456 | 0.740 | 0.850 | 0.886 |
| **Hybrid** | **0.504** | 0.807 | **0.950** | **0.913** |

### Key results

Hybrid achieved the strongest recorded:

- **Semantic Recall@10: 0.504**
- **Semantic MRR: 0.950**
- **Semantic NDCG@10: 0.913**

Dense obtained the highest utility precision at **0.820**.

The findings indicate that exact chunk identity and semantic evidence usefulness are distinct evaluation dimensions.

**These are historical LLM-assisted results from the initial 30-case cohort, not newly computed scores on the 100-case benchmark.**

---

# 12. Tier 3 — LLM-Based Clinical Usability Evaluation

The third evaluation layer assesses the **quality and clinical usability of generated care plans**.

This analysis is explicitly labeled:

**LLM-Based Clinical Usability Assessment — Not Independent Clinician Validation**

## Evaluation dimensions

Generated plans are assessed using a 1–5 rubric covering:

1. Faithfulness and groundedness.
2. Citation correctness.
3. Citation completeness.
4. Clinical correctness.
5. Clinical completeness.
6. Clinical utility.
7. Safety.

Additional evaluations address unsupported claims, contradictions, and critical omissions.

## Previously reported LLM-based usability results

| Metric | Dense | Hybrid |
|---|---:|---:|
| Faithfulness / Groundedness | **3.884** | 3.785 |
| Citation Correctness | **3.576** | 3.462 |
| Citation Completeness | **4.068** | 4.017 |
| Clinical Correctness | **3.975** | 3.924 |
| Clinical Completeness | **4.290** | 4.224 |
| Clinical Utility | **4.132** | 4.074 |
| **LLM-Rated Safety** | 4.873 | **5.000** |
| Unsupported-Claim Rate | 6.7% | **0%** |
| Contradiction Rate | 0% | 0% |
| Critical-Omission Rate | 6.7% | **0%** |

*These values are from the previously reported LLM-judge analysis. The corresponding case-level judgment records should be independently audited before treating the aggregates as final publication evidence.*

### Main findings

Dense achieved slightly higher scores in several general content-quality dimensions.

Hybrid achieved the strongest reported safety-oriented outcomes:

- **5.0/5 LLM-rated safety**
- **0% unsupported-claim cases**
- **0% contradictions**
- **0% critical-omission cases**

These results suggest a distinction between general generation quality and safety-oriented performance.

An automated LLM safety score does not demonstrate real-world clinical safety.

## Guideline-specific clinical usability

| Guideline | Arm | Clinical Correctness | Clinical Completeness | Clinical Utility | Safety |
|---|---|---:|---:|---:|---:|
| AAP | Dense | **4.000** | **4.257** | **4.129** | 5.000 |
| AAP | Hybrid | 3.892 | 4.207 | 4.049 | 5.000 |
| NICE | Dense | **4.080** | **4.327** | **4.204** | 5.000 |
| NICE | Hybrid | 3.921 | 4.224 | 4.072 | 5.000 |
| WHO | Dense | 3.791 | **4.258** | 4.025 | 4.578 |
| WHO | Hybrid | **3.955** | 4.237 | **4.096** | **5.000** |

The recorded WHO subgroup results favor Hybrid on clinical correctness, utility, and safety. Dense remains competitive or slightly stronger in several AAP/NICE dimensions.

---

# 13. Generation Evaluation

The initial care-plan generation experiment evaluated Dense and Hybrid over **30 cases per arm**.

## Automatic generation metrics

| Metric | Dense | Hybrid |
|---|---:|---:|
| METEOR | **0.431** | 0.423 |
| BLEU | **0.172** | 0.162 |
| BLEU-1 | **0.473** | 0.469 |
| BLEU-2 | **0.311** | 0.302 |
| BERTScore RoBERTa-large F1 | **0.870** | 0.869 |
| BERTScore DistilBERT F1 | **0.845** | 0.842 |
| Anchor-Grounding Recall | **0.636** | 0.629 |

The generated plans achieved similar BERTScore values, indicating close semantic alignment with their reference plans.

These automatic metrics measure reference similarity, not clinical correctness.

## Safety-oriented outcomes

| Metric | Dense | Hybrid |
|---|---:|---:|
| Regimen-Incomplete Cases | 6.7% | **0%** |
| Ungrounded-Dose Cases | 3.3% | **0%** |
| Cross-Guideline Conflicts | 0% | 0% |

**Hybrid recorded 0% incomplete antibiotic regimens and 0% ungrounded drug dosages in the evaluated cohort.**

---

# 14. Multi-Agent Verification Results

The Multi-Agent experiment compares Dense-MA with Hybrid-MA.

| Metric | Dense-MA | Hybrid-MA |
|---|---:|---:|
| Fact-Check Verified | 86.7% | 86.7% |
| Mean Fact-Check Confidence | 0.837 | 0.837 |
| Mean Resolution Steps | 3.00 | **2.27** |
| Regimen-Incomplete Cases | 6.7% | **0%** |
| Ungrounded-Dose Cases | 3.3% | **0%** |

### Key results

Hybrid-MA recorded:

- **0% incomplete antibiotic regimens**
- **0% ungrounded-dose cases**
- **2.27 average resolution steps**

Compared with Dense-MA, the Hybrid-based pipeline reduced resolution effort while maintaining the same 86.7% verification rate.

These improvements are associated with the complete retrieval, verification, resolution, and deterministic-safety workflow.

---

# 15. First-Phase Research Summary

The first research phase established the feasibility of an integrated explainable ML and evidence-grounded Agentic RAG framework for neonatal EOS.

| Research Area | Reported Result |
|---|---|
| ML risk prediction | **0.809 ROC-AUC** |
| Screening sensitivity | **87.7%** |
| Explainability | SHAP |
| Retrieval architectures | 5 evaluated |
| Graph exact ranking | **0.809 MRR@10** (expanded benchmark) |
| Graph anchor grounding | **0.859 Recall** (expanded benchmark) |
| Hybrid semantic ranking | **0.950 MRR** (initial evaluation) |
| Hybrid semantic NDCG | **0.913** (initial evaluation) |
| Hybrid section precision | **0.598** (expanded benchmark) |
| Hybrid regimen incompleteness | **0% observed** |
| Hybrid ungrounded dosage | **0% observed** |
| Multi-Agent resolution steps | **2.27** |
| Hybrid LLM-rated safety | **5.0/5**, previously reported |

The project demonstrates the integration of risk modeling, information retrieval, LLM reasoning, explainability, and automated verification.

---

# 16. Technical Stack

| Layer | Technologies |
|---|---|
| Mobile Frontend | Flutter, Dart, Riverpod |
| Backend | Python, FastAPI |
| Database | Supabase, PostgreSQL |
| Vector Database | pgvector |
| ML | XGBoost, Gradient Boosting, SVM, Random Forest, scikit-learn |
| Explainability | SHAP |
| Calibration | Isotonic Regression |
| LLMs | Ollama, vLLM, Groq, Gemini |
| RAG | LlamaIndex, BM25, Dense/Vector Retrieval, Graph Retrieval |
| Fusion | Reciprocal Rank Fusion |
| Reranking | Cross-Encoder |
| Diversity | Maximal Marginal Relevance |
| Evaluation | MRR, NDCG, BLEU, METEOR, BERTScore, LLM-as-Judge |
| Privacy | Client-Side De-identification, Schema Validation, PHI Rejection |

---


# 17. Limitations

NeoGuard is a research prototype and is not independently clinically validated.

Important limitations include:

- Retrieval benchmarks use simulated or curated clinical cases.
- LLM-as-Judge evaluation is not clinical ground truth.
- LLM-rated safety is not proof of clinical safety.
- Automated text metrics do not measure independent clinical correctness.
- Multi-Agent improvements are measured for the complete pipeline rather than isolated agents.
- Independent clinician assessment and prospective clinical evaluation are still required.

The system is intended for research and clinician-supervised decision support, not autonomous diagnosis or treatment.




---

**Disclaimer:** NeoGuard is a research prototype. Its outputs are not intended to replace qualified clinical judgment or validated medical protocols.
