import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../data/auth_service.dart';
import '../data/supabase_config.dart';
import '../domain/eoscal_calculator.dart';

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

final searchQueryProvider = StateProvider<String>((ref) => '');

final patientsProvider = StateNotifierProvider<PatientsNotifier, List<PatientParameters>>((ref) {
  return PatientsNotifier();
});

class PatientsNotifier extends StateNotifier<List<PatientParameters>> {
  PatientsNotifier() : super([]) {
    _loadPatients();
  }

  String get _userId => AuthService.instance.currentUser?.id ?? 'anonymous';
  String get _storageKey => 'patient_records_$_userId';
  bool get _canUseSupabase => SupabaseConfig.isConfigured && AuthService.instance.isSignedIn;

  // ─── Load ───────────────────────────────────────────────────────────────────

  Future<void> _loadPatients() async {
    if (_canUseSupabase) {
      try {
        final rows = await AuthService.instance.client
            .from('patient_records')
            .select('payload')
            .eq('owner_user_id', _userId)
            .order('updated_at', ascending: false);
        final patients = (rows as List<dynamic>)
            .map((row) => _fromJson((row as Map<String, dynamic>)['payload'] as Map<String, dynamic>))
            .toList();
        state = patients;
        await _saveLocal();
        return;
      } catch (_) {
        await _loadLocal();
        return;
      }
    }
    await _loadLocal();
  }

  Future<void> refresh() => _loadPatients();

  Future<void> _loadLocal() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final String? jsonString = prefs.getString(_storageKey);
      if (jsonString != null) {
        final List<dynamic> decodedList = jsonDecode(jsonString);
        state = decodedList.map((json) => _fromJson(json as Map<String, dynamic>)).toList();
      } else {
        state = [];
      }
    } catch (_) {
      state = [];
    }
  }

  // ─── Local persistence ───────────────────────────────────────────────────────

  Future<void> _saveLocal() async {
    final prefs = await SharedPreferences.getInstance();
    final listJson = state.map((p) => _toJson(p)).toList();
    await prefs.setString(_storageKey, jsonEncode(listJson));
  }

  // ─── Remote: patient_records (mobile cache) ──────────────────────────────────

  Future<void> _upsertRemote(PatientParameters patient) async {
    if (!_canUseSupabase) return;
    await AuthService.instance.client.from('patient_records').upsert({
      'id': patient.id,
      'owner_user_id': _userId,
      'encounter_ref': patient.mrn,
      'payload': _toJson(patient),
      'updated_at': DateTime.now().toUtc().toIso8601String(),
    });
  }

  // ─── Remote: patient_encounters + clinical_assessments + risk_results ────────

  List<Map<String, dynamic>> _computeAlerts(PatientParameters p) {
    final List<Map<String, dynamic>> alerts = [];
    final res = EoscalCalculator.calculate(p);

    if (res.riskCategory == RiskCategory.critical || res.riskCategory == RiskCategory.high) {
      alerts.add({
        'type': 'risk',
        'priority': res.riskCategory == RiskCategory.critical ? 'CRITICAL' : 'HIGH',
        'message': 'Patient has a high EOSCAL score of ${res.totalScore}. Initiate clinical protocols.',
      });
    }
    if (p.gestationalAgeWeeks < 35.0) {
      alerts.add({
        'type': 'preterm',
        'priority': 'MEDIUM',
        'message': 'Premature gestation (${p.gestationalAgeWeeks} weeks). Interpret calculations with caution.',
      });
    }
    if (p.bloodCulturePositive == true) {
      alerts.add({
        'type': 'culture',
        'priority': 'CRITICAL',
        'message': 'CONFIRMED BACTEREMIA. Blood culture returned positive.',
      });
    }
    return alerts;
  }

  /// Upserts a row in patient_encounters, inserts a new clinical_assessment,
  /// inserts the EOSCAL risk_result, and syncs alerts. All non-fatal.
  Future<void> _upsertClinicalTables(PatientParameters patient) async {
    if (!_canUseSupabase) return;
    final client = AuthService.instance.client;
    final now = DateTime.now().toUtc().toIso8601String();

    try {
      // 1. patient_encounters — one row per patient (upsert on id)
      await client.from('patient_encounters').upsert({
        'id': patient.id,
        'owner_user_id': _userId,
        'encounter_ref': patient.mrn,
        'patient_name': patient.name,
        'birth_date_time': patient.birthDateTime.toIso8601String(),
        'gestational_age_days': (patient.gestationalAgeWeeks * 7).round(),
        'created_at': now,
      });
    } catch (e) {
      debugPrint('patient_encounters upsert failed: $e');
    }

    String? assessmentId;
    try {
      // 2. clinical_assessments — new row per save (insert)
      final assessmentRow = await client.from('clinical_assessments').insert({
        'encounter_id': patient.id,
        'event_type': 'initial',
        'maternal_data': {
          'maternal_temperature': patient.maternalTemperature,
          'rom_hours': patient.romHours,
          'gbs_positive': patient.gbsPositive,
          'adequate_intrapartum_antibiotics': patient.adequateIntrapartumAntibiotics,
          'clinical_chorioamnionitis': patient.clinicalChorioamnionitis,
          'delivery_mode': patient.deliveryMode,
        },
        'neonatal_data': {
          'respiratory_distress': patient.respiratoryDistress,
          'oxygen_need': patient.oxygenNeed,
          'apgar_5_min': patient.apgar5Min,
          'poor_perfusion': patient.poorPerfusion,
          'neonatal_temperature': patient.neonatalTemperature,
          'neurological_status': patient.neurologicalStatus,
          'gestational_age_weeks': patient.gestationalAgeWeeks,
        },
        'lab_data': {
          if (patient.wbcCount != null) 'wbc_count': patient.wbcCount,
          if (patient.itRatio != null) 'it_ratio': patient.itRatio,
          if (patient.plateletCount != null) 'platelet_count': patient.plateletCount,
          if (patient.crpLevel != null) 'crp_level': patient.crpLevel,
          if (patient.pctLevel != null) 'pct_level': patient.pctLevel,
          if (patient.bloodCulturePositive != null) 'blood_culture_positive': patient.bloodCulturePositive,
        },
        'created_at': now,
      }).select('id').single();
      assessmentId = assessmentRow['id'] as String?;
    } catch (e) {
      debugPrint('clinical_assessments insert failed: $e');
    }

    try {
      // 3. risk_results — insert each time a score is computed on save
      final result = EoscalCalculator.calculate(patient);
      final drivers = result.allDrivers
          .map((d) => {
                'name': d.name,
                'points': d.points,
                'reason': d.reason,
                'layer': d.layer,
              })
          .toList();

      await client.from('risk_results').insert({
        'encounter_id': patient.id,
        if (assessmentId != null) 'assessment_id': assessmentId,
        'layer1_score': result.layer1Score,
        'layer2_score': result.layer2Score,
        'layer3_score': result.layer3Score,
        'combined_score': result.totalScore,
        'probability_per_1000': result.probabilityPer1000,
        'category': result.riskCategory.name.toUpperCase(),
        'drivers': drivers,
        'guideline_used': 'NICE',
        'created_at': now,
      });
    } catch (e) {
      debugPrint('risk_results insert failed: $e');
    }

    try {
      // 4. alerts — sync active alerts (delete obsolete, insert current)
      await client.from('alerts').delete().eq('encounter_id', patient.id);
      final activeAlerts = _computeAlerts(patient);
      for (final alert in activeAlerts) {
        await client.from('alerts').insert({
          'encounter_id': patient.id,
          'alert_type': alert['type'],
          'priority': alert['priority'],
          'created_at': now,
          'action_taken': alert['message'],
        });
      }
    } catch (e) {
      debugPrint('alerts sync failed: $e');
    }
  }

  // ─── Public mutators ─────────────────────────────────────────────────────────

  Future<void> addPatient(PatientParameters patient) async {
    state = [patient, ...state.where((p) => p.id != patient.id)];
    await _saveLocal();
    await _upsertRemote(patient);
    await _upsertClinicalTables(patient);
  }

  Future<void> updatePatient(PatientParameters patient) async {
    state = state.map((p) => p.id == patient.id ? patient : p).toList();
    await _saveLocal();
    await _upsertRemote(patient);
    await _upsertClinicalTables(patient);
  }

  Future<void> deletePatient(String id) async {
    state = state.where((p) => p.id != id).toList();
    await _saveLocal();
    if (_canUseSupabase) {
      final client = AuthService.instance.client;
      try {
        await client.from('patient_records').delete().eq('id', id).eq('owner_user_id', _userId);
      } catch (e) {
        debugPrint('patient_records delete failed: $e');
      }
      try {
        // Cascades to clinical_assessments, risk_results via ON DELETE CASCADE
        await client.from('patient_encounters').delete().eq('id', id).eq('owner_user_id', _userId);
      } catch (e) {
        debugPrint('patient_encounters delete failed: $e');
      }
    }
  }

  // ─── Serialisation ───────────────────────────────────────────────────────────

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
      id: json['id'] as String,
      name: json['name'] as String,
      mrn: json['mrn'] as String,
      gestationalAgeWeeks: (json['gestationalAgeWeeks'] as num).toDouble(),
      birthDateTime: DateTime.parse(json['birthDateTime'] as String),
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