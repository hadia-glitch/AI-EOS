/// Built-in, zero-dependency rule-based care plan protocols.
///
/// This is the THIRD and final offline tier, in order of preference:
///   1. Live AI (online) — patient-specific, cites the actual retrieved
///      guideline chunks, includes the fact-check judge for HIGH/CRITICAL.
///   2. Synced guideline knowledge cache (OfflineGuidelineCache) — LLM- or
///      curated-synthesized per (guideline, category), pulled from the
///      backend whenever online, includes any local/institutional guideline
///      the admin has uploaded via Settings > Guideline Configuration.
///   3. THIS FILE — hardcoded in the app binary, works on a device that has
///      NEVER synced anything (fresh install with no connectivity yet, or
///      a (guideline, category) combination the backend cache doesn't have
///      an entry for). Ships the same evidence-based content as the
///      backend's curated_protocols.py fallback, so the two stay aligned,
///      but requires zero network access ever.
///
/// IMPORTANT — what this tier is NOT: it has no knowledge of any locally
/// uploaded institutional guideline's actual content (that only reaches the
/// device via tier 2's sync). For an unrecognised/local guideline source,
/// this falls back to the NICE-equivalent thresholds as a reasonable
/// general-practice default — CarePlanBuilder labels this explicitly as a
/// generic built-in protocol, not as reflecting the institution's uploaded
/// document, so a clinician is never misled into thinking offline content
/// is more specific than it actually is.
///
/// Clinical basis (mirrors backend/rag/curated_protocols.py):
///   NICE NG195  — risk factor assessment, red-flag clinical indicators,
///                 empirical antibiotics (benzylpenicillin + gentamicin),
///                 CRP at 18-24h, stop at 36h if culture-negative/well/CRP<10.
///   AAP 2023    — clinical categorisation (well/equivocal/ill), adequate
///                 IAP thresholds, ampicillin + gentamicin, CBC/CRP timing.
///   WHO         — danger-sign based assessment for resource-limited
///                 settings, ampicillin/benzylpenicillin + gentamicin,
///                 7-10 day course, pre-referral IM dosing.
class CuratedProtocol {
  final String clinicalSummaryTemplate;
  final String riskAnalysisTemplate;
  final String driverBreakdownTemplate;
  final List<String> recommendedActions;
  final bool antibioticRequired;
  final String antibioticUrgency;
  final List<String> antibioticRegimen;
  final String antibioticDuration;
  final String antibioticStopCriteria;
  final String monitoringPlan;
  final String escalationCriteria;

  const CuratedProtocol({
    required this.clinicalSummaryTemplate,
    required this.riskAnalysisTemplate,
    required this.driverBreakdownTemplate,
    required this.recommendedActions,
    required this.antibioticRequired,
    required this.antibioticUrgency,
    this.antibioticRegimen = const [],
    this.antibioticDuration = '',
    this.antibioticStopCriteria = '',
    required this.monitoringPlan,
    required this.escalationCriteria,
  });
}

const _niceAbxRegimen = ['IV benzylpenicillin', 'IV gentamicin'];
const _niceAbxDuration = 'Reassess at 36-48h; stop if culture-negative, clinically well, and CRP < 10 mg/L';
const _niceAbxStop = 'Blood culture negative at 36-48h AND clinically well AND CRP < 10 mg/L';

const _aapAbxRegimen = ['IV ampicillin', 'IV gentamicin'];
const _aapAbxDuration = '48-72 hours pending culture; extend per sensitivities if culture-positive';
const _aapAbxStop = 'Blood culture negative AND clinically well AND CBC/CRP reassuring at 24-48h';

const _whoAbxRegimen = ['Injectable ampicillin (or benzylpenicillin)', 'Injectable gentamicin'];
const _whoAbxDuration = 'At least 7-10 days if confirmed; pre-referral IM dose if transfer needed';
const _whoAbxStop = 'Only after completing the full course in confirmed cases — WHO protocol does not '
    'support early stopping in resource-limited settings without lab confirmation';

class CuratedCarePlanRules {
  static const Map<String, Map<String, CuratedProtocol>> _protocols = {
    'NICE': {
      'LOW': CuratedProtocol(
        clinicalSummaryTemplate:
            '{patient_ref} has a LOW EOSCAL risk under NICE NG195. No red-flag clinical indicators '
            'or significant maternal risk factors were identified.',
        riskAnalysisTemplate:
            'Score {total_score}. Layer contributions: maternal {layer1_score}, clinical {layer2_score}, '
            'laboratory {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Routine postnatal observation per unit protocol',
          'No blood culture or empirical antibiotics indicated at this time',
          'Educate parents on signs to prompt reassessment (feeding, breathing, temperature, tone)',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Not indicated',
        monitoringPlan: 'Routine observations per postnatal ward schedule. Reassess if any new clinical sign develops.',
        escalationCriteria: 'Any red-flag sign (respiratory distress >4h after birth, seizures, signs of '
            'shock, need for CPR) — reassess immediately and escalate.',
      ),
      'INTERMEDIATE': CuratedProtocol(
        clinicalSummaryTemplate:
            '{patient_ref} has an INTERMEDIATE EOSCAL risk under NICE NG195, driven by maternal and/or '
            'non-red-flag clinical factors requiring closer observation.',
        riskAnalysisTemplate:
            'Score {total_score}. Layer contributions: maternal {layer1_score}, clinical {layer2_score}, '
            'laboratory {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Enhanced clinical observation (minimum 4-hourly for first 12h) per NICE NG195',
          'Consider blood culture and CRP if any additional risk factor or non-red-flag sign emerges',
          'Review at 12h, 24h, and prior to discharge',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Reassess if signs progress',
        monitoringPlan: '4-hourly observations for at least 12 hours; extend if any new sign or risk '
            'factor identified. CRP at 18-24h if antibiotics are started.',
        escalationCriteria: 'Progression to any red-flag clinical indicator, or two or more non-red-flag '
            'signs — escalate to senior review and treat as HIGH risk.',
      ),
      'HIGH': CuratedProtocol(
        clinicalSummaryTemplate:
            '{patient_ref} has a HIGH EOSCAL risk under NICE NG195. Current evidence supports blood '
            'culture sampling and empirical antibiotic therapy.',
        riskAnalysisTemplate:
            'Score {total_score}. Layer contributions: maternal {layer1_score}, clinical {layer2_score}, '
            'laboratory {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Obtain blood culture before first antibiotic dose — do not delay treatment to do so',
          'Start empirical IV antibiotics within 1 hour of the decision to treat',
          'Measure CRP at 18-24h after birth or after starting antibiotics; repeat at 36-48h',
          'Senior clinician review within 1 hour',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Within 1 hour',
        antibioticRegimen: _niceAbxRegimen,
        antibioticDuration: _niceAbxDuration,
        antibioticStopCriteria: _niceAbxStop,
        monitoringPlan: 'Continuous/hourly observations for the first 4 hours, then per unit escalation '
            'protocol. CRP at 18-24h and 36-48h.',
        escalationCriteria: 'Clinical deterioration, positive blood culture, or CRP rising — escalate to '
            'CRITICAL pathway and involve NICU/senior neonatology.',
      ),
      'CRITICAL': CuratedProtocol(
        clinicalSummaryTemplate:
            '{patient_ref} has a CRITICAL EOSCAL risk under NICE NG195. Immediate empirical treatment '
            'and senior escalation are indicated.',
        riskAnalysisTemplate:
            'Score {total_score}. Layer contributions: maternal {layer1_score}, clinical {layer2_score}, '
            'laboratory {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Obtain blood culture and start empirical IV antibiotics IMMEDIATELY — do not wait for culture',
          'Immediate senior neonatology review and consider NICU admission',
          'Full septic screen per unit protocol (CRP, FBC, blood gas as clinically indicated)',
          'Continuous cardiorespiratory monitoring',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Immediate',
        antibioticRegimen: _niceAbxRegimen,
        antibioticDuration: _niceAbxDuration,
        antibioticStopCriteria: _niceAbxStop,
        monitoringPlan: 'Continuous cardiorespiratory monitoring in a NICU/HDU setting. Serial CRP and '
            'clinical review at least every 4 hours.',
        escalationCriteria: 'Any further deterioration — escalate per local critical-illness pathway; '
            'involve consultant neonatologist without delay.',
      ),
    },
    'AAP': {
      'LOW': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} is well-appearing with LOW EOSCAL risk under AAP 2023 categorisation.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Routine care — no laboratory evaluation or antibiotics indicated',
          'Standard newborn observation per unit protocol',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Not indicated',
        monitoringPlan: 'Routine observation per postnatal protocol.',
        escalationCriteria: 'Any change to equivocal or clinically-ill appearance — reassess and move '
            'to enhanced observation.',
      ),
      'INTERMEDIATE': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} is categorised as equivocal under AAP 2023, warranting enhanced observation.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Enhanced observation with vital signs at least every 4 hours',
          'Consider CBC (WBC, I:T ratio) and CRP if drawn 6-12h after birth for best sensitivity',
          'Reassess clinical appearance at each observation',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Reassess if signs progress',
        monitoringPlan: '4-hourly vital signs; CBC/CRP most informative at 6-12h of age per AAP 2023.',
        escalationCriteria: 'Respiratory distress, temperature instability, lethargy, or poor perfusion '
            'developing — treat as clinically ill (HIGH).',
      ),
      'HIGH': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} is categorised as clinically ill under AAP 2023 — immediate '
            'evaluation and empirical therapy are indicated.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Blood culture and CBC with differential (WBC, I:T ratio) and CRP',
          'Start empirical ampicillin and gentamicin without delay',
          'Assess adequacy of maternal intrapartum antibiotic prophylaxis (IAP) if GBS-positive',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Immediate',
        antibioticRegimen: _aapAbxRegimen,
        antibioticDuration: _aapAbxDuration,
        antibioticStopCriteria: _aapAbxStop,
        monitoringPlan: 'Continuous monitoring; repeat CBC/CRP at 24h if initial values equivocal.',
        escalationCriteria: 'Any further clinical decline, or blood culture turns positive — escalate to '
            'CRITICAL/NICU pathway.',
      ),
      'CRITICAL': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} meets AAP 2023 criteria for critically ill — immediate '
            'treatment and NICU-level care are indicated.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Immediate blood culture and empirical ampicillin + gentamicin — do not delay for culture',
          'NICU admission and consultant neonatology review',
          'Full sepsis workup (CBC, CRP, blood gas) and continuous monitoring',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Immediate',
        antibioticRegimen: _aapAbxRegimen,
        antibioticDuration: _aapAbxDuration,
        antibioticStopCriteria: _aapAbxStop,
        monitoringPlan: 'Continuous cardiorespiratory monitoring in NICU. Serial labs per consultant direction.',
        escalationCriteria: 'Ongoing instability — escalate per local critical-illness pathway without delay.',
      ),
    },
    'WHO': {
      'LOW': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} shows no WHO danger signs. LOW risk — essential newborn care applies.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Essential newborn care: skin-to-skin, early exclusive breastfeeding, hygienic cord care',
          'Educate caregiver on danger signs requiring urgent return',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Not indicated',
        monitoringPlan: 'Routine community/facility follow-up per WHO schedule.',
        escalationCriteria: 'Any WHO danger sign appearing (poor feeding, convulsions, fast breathing '
            '>=60/min, severe chest indrawing, temperature extremes, severe lethargy) — refer urgently.',
      ),
      'INTERMEDIATE': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} has emerging risk factors without confirmed WHO danger '
            'signs — close observation indicated.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Close observation for danger signs; reassess frequently',
          'Ensure adequate thermal care and support breastfeeding/feeding tolerance',
          'Arrange follow-up before discharge',
        ],
        antibioticRequired: false,
        antibioticUrgency: 'Reassess if danger signs emerge',
        monitoringPlan: 'Frequent reassessment for danger signs; supportive care (thermal, feeding).',
        escalationCriteria: 'Any single WHO danger sign — treat as serious bacterial infection and '
            'refer/treat per HIGH pathway.',
      ),
      'HIGH': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} shows WHO danger sign(s) of possible serious bacterial '
            'infection — treatment indicated.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Start first-line injectable antibiotics (ampicillin/benzylpenicillin + gentamicin) without delay',
          'Arrange urgent referral to a facility with neonatal care capability if not already there',
          'Support oxygenation, hydration, glucose, and thermal control',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Immediate',
        antibioticRegimen: _whoAbxRegimen,
        antibioticDuration: _whoAbxDuration,
        antibioticStopCriteria: _whoAbxStop,
        monitoringPlan: 'Close monitoring of oxygenation, hydration, glucose, and temperature during '
            'treatment/transfer.',
        escalationCriteria: 'Any deterioration during treatment or transfer — treat as CRITICAL and '
            'expedite referral.',
      ),
      'CRITICAL': CuratedProtocol(
        clinicalSummaryTemplate: '{patient_ref} shows multiple/severe WHO danger signs — treat as '
            'critical serious bacterial infection.',
        riskAnalysisTemplate: 'Score {total_score}. Layer contributions: L1 {layer1_score}, L2 {layer2_score}, L3 {layer3_score}.',
        driverBreakdownTemplate: 'See individual risk drivers below.',
        recommendedActions: [
          'Give pre-referral IM dose of ampicillin/benzylpenicillin and gentamicin if transfer is needed',
          'Urgent referral/transfer to a facility with NICU-level capability',
          'Aggressive supportive care: oxygen if saturation <90%, IV/NG fluids, dextrose for hypoglycaemia, '
              'thermal control',
        ],
        antibioticRequired: true,
        antibioticUrgency: 'Immediate (pre-referral IM dose if transferring)',
        antibioticRegimen: _whoAbxRegimen,
        antibioticDuration: _whoAbxDuration,
        antibioticStopCriteria: _whoAbxStop,
        monitoringPlan: 'Continuous monitoring during stabilisation and transfer; full course to be '
            'completed at receiving facility.',
        escalationCriteria: 'This is already the most urgent WHO pathway — ensure transfer/escalation '
            'is not delayed.',
      ),
    },
  };

  /// Returns the built-in protocol for (source, category). Falls back to
  /// NICE for any unrecognised source (e.g. a local/institutional guideline
  /// tag this device has never synced content for) and to INTERMEDIATE for
  /// an unrecognised category, so this NEVER returns null — it is always
  /// the guaranteed final fallback.
  static CuratedProtocol getProtocol(String source, String category) {
    final sourceKey = _protocols.containsKey(source.toUpperCase()) ? source.toUpperCase() : 'NICE';
    final categoryMap = _protocols[sourceKey]!;
    final categoryKey = categoryMap.containsKey(category.toUpperCase()) ? category.toUpperCase() : 'INTERMEDIATE';
    return categoryMap[categoryKey]!;
  }

  static bool hasExplicitProtocolFor(String source) => _protocols.containsKey(source.toUpperCase());
}