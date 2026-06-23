"""Seed guideline chunks used when Supabase is empty (development / offline fallback)."""

SEED_CHUNKS = [
    {
        "id": "seed-nice-001",
        "source": "NICE",
        "source_name": "NICE NG195",
        "section": "Maternal Risk Factors",
        "region_tag": "UK",
        "version": "2023",
        "chunk_text": (
            "NICE NG195 recommends assessing risk factors for early-onset neonatal sepsis including "
            "maternal GBS colonization, prolonged rupture of membranes (>18 hours), maternal intrapartum "
            "fever (>=38.0°C), and clinical chorioamnionitis. Empirical IV benzylpenicillin and gentamicin "
            "should be started within 1 hour when treatment is indicated."
        ),
    },
    {
        "id": "seed-nice-002",
        "source": "NICE",
        "source_name": "NICE NG195",
        "section": "Laboratory Monitoring",
        "region_tag": "UK",
        "version": "2023",
        "chunk_text": (
            "Measure C-reactive protein (CRP) at 18-24 hours. Perform blood culture before antibiotics "
            "but do not delay treatment. Consider stopping antibiotics at 36-48 hours if culture negative, "
            "clinically well, and CRP normal (<10 mg/L). I:T ratio >0.20 supports infection evaluation."
        ),
    },
    {
        "id": "seed-aap-001",
        "source": "AAP",
        "source_name": "AAP EOS Guidelines 2023",
        "section": "Clinical Evaluation",
        "region_tag": "USA",
        "version": "2023",
        "chunk_text": (
            "AAP 2023 classifies newborns by clinical appearance. Infants with respiratory distress, "
            "CPAP or mechanical ventilation, or persistent vital sign abnormality require enhanced "
            "evaluation. Empiric ampicillin and gentamicin indicated for clinically ill infants."
        ),
    },
    {
        "id": "seed-aap-002",
        "source": "AAP",
        "source_name": "AAP EOS Guidelines 2023",
        "section": "GBS Prophylaxis",
        "region_tag": "USA",
        "version": "2023",
        "chunk_text": (
            "Adequate intrapartum antibiotic prophylaxis (IAP) is penicillin G, ampicillin, or cefazolin "
            "given >=4 hours before delivery. Inadequate IAP in GBS-positive mothers increases EOS risk."
        ),
    },
    {
        "id": "seed-who-001",
        "source": "WHO",
        "source_name": "WHO Newborn Sepsis Guidelines",
        "section": "Danger Signs",
        "region_tag": "GLOBAL",
        "version": "2024",
        "chunk_text": (
            "WHO newborn sepsis guidance: danger signs include poor feeding, convulsions, fast breathing, "
            "severe chest indrawing, high or low temperature. First-line injectable ampicillin and gentamicin "
            "with urgent referral when sepsis suspected in resource-limited settings."
        ),
    },
    {
        "id": "seed-eoscal-001",
        "source": "EOSCAL",
        "source_name": "Kuzniewicz et al. 2024",
        "section": "EOSCAL 2024 Recalibration",
        "region_tag": "GLOBAL",
        "version": "2024",
        "chunk_text": (
            "EOSCAL 2024 recalibrated on contemporary cohort restricts validated clinical status "
            "thresholds to infants >=35 weeks gestation. Clinical illness criteria include CPAP or "
            "mechanical ventilation, vasoactive drugs, base deficit <=-16, Apgar <5 at 5 minutes."
        ),
    },
    {
        "id": "seed-eoscal-002",
        "source": "EOSCAL",
        "source_name": "Puopolo et al. 2011",
        "section": "Risk Probability",
        "region_tag": "GLOBAL",
        "version": "2011",
        "chunk_text": (
            "Original EOSCAL estimates probability of early-onset infection per 1000 live births using "
            "maternal risk factors: gestational age, maternal temperature, ROM duration, GBS status, "
            "and intrapartum antibiotic administration via logistic regression."
        ),
    },
    {
        "id": "seed-nice-003",
        "source": "NICE",
        "source_name": "NICE NG195",
        "section": "Respiratory Distress",
        "region_tag": "UK",
        "version": "2023",
        "chunk_text": (
            "Respiratory distress starting more than 4 hours after birth, grunting, tachypnoea, "
            "nasal flaring, and need for CPAP are clinical indicators of possible early-onset sepsis "
            "requiring senior review and investigations including blood culture, FBC, and CRP."
        ),
    },
]
