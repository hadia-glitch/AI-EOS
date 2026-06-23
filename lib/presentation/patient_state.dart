import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../domain/eoscal_calculator.dart';

// State provider for the active guideline (NICE, AAP, or WHO)
final activeGuidelineProvider = StateNotifierProvider<ActiveGuidelineNotifier, String>((ref) {
  return ActiveGuidelineNotifier();
});

class ActiveGuidelineNotifier extends StateNotifier<String> {
  ActiveGuidelineNotifier() : super('NICE') {
    _loadGuideline();
  }

  Future<void> _loadGuideline() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString('active_guideline');
    if (saved != null) {
      state = saved;
    }
  }

  Future<void> setGuideline(String guideline) async {
    state = guideline;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('active_guideline', guideline);
  }
}

// Search filter query provider
final searchQueryProvider = StateProvider<String>((ref) => '');

// Patients list provider
final patientsProvider = StateNotifierProvider<PatientsNotifier, List<PatientParameters>>((ref) {
  return PatientsNotifier();
});

class PatientsNotifier extends StateNotifier<List<PatientParameters>> {
  PatientsNotifier() : super([]) {
    _loadPatients();
  }

  static const String _storageKey = 'patient_records';

  Future<void> _loadPatients() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final String? jsonString = prefs.getString(_storageKey);
      if (jsonString != null) {
        final List<dynamic> decodedList = jsonDecode(jsonString);
        final patients = decodedList.map((json) => _fromJson(json)).toList();
        state = patients;
      } else {
        // Seed default dummy patients for demonstration if list is empty
        _seedDemoPatients();
      }
    } catch (e) {
      // Fallback if load fails
      _seedDemoPatients();
    }
  }

  void _seedDemoPatients() {
    final now = DateTime.now();
    state = [
      PatientParameters(
        id: '1',
        name: 'Baby Boy Smith',
        mrn: 'MRN-2026-001',
        gestationalAgeWeeks: 38.5,
        birthDateTime: now.subtract(const Duration(hours: 14)),
        maternalTemperature: 38.4,
        romHours: 20.0,
        gbsPositive: true,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'Mild',
        oxygenNeed: 'Supplemental',
        apgar5Min: 6,
        poorPerfusion: false,
        neonatalTemperature: 36.8,
        neurologicalStatus: 'Normal',
        wbcCount: 4200,
        itRatio: 0.22,
        plateletCount: 150000,
        crpLevel: 12.0,
        pctLevel: 0.3,
        bloodCulturePositive: false,
      ),
      PatientParameters(
        id: '2',
        name: 'Baby Girl Jones',
        mrn: 'MRN-2026-002',
        gestationalAgeWeeks: 34.2, // Premature warning
        birthDateTime: now.subtract(const Duration(hours: 4)),
        maternalTemperature: 37.2,
        romHours: 6.0,
        gbsPositive: false,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: false,
        deliveryMode: 'Caesarean',
        respiratoryDistress: 'None',
        oxygenNeed: 'None',
        apgar5Min: 9,
        poorPerfusion: false,
        neonatalTemperature: 36.6,
        neurologicalStatus: 'Normal',
      ),
      PatientParameters(
        id: '3',
        name: 'Baby Infant Doe',
        mrn: 'MRN-2026-003',
        gestationalAgeWeeks: 39.0,
        birthDateTime: now.subtract(const Duration(hours: 26)),
        maternalTemperature: 39.2, // Maternal High Fever
        romHours: 26.0, // Prolonged ROM
        gbsPositive: true,
        adequateIntrapartumAntibiotics: false,
        clinicalChorioamnionitis: true, // Chorio
        deliveryMode: 'Vaginal',
        respiratoryDistress: 'Severe', // Severe clinical distress
        oxygenNeed: 'CPAP/Ventilation',
        apgar5Min: 3, // Low APGAR
        poorPerfusion: true, // Shock
        neonatalTemperature: 35.8, // Hypothermia
        neurologicalStatus: 'Seizures', // Seizures
        wbcCount: 29000, // Elevated
        itRatio: 0.31, // Elevated
        plateletCount: 88000, // Thrombocytopenia
        crpLevel: 45.0, // High CRP
        pctLevel: 1.8,
        bloodCulturePositive: true, // Culture positive
      ),
    ];
    _savePatients();
  }

  Future<void> _savePatients() async {
    final prefs = await SharedPreferences.getInstance();
    final listJson = state.map((p) => _toJson(p)).toList();
    await prefs.setString(_storageKey, jsonEncode(listJson));
  }

  Future<void> addPatient(PatientParameters patient) async {
    state = [patient, ...state];
    await _savePatients();
  }

  Future<void> updatePatient(PatientParameters patient) async {
    state = state.map((p) => p.id == patient.id ? patient : p).toList();
    await _savePatients();
  }

  Future<void> deletePatient(String id) async {
    state = state.where((p) => p.id != id).toList();
    await _savePatients();
  }

  // --- JSON Mappers ---
  Map<String, dynamic> _toJson(PatientParameters p) {
    return {
      'id': p.id,
      'name': p.name,
      'mrn': p.mrn,
      'gestationalAgeWeeks': p.gestationalAgeWeeks,
      'birthDateTime': p.birthDateTime.toIso8601String(),
      'maternalTemperature': p.maternalTemperature,
      'romHours': p.romHours,
      'gbsPositive': p.gbsPositive,
      'adequateIntrapartumAntibiotics': p.adequateIntrapartumAntibiotics,
      'clinicalChorioamnionitis': p.clinicalChorioamnionitis,
      'deliveryMode': p.deliveryMode,
      'respiratoryDistress': p.respiratoryDistress,
      'oxygenNeed': p.oxygenNeed,
      'apgar5Min': p.apgar5Min,
      'poorPerfusion': p.poorPerfusion,
      'neonatalTemperature': p.neonatalTemperature,
      'neurologicalStatus': p.neurologicalStatus,
      'wbcCount': p.wbcCount,
      'itRatio': p.itRatio,
      'plateletCount': p.plateletCount,
      'crpLevel': p.crpLevel,
      'pctLevel': p.pctLevel,
      'bloodCulturePositive': p.bloodCulturePositive,
    };
  }

  PatientParameters _fromJson(Map<String, dynamic> json) {
    return PatientParameters(
      id: json['id'],
      name: json['name'],
      mrn: json['mrn'],
      gestationalAgeWeeks: (json['gestationalAgeWeeks'] as num).toDouble(),
      birthDateTime: DateTime.parse(json['birthDateTime']),
      maternalTemperature: (json['maternalTemperature'] as num).toDouble(),
      romHours: (json['romHours'] as num).toDouble(),
      gbsPositive: json['gbsPositive'] as bool,
      adequateIntrapartumAntibiotics: json['adequateIntrapartumAntibiotics'] as bool,
      clinicalChorioamnionitis: json['clinicalChorioamnionitis'] as bool,
      deliveryMode: json['deliveryMode'] as String,
      respiratoryDistress: json['respiratoryDistress'] as String,
      oxygenNeed: json['oxygenNeed'] as String,
      apgar5Min: json['apgar5Min'] as int,
      poorPerfusion: json['poorPerfusion'] as bool,
      neonatalTemperature: (json['neonatalTemperature'] as num).toDouble(),
      neurologicalStatus: json['neurologicalStatus'] as String,
      wbcCount: json['wbcCount'] != null ? (json['wbcCount'] as num).toDouble() : null,
      itRatio: json['itRatio'] != null ? (json['itRatio'] as num).toDouble() : null,
      plateletCount: json['plateletCount'] != null ? (json['plateletCount'] as num).toDouble() : null,
      crpLevel: json['crpLevel'] != null ? (json['crpLevel'] as num).toDouble() : null,
      pctLevel: json['pctLevel'] != null ? (json['pctLevel'] as num).toDouble() : null,
      bloodCulturePositive: json['bloodCulturePositive'] as bool?,
    );
  }
}
