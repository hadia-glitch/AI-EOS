import '../domain/eoscal_calculator.dart';

/// De-identified patient snapshot for backend API calls (Section 8.1).
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
