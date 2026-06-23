import 'package:flutter_test/flutter_test.dart';
import 'package:eos/domain/eoscal_calculator.dart';

void main() {
  test('EOSCAL includes layer scores and probability', () {
    final patient = PatientParameters(
      id: 't',
      name: 'Test',
      mrn: 'M1',
      gestationalAgeWeeks: 38.0,
      birthDateTime: DateTime.now(),
      maternalTemperature: 38.5,
      romHours: 20,
      gbsPositive: true,
      adequateIntrapartumAntibiotics: false,
      clinicalChorioamnionitis: false,
      deliveryMode: 'Vaginal',
      respiratoryDistress: 'Mild',
      oxygenNeed: 'None',
      apgar5Min: 8,
      poorPerfusion: false,
      neonatalTemperature: 36.8,
      neurologicalStatus: 'Normal',
    );
    final result = EoscalCalculator.calculate(patient);
    expect(result.layer1Score, greaterThan(0));
    expect(result.probabilityPer1000, greaterThan(0));
    expect(result.allDrivers.every((d) => d.layer >= 1), isTrue);
  });

  test('Below 35 weeks sets scope flag', () {
    final patient = PatientParameters(
      id: 't2',
      name: 'Preterm',
      mrn: 'M2',
      gestationalAgeWeeks: 34.0,
      birthDateTime: DateTime.now(),
      maternalTemperature: 37.0,
      romHours: 4,
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
    expect(result.belowEoscalScope, isTrue);
  });
}
