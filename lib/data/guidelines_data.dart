class GuidelineChunk {
  final String id;
  final String source; // "NICE", "AAP", "WHO"
  final String section;
  final String content;
  final List<String> keywords;

  const GuidelineChunk({
    required this.id,
    required this.source,
    required this.section,
    required this.content,
    required this.keywords,
  });
}

class GuidelinesData {
  static const List<GuidelineChunk> chunks = [
    // --- NICE NG195 Guidelines (UK) ---
    GuidelineChunk(
      id: 'nice_maternal_risk',
      source: 'NICE',
      section: 'Maternal Risk Factors',
      content: 'NICE NG195 recommends assessing risk factors for early-onset neonatal sepsis. '
          'Key maternal risk factors include: invasive group B streptococcal infection in a previous baby, '
          'maternal group B streptococcal colonization, bacteriuria or infection during the current pregnancy, '
          'prelabor rupture of membranes (PROM), prolonged rupture of membranes (ROM > 18 hours), '
          'maternal intrapartum fever (temperature >= 38.0°C), and clinical diagnosis of chorioamnionitis.',
      keywords: ['maternal', 'risk', 'gbs', 'fever', 'temperature', 'rom', 'rupture', 'membranes', 'chorioamnionitis'],
    ),
    GuidelineChunk(
      id: 'nice_neonatal_signs',
      source: 'NICE',
      section: 'Neonatal Clinical Indicators',
      content: 'NICE NG195 details clinical indicators of possible early-onset neonatal sepsis. '
          'Red flag clinical indicators include: respiratory distress starting more than 4 hours after birth, '
          'intubation/mechanical ventilation, seizures, signs of shock (capillary refill time > 3 seconds, severe hypotension), '
          'need for cardiopulmonary resuscitation, and major neurological impairment. '
          'Non-red flag signs include respiratory rate > 60 breaths/min, grunting, nasal flaring, temperature instability '
          '(< 36.0°C or > 38.0°C), altered muscle tone (floppiness), and lethargy.',
      keywords: ['respiratory', 'distress', 'seizures', 'perfusion', 'shock', 'apgar', 'temperature', 'lethargy', 'irritability', 'tone'],
    ),
    GuidelineChunk(
      id: 'nice_antibiotics',
      source: 'NICE',
      section: 'Empiric Antibiotics',
      content: 'For neonates with a high risk or severe clinical signs, NICE NG195 recommends initiating '
          'empirical intravenous antibiotics within 1 hour of deciding to treat. '
          'The first-line antibiotic regimen is a combination of benzylpenicillin and gentamicin. '
          'Obtain a blood culture before administering the first dose of antibiotics, but do not delay treatment.',
      keywords: ['antibiotics', 'penicillin', 'gentamicin', 'treatment', 'empiric', 'culture', '1 hour'],
    ),
    GuidelineChunk(
      id: 'nice_monitoring_labs',
      source: 'NICE',
      section: 'Laboratory Investigations & Monitoring',
      content: 'Measure C-reactive protein (CRP) at 18 to 24 hours after birth or after starting antibiotic treatment. '
          'Perform a blood culture. Repeat CRP measurement at 36 to 48 hours. '
          'Consider stopping antibiotics at 36 hours if blood culture is negative, '
          'the baby is clinically well, and the CRP is normal (< 10 mg/L).',
      keywords: ['crp', 'wbc', 'culture', 'laboratory', 'monitoring', 'duration', 'stop'],
    ),

    // --- AAP 2023 Guidelines (US) ---
    GuidelineChunk(
      id: 'aap_maternal_gbs',
      source: 'AAP',
      section: 'Maternal GBS and Prophylaxis',
      content: 'The AAP 2023 guidelines emphasize maternal Group B Streptococcus (GBS) screening. '
          'Adequate intrapartum antibiotic prophylaxis (IAP) is defined as administration of Penicillin G, '
          'Ampicillin, or Cefazolin >= 4 hours prior to delivery. Inadequate IAP in GBS-positive mothers '
          'increases baseline risk, warranting close observation of the newborn.',
      keywords: ['gbs', 'antibiotics', 'prophylaxis', 'iap', 'penicillin', 'ampicillin', 'cefazolin', 'adequate'],
    ),
    GuidelineChunk(
      id: 'aap_neonatal_eval',
      source: 'AAP',
      section: 'Clinical Evaluation & Categorization',
      content: 'AAP 2023 recommends classifying newborns based on clinical appearance. '
          'Well-appearing infants born to mothers with risk factors should undergo enhanced observation. '
          'Equivocal or clinically ill infants (respiratory distress, temperature instability, '
          'tachycardia/bradycardia, lethargy, poor perfusion) require blood culture, '
          'laboratory assessment (complete blood count, CRP), and immediate empirical antibiotic therapy (Ampicillin + Gentamicin).',
      keywords: ['respiratory', 'distress', 'temperature', 'lethargy', 'perfusion', 'cbc', 'wbc', 'crp', 'ampicillin', 'gentamicin'],
    ),
    GuidelineChunk(
      id: 'aap_choriomamnio',
      source: 'AAP',
      section: 'Management of Suspected Chorioamnionitis',
      content: 'For infants born to mothers with suspected clinical chorioamnionitis (intrapartum fever >= 38.0°C '
          'plus maternal tachycardia, uterine tenderness, purulent fluid, or WBC > 15,000), '
          'AAP recommends obtaining blood cultures at birth and administering empirical antibiotics, '
          'even if the newborn appears clinically well at birth, unless the mother received robust broad-spectrum intrapartum antibiotics.',
      keywords: ['chorioamnionitis', 'fever', 'antibiotics', 'culture', 'well-appearing'],
    ),
    GuidelineChunk(
      id: 'aap_lab_markers',
      source: 'AAP',
      section: 'Role of Lab Markers',
      content: 'Complete Blood Count (CBC) and C-reactive protein (CRP) are poor screening tools '
          'when used at birth. CBC (specifically WBC count and Immature-to-Total ratio) and CRP are most '
          'valuable when drawn 6-12 hours after birth. A persistent low WBC (< 5,000) or an elevated '
          'I:T ratio (>= 0.2) or elevated CRP (>= 10 mg/L) supports the diagnosis of early-onset sepsis.',
      keywords: ['cbc', 'wbc', 'it ratio', 'crp', 'platelets', 'markers', 'hours', 'neutrophil'],
    ),

    // --- WHO Guidelines (Global/Resource-limited) ---
    GuidelineChunk(
      id: 'who_newborn_care',
      source: 'WHO',
      section: 'Essential Newborn Care & Prevention',
      content: 'WHO guidelines emphasize preventative essential newborn care for all infants. '
          'This includes immediate dry and thermal care (skin-to-skin contact), promotion of early, '
          'exclusive breastfeeding within the first hour of life, hygienic cord care (chlorhexidine in high-mortality areas), '
          'and delayed bathing (to prevent hypothermia). Standard hygiene during delivery is critical to prevent infection.',
      keywords: ['breastfeeding', 'thermal', 'cord', 'hygiene', 'prevention', 'skin-to-skin', 'hypothermia'],
    ),
    GuidelineChunk(
      id: 'who_danger_signs',
      source: 'WHO',
      section: 'Identification of Sepsis / Danger Signs',
      content: 'The WHO guidelines identify "danger signs" in newborns indicating possible serious bacterial infection. '
          'These include: poor feeding or inability to suckle, convulsions, fast breathing (respiratory rate >= 60 breaths/min), '
          'severe chest indrawing, high temperature (> 37.5°C), low temperature (< 35.5°C), '
          'movement only when stimulated or no movement at all (severe lethargy), and central cyanosis or jaundice.',
      keywords: ['danger signs', 'feeding', 'convulsions', 'respiratory', 'breathing', 'temperature', 'lethargy', 'movement', 'jaundice'],
    ),
    GuidelineChunk(
      id: 'who_antibiotic_regimen',
      source: 'WHO',
      section: 'First-Line Antibiotics',
      content: 'WHO recommends a combination of injectable Ampicillin (or Benzylpenicillin) and Gentamicin '
          'as the first-line treatment for neonates with signs of serious bacterial infection. '
          'Treatment should be given for at least 7-10 days in confirmed cases. '
          'If a referral is necessary, give a pre-referral dose of intramuscular Ampicillin and Gentamicin.',
      keywords: ['ampicillin', 'gentamicin', 'injectable', 'antibiotics', 'danger signs', 'referral', 'duration'],
    ),
    GuidelineChunk(
      id: 'who_supportive_care',
      source: 'WHO',
      section: 'Supportive Management',
      content: 'WHO outlines supportive therapies essential for neonatal sepsis management: '
          'ensure adequate oxygenation (nasal prongs if oxygen saturation < 90%), support hydration '
          'via IV fluids or nasogastric feeding if infant cannot suckle, manage hypoglycemia '
          '(IV 10% dextrose bolus if glucose < 2.5 mmol/L), and actively maintain thermal control '
          'using warm rooms, blankets, or incubators.',
      keywords: ['supportive', 'oxygen', 'hydration', 'glucose', 'hypoglycemia', 'thermal', 'fluids'],
    ),
  ];
}
