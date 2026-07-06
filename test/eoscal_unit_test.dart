import 'package:flutter_test/flutter_test.dart';
import 'package:eos/domain/eoscal_calculator.dart';
import 'package:eos/data/rag_service.dart';

void main() {
  group('EOSCAL 2024 Scoring Engine Tests', () {
    test('Low Risk Scenario (Standard Term Baby, No maternal indicators)', () {
      final patient = PatientParameters(
        id: 'test-1',
        name: 'Normal Baby',
        mrn: 'MRN-001',
        gestationalAgeWeeks: 39.5,
        birthDateTime: DateTime.now().subtract(const Duration(hours: 12)),
        maternalTemperature: 37.0,
        romHours: 4.0,
        gbsPositive: false,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'None',
        oxygenNeed: 'None',
        apgar5Min: 9,
        poorPerfusion: false,
        neonatalTemperature: 36.8,
        neurologicalStatus: 'Normal',
      );

      final result = EoscalCalculator.calculate(patient);

      expect(result.totalScore, equals(0));
      expect(result.riskCategory, equals(RiskCategory.low));
      expect(result.warnings, isEmpty);
    });

    test('Intermediate Risk Scenario (Maternal fever + mild respiratory distress)', () {
      final patient = PatientParameters(
        id: 'test-2',
        name: 'Mod Risk Baby',
        mrn: 'MRN-002',
        gestationalAgeWeeks: 38.0,
        birthDateTime: DateTime.now().subtract(const Duration(hours: 8)),
        maternalTemperature: 38.3, // +1 pt
        romHours: 10.0, // 0 pts
        gbsPositive: false,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'Mild', // +1 pt
        oxygenNeed: 'None', // 0 pts
        apgar5Min: 8, // 0 pts
        poorPerfusion: false,
        neonatalTemperature: 36.6,
        neurologicalStatus: 'Normal',
        wbcCount: 4000, // +1 pt (Abnormal: <5000)
        crpLevel: 15.0, // +1 pt (Elevated: 10-30)
      );

      final result = EoscalCalculator.calculate(patient);

      // 1 (maternal fever) + 1 (mild respiratory distress) + 1 (low WBC) + 1 (elevated CRP) = 4 pts
      expect(result.totalScore, equals(4));
      expect(result.riskCategory, equals(RiskCategory.intermediate));
      expect(result.warnings, isEmpty);
    });

    test('High Risk Scenario (Chorioamnionitis, CPAP, Thrombocytopenia)', () {
      final patient = PatientParameters(
        id: 'test-3',
        name: 'High Risk Baby',
        mrn: 'MRN-003',
        gestationalAgeWeeks: 37.5,
        birthDateTime: DateTime.now().subtract(const Duration(hours: 2)),
        maternalTemperature: 38.5, // +1 pt (Maternal fever)
        romHours: 20.0, // +1 pt (ROM >=18h)
        gbsPositive: true,
        adequateIntrapartumAntibiotics: false, // +1 pt (GBS+ with inadequate abx)
        clinicalChorioamnionitis: true, // +2 pts (Chorio)
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'Severe', // +2 pts (Severe resp distress)
        oxygenNeed: 'CPAP/Ventilation', // +2 pts (CPAP support)
        apgar5Min: 5, // +1 pt (APGAR 4-6)
        poorPerfusion: false,
        neonatalTemperature: 37.7, // +1 pt (Hyperthermia >37.5)
        neurologicalStatus: 'Irritable/Lethargic', // +1 pt (Lethargy)
        wbcCount: 3000, // +1 pt (Abnormal <5000)
        plateletCount: 90000, // +1 pt (Thrombocytopenia <100,000)
      );

      final result = EoscalCalculator.calculate(patient);

      // Points:
      // Maternal Temp (38.5) -> +1
      // ROM (20.0) -> +1
      // GBS+ no abx -> +1
      // Chorio -> +2
      // Resp severe -> +2
      // Oxygen CPAP -> +2
      // APGAR (5) -> +1
      // Neonatal Temp (37.7) -> +1
      // Neuro (Lethargy) -> +1
      // WBC (3000) -> +1
      // Platelets (90000) -> +1
      // Total = 1 + 1 + 1 + 2 + 2 + 2 + 1 + 1 + 1 + 1 + 1 = 14 pts
      expect(result.totalScore, equals(14));
      expect(result.riskCategory, equals(RiskCategory.critical));
    });

    test('Critical Scenario (Positive Blood Culture Alert)', () {
      final patient = PatientParameters(
        id: 'test-4',
        name: 'Sepsis Confirmed Baby',
        mrn: 'MRN-004',
        gestationalAgeWeeks: 38.0,
        birthDateTime: DateTime.now().subtract(const Duration(hours: 24)),
        maternalTemperature: 37.0,
        romHours: 4.0,
        gbsPositive: false,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'None',
        oxygenNeed: 'None',
        apgar5Min: 9,
        poorPerfusion: false,
        neonatalTemperature: 36.8,
        neurologicalStatus: 'Normal',
        bloodCulturePositive: true, // +3 pts
      );

      final result = EoscalCalculator.calculate(patient);

      expect(result.totalScore, equals(10));
      expect(result.riskCategory, equals(RiskCategory.critical));
      expect(result.warnings.length, equals(1));
      expect(result.warnings.first, contains('CRITICAL'));
    });
  });

  group('RAG Search Retrieval Engine Tests', () {
    test('Keyword Matching and Source Boosting', () {
      final patient = PatientParameters(
        id: 'test-5',
        name: 'GBS Risk Baby',
        mrn: 'MRN-005',
        gestationalAgeWeeks: 39.0,
        birthDateTime: DateTime.now().subtract(const Duration(hours: 6)),
        maternalTemperature: 37.0,
        romHours: 4.0,
        gbsPositive: true, // triggers GBS keywords
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'None',
        oxygenNeed: 'None',
        apgar5Min: 9,
        poorPerfusion: false,
        neonatalTemperature: 36.8,
        neurologicalStatus: 'Normal',
      );

      final result = EoscalCalculator.calculate(patient);

      // Search RAG chunks with active guideline = AAP
      final retrievedAAP = RagService.retrieveRelevantChunks(
        patient: patient,
        result: result,
        activeGuideline: 'AAP',
      );

      expect(retrievedAAP, isNotEmpty);
      // AAP GBS chunk should be first or highly ranked due to AAP active source boost
      expect(retrievedAAP.first.source, equals('AAP'));
      expect(retrievedAAP.first.keywords, contains('gbs'));
    });
  });
}
