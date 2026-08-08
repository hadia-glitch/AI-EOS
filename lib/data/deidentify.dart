import '../domain/eoscal_calculator.dart';

/// De-identified patient snapshot for backend API calls (Section 8.1).
///
/// Extended to full parity with what's actually captured per assessment
/// (see patient_state.dart's _upsertClinicalTables) -- previously only 10 of
/// ~20 fields reached the backend, so query construction and generation
/// prompts couldn't see delivery mode, chorioamnionitis, oxygen need, apgar,
/// perfusion, neuro status, or any lab value except CRP/blood culture.
class DeidentifiedPatient {
  final double? gestationalAgeWeeks;
  final double? maternalTemperature;
  final double? romHours;
  final bool? gbsPositive;
  final String? respiratoryDistress;
  final double? crpLevel;
  final bool? bloodCulturePositive;
  final double? urineOutputMlKgHr;
  final double? creatinineMgDl;
  final bool? penicillinAllergy;
  final bool? adequateIntrapartumAntibiotics;
  final bool? clinicalChorioamnionitis;
  final String? deliveryMode;
  final String? oxygenNeed;
  final int? apgar5Min;
  final bool? poorPerfusion;
  final double? neonatalTemperature;
  final String? neurologicalStatus;
  final double? wbcCount;
  final double? itRatio;
  final double? plateletCount;
  final double? pctLevel;
  // Not previously exposed to the backend at all -- see
  // patient_entry_flow.dart / new_patient_screen.dart for where this is
  // actually captured now. Feeds rag/query_builder.py's low-birth-weight
  // query terms and domain/contraindication_rules.check_who_outpatient_exclusions
  // on the backend (both via schemas.PatientSnapshot.birth_weight_g).
  final double? birthWeightGrams;

  const DeidentifiedPatient({
    this.gestationalAgeWeeks,
    this.maternalTemperature,
    this.romHours,
    this.gbsPositive,
    this.respiratoryDistress,
    this.crpLevel,
    this.bloodCulturePositive,
    this.urineOutputMlKgHr,
    this.creatinineMgDl,
    this.penicillinAllergy,
    this.adequateIntrapartumAntibiotics,
    this.clinicalChorioamnionitis,
    this.deliveryMode,
    this.oxygenNeed,
    this.apgar5Min,
    this.poorPerfusion,
    this.neonatalTemperature,
    this.neurologicalStatus,
    this.wbcCount,
    this.itRatio,
    this.plateletCount,
    this.pctLevel,
    this.birthWeightGrams,
  });

  Map<String, dynamic> toJson() => {
        if (gestationalAgeWeeks != null) 'gestational_age_weeks': gestationalAgeWeeks,
        if (maternalTemperature != null) 'maternal_temperature': maternalTemperature,
        if (romHours != null) 'rom_hours': romHours,
        if (gbsPositive != null) 'gbs_positive': gbsPositive,
        if (respiratoryDistress != null) 'respiratory_distress': respiratoryDistress,
        if (crpLevel != null) 'crp_level': crpLevel,
        if (bloodCulturePositive != null) 'blood_culture_positive': bloodCulturePositive,
        if (urineOutputMlKgHr != null) 'urine_output_ml_kg_hr': urineOutputMlKgHr,
        if (creatinineMgDl != null) 'creatinine_mg_dl': creatinineMgDl,
        if (penicillinAllergy != null) 'penicillin_allergy': penicillinAllergy,
        if (adequateIntrapartumAntibiotics != null)
          'adequate_intrapartum_antibiotics': adequateIntrapartumAntibiotics,
        if (clinicalChorioamnionitis != null) 'clinical_chorioamnionitis': clinicalChorioamnionitis,
        if (deliveryMode != null) 'delivery_mode': deliveryMode,
        if (oxygenNeed != null) 'oxygen_need': oxygenNeed,
        if (apgar5Min != null) 'apgar_5_min': apgar5Min,
        if (poorPerfusion != null) 'poor_perfusion': poorPerfusion,
        if (neonatalTemperature != null) 'neonatal_temperature': neonatalTemperature,
        if (neurologicalStatus != null) 'neurological_status': neurologicalStatus,
        if (wbcCount != null) 'wbc_count': wbcCount,
        if (itRatio != null) 'it_ratio': itRatio,
        if (plateletCount != null) 'platelet_count': plateletCount,
        if (pctLevel != null) 'pct_level': pctLevel,
        if (birthWeightGrams != null) 'birth_weight_g': birthWeightGrams,
      };

  static DeidentifiedPatient fromPatient(PatientParameters p) {
    return DeidentifiedPatient(
      gestationalAgeWeeks: p.gestationalAgeWeeks,
      maternalTemperature: p.maternalTemperature,
      romHours: p.romHours,
      gbsPositive: p.gbsPositive,
      respiratoryDistress: p.respiratoryDistress,
      crpLevel: p.crpLevel,
      bloodCulturePositive: p.bloodCulturePositive,
      urineOutputMlKgHr: p.urineOutputMlKgHr,
      creatinineMgDl: p.creatinineMgDl,
      penicillinAllergy: p.penicillinAllergy,
      adequateIntrapartumAntibiotics: p.adequateIntrapartumAntibiotics,
      clinicalChorioamnionitis: p.clinicalChorioamnionitis,
      deliveryMode: p.deliveryMode,
      oxygenNeed: p.oxygenNeed,
      apgar5Min: p.apgar5Min,
      poorPerfusion: p.poorPerfusion,
      neonatalTemperature: p.neonatalTemperature,
      neurologicalStatus: p.neurologicalStatus,
      wbcCount: p.wbcCount,
      itRatio: p.itRatio,
      plateletCount: p.plateletCount,
      pctLevel: p.pctLevel,
      birthWeightGrams: p.birthWeightGrams,
    );
  }
}

/// De-identified risk payload for RAG + Gemini endpoints.
Map<String, dynamic> buildRiskPayload(PatientParameters patient, EoscalResult result) {
  return {
    'total_score': result.totalScore,
    'combined_score': result.totalScore,
    'layer1_score': result.layer1Score,
    'layer2_score': result.layer2Score,
    'layer3_score': result.layer3Score,
    'category': result.riskCategory.name.toUpperCase(),
    'risk_category': result.riskCategory.displayName,
    'probability_per_1000': result.probabilityPer1000,
    'below_eoscal_scope': result.belowEoscalScope,
    'drivers': result.allDrivers
        .map((d) => {
              'name': d.name,
              'points': d.points,
              'reason': d.reason,
              'contribution_percent': d.contributionPercent,
              'layer': d.layer,
            })
        .toList(),
    'patient': DeidentifiedPatient.fromPatient(patient).toJson(),
    'warnings': result.warnings,
    'combination_note': result.combinationNote,
  };
}