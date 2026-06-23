import 'dart:math' as math;
import 'package:flutter/material.dart';

enum RiskCategory {
  low,
  intermediate,
  high,
  critical,
}

extension RiskCategoryExtension on RiskCategory {
  String get displayName {
    switch (this) {
      case RiskCategory.low:
        return 'Low Risk';
      case RiskCategory.intermediate:
        return 'Intermediate Risk';
      case RiskCategory.high:
        return 'High Risk';
      case RiskCategory.critical:
        return 'Critical Risk';
    }
  }

  Color get color {
    switch (this) {
      case RiskCategory.low:
        return const Color(0xFF10B981);
      case RiskCategory.intermediate:
        return const Color(0xFFF59E0B);
      case RiskCategory.high:
        return const Color(0xFFF97316);
      case RiskCategory.critical:
        return const Color(0xFFEF4444);
    }
  }
}

class PatientParameters {
  final String id;
  final String name;
  final String mrn;
  final double gestationalAgeWeeks; // e.g. 38.5
  final DateTime birthDateTime;

  // Layer 1: Maternal Risk Factors
  final double maternalTemperature; // Celsius
  final double romHours;
  final bool gbsPositive;
  final bool adequateIntrapartumAntibiotics;
  final bool clinicalChorioamnionitis;
  final String deliveryMode; // "Vaginal" or "Caesarean"

  // Layer 2: Neonatal Clinical Status
  final String respiratoryDistress; // "None", "Mild", "Severe"
  final String oxygenNeed; // "None", "Supplemental", "CPAP/Ventilation"
  final int apgar5Min;
  final bool poorPerfusion;
  final double neonatalTemperature; // Celsius
  final String neurologicalStatus; // "Normal", "Irritable/Lethargic", "Seizures"

  // Layer 3: Laboratory Evidence
  final double? wbcCount; // cells/mm3
  final double? itRatio; // Immature to Total ratio (e.g. 0.15)
  final double? plateletCount; // cells/mm3
  final double? crpLevel; // mg/L
  final double? pctLevel; // ng/mL
  final bool? bloodCulturePositive;

  PatientParameters({
    required this.id,
    required this.name,
    required this.mrn,
    required this.gestationalAgeWeeks,
    required this.birthDateTime,
    required this.maternalTemperature,
    required this.romHours,
    required this.gbsPositive,
    required this.adequateIntrapartumAntibiotics,
    required this.clinicalChorioamnionitis,
    required this.deliveryMode,
    required this.respiratoryDistress,
    required this.oxygenNeed,
    required this.apgar5Min,
    required this.poorPerfusion,
    required this.neonatalTemperature,
    required this.neurologicalStatus,
    this.wbcCount,
    this.itRatio,
    this.plateletCount,
    this.crpLevel,
    this.pctLevel,
    this.bloodCulturePositive,
  });

  // CopyWith helper
  PatientParameters copyWith({
    String? id,
    String? name,
    String? mrn,
    double? gestationalAgeWeeks,
    DateTime? birthDateTime,
    double? maternalTemperature,
    double? romHours,
    bool? gbsPositive,
    bool? adequateIntrapartumAntibiotics,
    bool? clinicalChorioamnionitis,
    String? deliveryMode,
    String? respiratoryDistress,
    String? oxygenNeed,
    int? apgar5Min,
    bool? poorPerfusion,
    double? neonatalTemperature,
    String? neurologicalStatus,
    double? wbcCount,
    double? itRatio,
    double? plateletCount,
    double? crpLevel,
    double? pctLevel,
    bool? bloodCulturePositive,
  }) {
    return PatientParameters(
      id: id ?? this.id,
      name: name ?? this.name,
      mrn: mrn ?? this.mrn,
      gestationalAgeWeeks: gestationalAgeWeeks ?? this.gestationalAgeWeeks,
      birthDateTime: birthDateTime ?? this.birthDateTime,
      maternalTemperature: maternalTemperature ?? this.maternalTemperature,
      romHours: romHours ?? this.romHours,
      gbsPositive: gbsPositive ?? this.gbsPositive,
      adequateIntrapartumAntibiotics: adequateIntrapartumAntibiotics ?? this.adequateIntrapartumAntibiotics,
      clinicalChorioamnionitis: clinicalChorioamnionitis ?? this.clinicalChorioamnionitis,
      deliveryMode: deliveryMode ?? this.deliveryMode,
      respiratoryDistress: respiratoryDistress ?? this.respiratoryDistress,
      oxygenNeed: oxygenNeed ?? this.oxygenNeed,
      apgar5Min: apgar5Min ?? this.apgar5Min,
      poorPerfusion: poorPerfusion ?? this.poorPerfusion,
      neonatalTemperature: neonatalTemperature ?? this.neonatalTemperature,
      neurologicalStatus: neurologicalStatus ?? this.neurologicalStatus,
      wbcCount: wbcCount ?? this.wbcCount,
      itRatio: itRatio ?? this.itRatio,
      plateletCount: plateletCount ?? this.plateletCount,
      crpLevel: crpLevel ?? this.crpLevel,
      pctLevel: pctLevel ?? this.pctLevel,
      bloodCulturePositive: bloodCulturePositive ?? this.bloodCulturePositive,
    );
  }
}

class ScoreDriver {
  final String name;
  final int points;
  final String reason;
  final int layer;
  final double contributionPercent;

  ScoreDriver({
    required this.name,
    required this.points,
    required this.reason,
    this.layer = 1,
    this.contributionPercent = 0,
  });
}

enum LayerConcern { low, moderate, high }

class EoscalResult {
  final int totalScore;
  final int layer1Score;
  final int layer2Score;
  final int layer3Score;
  final double probabilityPer1000;
  final bool belowEoscalScope;
  final LayerConcern layer1Concern;
  final LayerConcern layer2Concern;
  final LayerConcern layer3Concern;
  final String? combinationNote;
  final RiskCategory riskCategory;
  final List<ScoreDriver> layer1Drivers;
  final List<ScoreDriver> layer2Drivers;
  final List<ScoreDriver> layer3Drivers;
  final List<String> warnings;

  EoscalResult({
    required this.totalScore,
    required this.layer1Score,
    required this.layer2Score,
    required this.layer3Score,
    required this.probabilityPer1000,
    required this.belowEoscalScope,
    required this.layer1Concern,
    required this.layer2Concern,
    required this.layer3Concern,
    this.combinationNote,
    required this.riskCategory,
    required this.layer1Drivers,
    required this.layer2Drivers,
    required this.layer3Drivers,
    required this.warnings,
  });

  List<ScoreDriver> get allDrivers => [...layer1Drivers, ...layer2Drivers, ...layer3Drivers];
}

class EoscalCalculator {
  static EoscalResult calculate(PatientParameters patient) {
    final List<ScoreDriver> layer1 = [];
    final List<ScoreDriver> layer2 = [];
    final List<ScoreDriver> layer3 = [];
    final List<String> warnings = [];

    // Preterm Warning (<35 weeks) — EOSCAL 2024 scope restriction
    final belowScope = patient.gestationalAgeWeeks < 35.0;
    if (belowScope) {
      warnings.add(
        'EOSCAL 2024 validated for ≥35 weeks. Layer 2 thresholds unvalidated below 35 weeks — route to senior clinical review.',
      );
    } else if (patient.gestationalAgeWeeks < 37.0) {
      warnings.add(
        'Patient is late preterm (<37 weeks). Enhanced monitoring advised.',
      );
    }

    // --- LAYER 1: MATERNAL RISK FACTORS ---
    // Maternal Temperature
    if (patient.maternalTemperature >= 39.0) {
      layer1.add(ScoreDriver(
        name: 'Maternal High Fever',
        points: 2,
        reason: 'Maternal temperature is severe: ${patient.maternalTemperature}°C (>=39.0°C)',
      ));
    } else if (patient.maternalTemperature >= 38.0) {
      layer1.add(ScoreDriver(
        name: 'Maternal Fever',
        points: 1,
        reason: 'Maternal temperature is elevated: ${patient.maternalTemperature}°C (38.0-38.9°C)',
      ));
    }

    // Rupture of Membranes
    if (patient.romHours > 24) {
      layer1.add(ScoreDriver(
        name: 'Prolonged ROM (>24h)',
        points: 2,
        reason: 'Rupture of membranes duration is ${patient.romHours} hours (>24 hours)',
      ));
    } else if (patient.romHours >= 18) {
      layer1.add(ScoreDriver(
        name: 'ROM >= 18h',
        points: 1,
        reason: 'Rupture of membranes duration is ${patient.romHours} hours (18-24 hours)',
      ));
    }

    // GBS Status & Antibiotics
    if (patient.gbsPositive) {
      if (!patient.adequateIntrapartumAntibiotics) {
        layer1.add(ScoreDriver(
          name: 'GBS + Inadequate Abx',
          points: 1,
          reason: 'Maternal GBS positive with inadequate/no intrapartum antibiotic prophylaxis',
        ));
      } else {
        layer1.add(ScoreDriver(
          name: 'GBS + Adequate Abx',
          points: 0,
          reason: 'Maternal GBS positive but received adequate intrapartum prophylaxis (risk mitigated)',
        ));
      }
    }

    // Clinical Chorioamnionitis
    if (patient.clinicalChorioamnionitis) {
      layer1.add(ScoreDriver(
        name: 'Chorioamnionitis',
        points: 2,
        reason: 'Clinical diagnosis of maternal chorioamnionitis',
      ));
    }

    // Gestational Age baseline risk
    if (patient.gestationalAgeWeeks < 37.0) {
      layer1.add(ScoreDriver(
        name: 'Late Preterm Baseline',
        points: 1,
        reason: 'Gestational age is ${patient.gestationalAgeWeeks} weeks (<37 weeks)',
      ));
    }

    // --- LAYER 2: NEONATAL CLINICAL STATUS ---
    // Respiratory Distress
    if (patient.respiratoryDistress == 'Severe') {
      layer2.add(ScoreDriver(
        name: 'Severe Resp. Distress',
        points: 2,
        reason: 'Severe respiratory distress (persistent retracting, grunting)',
      ));
    } else if (patient.respiratoryDistress == 'Mild') {
      layer2.add(ScoreDriver(
        name: 'Mild Resp. Distress',
        points: 1,
        reason: 'Mild/transient respiratory distress or tachypnea',
      ));
    }

    // Oxygen/CPAP
    if (patient.oxygenNeed == 'CPAP/Ventilation') {
      layer2.add(ScoreDriver(
        name: 'CPAP/Ventilation',
        points: 2,
        reason: 'Neonatal support requires CPAP or mechanical ventilation',
      ));
    } else if (patient.oxygenNeed == 'Supplemental') {
      layer2.add(ScoreDriver(
        name: 'Supplemental Oxygen',
        points: 1,
        reason: 'Neonatal support requires supplemental oxygen',
      ));
    }

    // APGAR
    if (patient.apgar5Min < 4) {
      layer2.add(ScoreDriver(
        name: 'Low APGAR (<4)',
        points: 2,
        reason: '5-minute APGAR score is critical: ${patient.apgar5Min}',
      ));
    } else if (patient.apgar5Min <= 6) {
      layer2.add(ScoreDriver(
        name: 'Mod. APGAR (4-6)',
        points: 1,
        reason: '5-minute APGAR score is intermediate: ${patient.apgar5Min}',
      ));
    }

    // Perfusion
    if (patient.poorPerfusion) {
      layer2.add(ScoreDriver(
        name: 'Poor Perfusion',
        points: 1,
        reason: 'Signs of poor cardiovascular perfusion (capillary refill time >3s, hypotension)',
      ));
    }

    // Temperature Instability
    if (patient.neonatalTemperature < 36.5) {
      layer2.add(ScoreDriver(
        name: 'Hypothermia',
        points: 1,
        reason: 'Neonatal temperature is low: ${patient.neonatalTemperature}°C (<36.5°C)',
      ));
    } else if (patient.neonatalTemperature > 37.5) {
      layer2.add(ScoreDriver(
        name: 'Hyperthermia',
        points: 1,
        reason: 'Neonatal temperature is elevated: ${patient.neonatalTemperature}°C (>37.5°C)',
      ));
    }

    // Neurological Status
    if (patient.neurologicalStatus == 'Seizures') {
      layer2.add(ScoreDriver(
        name: 'Neonatal Seizures',
        points: 2,
        reason: 'Observed seizures or status epilepticus',
      ));
    } else if (patient.neurologicalStatus == 'Irritable/Lethargic') {
      layer2.add(ScoreDriver(
        name: 'Lethargy/Irritability',
        points: 1,
        reason: 'Neurological symptoms present: irritability, lethargy, or hypotonia',
      ));
    }

    // --- LAYER 3: LABORATORY EVIDENCE ---
    // WBC
    if (patient.wbcCount != null) {
      if (patient.wbcCount! < 5000 || patient.wbcCount! > 25000) {
        layer3.add(ScoreDriver(
          name: 'Abnormal WBC',
          points: 1,
          reason: 'Abnormal white blood cell count: ${patient.wbcCount!.toStringAsFixed(0)} cells/mm³',
        ));
      }
    }

    // I:T Ratio
    if (patient.itRatio != null) {
      if (patient.itRatio! >= 0.20) {
        layer3.add(ScoreDriver(
          name: 'Elevated I:T Ratio',
          points: 1,
          reason: 'Immature to Total neutrophil ratio is high: ${patient.itRatio!.toStringAsFixed(2)} (>=0.20)',
        ));
      }
    }

    // Platelets
    if (patient.plateletCount != null) {
      if (patient.plateletCount! < 100000) {
        layer3.add(ScoreDriver(
          name: 'Thrombocytopenia',
          points: 1,
          reason: 'Platelet count is low: ${patient.plateletCount!.toStringAsFixed(0)} cells/mm³ (<100,000)',
        ));
      }
    }

    // CRP
    if (patient.crpLevel != null) {
      if (patient.crpLevel! >= 30.0) {
        layer3.add(ScoreDriver(
          name: 'Severely High CRP',
          points: 2,
          reason: 'C-Reactive Protein level is severely high: ${patient.crpLevel} mg/L (>=30 mg/L)',
        ));
      } else if (patient.crpLevel! >= 10.0) {
        layer3.add(ScoreDriver(
          name: 'Elevated CRP',
          points: 1,
          reason: 'C-Reactive Protein level is elevated: ${patient.crpLevel} mg/L (10-30 mg/L)',
        ));
      }
    }

    // Procalcitonin
    if (patient.pctLevel != null) {
      if (patient.pctLevel! >= 0.5) {
        layer3.add(ScoreDriver(
          name: 'Elevated PCT',
          points: 1,
          reason: 'Procalcitonin level is elevated: ${patient.pctLevel} ng/mL (>=0.5 ng/mL)',
        ));
      }
    }

    // Blood Culture Status
    if (patient.bloodCulturePositive == true) {
      layer3.add(ScoreDriver(
        name: 'Positive Blood Culture',
        points: 3, // Severe lab finding
        reason: 'Blood culture indicates bacterial growth',
      ));
      warnings.add('CRITICAL: Positive blood culture confirms bacteremia. Initiate sepsis protocol immediately.');
    }

    // Calculate Total Score
    int layer1Score = 0;
    for (var driver in layer1) {
      layer1Score += driver.points;
    }
    int layer2Score = 0;
    for (var driver in layer2) {
      layer2Score += driver.points;
    }
    int layer3Score = 0;
    for (var driver in layer3) {
      layer3Score += driver.points;
    }
    int score = layer1Score + layer2Score + layer3Score;

    // Determine Risk Category
    RiskCategory category;
    if (patient.bloodCulturePositive == true) {
      score = score < 10 ? 10 : score;
    }
    if (score >= 10 || patient.bloodCulturePositive == true) {
      category = RiskCategory.critical;
    } else if (score >= 7) {
      category = RiskCategory.high;
    } else if (score >= 4) {
      category = RiskCategory.intermediate;
    } else {
      category = RiskCategory.low;
    }

    final probability = _calculateProbabilityPer1000(patient, layer1Score, layer2Score, layer3Score);
    final combinationNote = _layerCombinationNote(layer1Score, layer2Score, layer3Score);

    final layer1Concern = _layerConcern(layer1Score);
    final layer2Concern = _layerConcern(layer2Score);
    final layer3Concern = _layerConcern(layer3Score);

    final layer1WithPct = _applyContributions(layer1, score, 1);
    final layer2WithPct = _applyContributions(layer2, score, 2);
    final layer3WithPct = _applyContributions(layer3, score, 3);

    return EoscalResult(
      totalScore: score,
      layer1Score: layer1Score,
      layer2Score: layer2Score,
      layer3Score: layer3Score,
      probabilityPer1000: probability,
      belowEoscalScope: belowScope,
      layer1Concern: layer1Concern,
      layer2Concern: layer2Concern,
      layer3Concern: layer3Concern,
      combinationNote: combinationNote,
      riskCategory: category,
      layer1Drivers: layer1WithPct,
      layer2Drivers: layer2WithPct,
      layer3Drivers: layer3WithPct,
      warnings: warnings,
    );
  }

  static LayerConcern _layerConcern(int score) {
    if (score >= 5) return LayerConcern.high;
    if (score >= 2) return LayerConcern.moderate;
    return LayerConcern.low;
  }

  static List<ScoreDriver> _applyContributions(List<ScoreDriver> drivers, int total, int layer) {
    if (total == 0) {
      return drivers
          .map((d) => ScoreDriver(
                name: d.name,
                points: d.points,
                reason: d.reason,
                layer: layer,
                contributionPercent: 0,
              ))
          .toList();
    }
    return drivers
        .map((d) => ScoreDriver(
              name: d.name,
              points: d.points,
              reason: d.reason,
              layer: layer,
              contributionPercent: (d.points / total) * 100,
            ))
        .toList();
  }

  /// Simplified EOSCAL logistic probability per 1000 births, scaled by L2/L3.
  static double _calculateProbabilityPer1000(
    PatientParameters patient,
    int l1,
    int l2,
    int l3,
  ) {
    double logit = -7.0;
    if (patient.gestationalAgeWeeks < 37) logit += 0.8;
    if (patient.maternalTemperature >= 38.0) logit += 0.6;
    if (patient.maternalTemperature >= 39.0) logit += 0.4;
    if (patient.romHours >= 18) logit += 0.5;
    if (patient.romHours >= 24) logit += 0.3;
    if (patient.gbsPositive && !patient.adequateIntrapartumAntibiotics) logit += 0.7;
    if (patient.clinicalChorioamnionitis) logit += 1.2;
    if (patient.adequateIntrapartumAntibiotics) logit -= 0.5;

    final baseProb = (1000 * (1 / (1 + _exp(-logit)))).clamp(0.1, 50.0);
    final scale = 1.0 + (l2 * 0.15) + (l3 * 0.25);
    return double.parse((baseProb * scale).toStringAsFixed(2));
  }

  static double _exp(double x) {
    return math.exp(x);
  }

  static String? _layerCombinationNote(int l1, int l2, int l3) {
    final maternal = _layerConcern(l1);
    final clinical = _layerConcern(l2);
    final lab = _layerConcern(l3);

    if (maternal == LayerConcern.high &&
        clinical == LayerConcern.low &&
        lab == LayerConcern.low) {
      return 'High maternal risk alone — observe closely; empirical antibiotics not justified without clinical/lab corroboration.';
    }
    if (maternal.index >= LayerConcern.moderate.index &&
        clinical.index >= LayerConcern.moderate.index &&
        lab.index >= LayerConcern.moderate.index) {
      return 'Concordant evidence across all three layers — strong consideration for EOS evaluation and empirical antibiotics.';
    }
    if (maternal == LayerConcern.low &&
        (clinical == LayerConcern.high || lab == LayerConcern.high)) {
      return 'Clinical/laboratory layers override reassuring maternal history — treat as evolving sepsis.';
    }
    return null;
  }

  static String layerConcernLabel(LayerConcern c) {
    switch (c) {
      case LayerConcern.low:
        return 'Low';
      case LayerConcern.moderate:
        return 'Moderate';
      case LayerConcern.high:
        return 'High';
    }
  }

  // Generate recommendation text based on Guideline (NICE, AAP, WHO) and Risk Level
  static String getRecommendation({
    required String guideline,
    required RiskCategory riskCategory,
    String? combinationNote,
  }) {
    final base = _baseRecommendation(guideline, riskCategory);
    if (combinationNote != null && combinationNote.isNotEmpty) {
      return '$base\n\nLayer combination: $combinationNote';
    }
    return base;
  }

  static String _baseRecommendation(String guideline, RiskCategory riskCategory) {
    switch (guideline) {
      case 'NICE':
        switch (riskCategory) {
          case RiskCategory.low:
            return 'NICE NG195: Routine postnatal care. Monitor vital signs every 12 hours for the first 24 hours. No antibiotics required.';
          case RiskCategory.intermediate:
            return 'NICE NG195: Close observation. Monitor heart rate, respiration, and temperature every 4 hours. Re-evaluate laboratory markers (CRP/cultures) in 12-24 hours. Start empirical antibiotics if clinical status deteriorates.';
          case RiskCategory.high:
            return 'NICE NG195: Initiate empiric intravenous antibiotics (Benzylpenicillin + Gentamicin) within 1 hour. Obtain blood culture and baseline blood tests (FBC, CRP) prior to administration.';
          case RiskCategory.critical:
            return 'NICE NG195: CRITICAL. Immediate IV antibiotics (Benzylpenicillin + Gentamicin), support airway/cardiovascular system, transfer to Neonatal Intensive Care Unit (NICU).';
        }
      case 'AAP':
        switch (riskCategory) {
          case RiskCategory.low:
            return 'AAP 2023: Standard newborn care and routine surveillance. Observe in hospital for 24-48 hours.';
          case RiskCategory.intermediate:
            return 'AAP 2023: Categorize by clinical status. If clinically well, execute enhanced monitoring (observe closely). If showing clinical signs of sepsis, obtain blood culture and start empiric IV Ampicillin + Gentamicin.';
          case RiskCategory.high:
            return 'AAP 2023: Empiric antibiotic therapy (Ampicillin + Gentamicin) is indicated. Draw blood culture. Monitor neonate closely in NICU setting.';
          case RiskCategory.critical:
            return 'AAP 2023: CRITICAL. Urgent blood culture, start empiric broad-spectrum antibiotics, support ventilation and perfusion. Continuous physiological monitoring.';
        }
      case 'WHO':
      default:
        switch (riskCategory) {
          case RiskCategory.low:
            return 'WHO Newborn Care: Support early exclusive breastfeeding. Maintain thermal care (skin-to-skin). Clean umbilical cord care. Monitor for danger signs.';
          case RiskCategory.intermediate:
            return 'WHO Sepsis Guidelines: Close monitoring. If infant has any "danger signs" (poor feeding, convulsions, fast breathing, severe chest indrawing, high temp, etc.), start first-line IM/IV Ampicillin + Gentamicin and refer to secondary care.';
          case RiskCategory.high:
            return 'WHO Sepsis Guidelines: Immediate initiation of first-line injectable antibiotics (Ampicillin + Gentamicin). Provide supportive oxygen if respiratory distress is present. Manage hypoglycemia and hypothermia.';
          case RiskCategory.critical:
            return 'WHO Sepsis Guidelines: CRITICAL. Immediate first-line injectable antibiotics, urgent hospital transfer. Provide advanced respiratory support, IV fluids, and monitoring.';
        }
    }
  }
}
