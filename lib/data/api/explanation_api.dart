import 'dart:developer' as developer;
import 'package:dio/dio.dart';
import '../deidentify.dart';
import '../gemini_service.dart';
import '../guidelines_data.dart';
import '../models/rag_chunk.dart';
import '../rag_service.dart';
import '../../domain/eoscal_calculator.dart';
import 'api_client.dart';

/// Thrown when the backend is reachable but returns an error,
/// or when it is completely unreachable.
/// The UI catches this to show a targeted "check your backend URL" message.
class BackendUnreachableException implements Exception {
  final String backendUrl;
  final String cause;
  BackendUnreachableException(this.backendUrl, this.cause);

  @override
  String toString() => 'Backend unreachable at $backendUrl: $cause';
}

class ExplanationApi {
  final ApiClient _client;
  ExplanationApi({ApiClient? client}) : _client = client ?? apiClient;

  Future<StructuredExplanation> generate({
    required String encounterId,
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    List<Map<String, dynamic>> previousAssessments = const [],
  }) async {
    final id = encounterId.isNotEmpty ? encounterId : patient.id;
    final available = await _client.isBackendAvailable();

    developer.log(
      '[ExplanationApi] backend=$available url=${_client.baseUrl} id=$id',
      name: 'ExplanationApi',
    );

    if (!available) {
      // Throw so the UI can show a targeted error with the URL fix hint.
      // Do NOT silently fall back — clinicians must know this is rule-based.
      throw BackendUnreachableException(
        _client.baseUrl,
        'Health check failed. Is the backend running and is API_BASE_URL correct?',
      );
    }

    try {
      final payload = buildRiskPayload(patient, result);
      final response = await _client.dio.post(
        '/api/v1/encounters/$id/explanation',
        data: {
          'risk_result': payload,
          'active_guideline': activeGuideline,
        },
      );

      if (response.statusCode != 200 || response.data == null) {
        throw Exception('Unexpected status ${response.statusCode}');
      }

      final data = response.data as Map<String, dynamic>;
      developer.log(
        '[ExplanationApi] OK fallback=${data['fallback_used']} model=${data['model_version']}',
        name: 'ExplanationApi',
      );
      return _fromBackendResponse(data, activeGuideline);
    } on DioException catch (e, st) {
      developer.log('[ExplanationApi] Dio error: ${e.message}',
          name: 'ExplanationApi', error: e, stackTrace: st);
      rethrow;
    }
  }

  StructuredExplanation _fromBackendResponse(
    Map<String, dynamic> data,
    String activeGuideline,
  ) {
    final isFallback = data['fallback_used'] as bool? ?? false;
    final modelVersion = data['model_version'] as String? ?? 'unknown';
    final isCached = modelVersion.startsWith('cached:');
    final isSimulated = isFallback || modelVersion == 'rule-based-fallback';

    String sourceLabel;
    if (isSimulated) {
      sourceLabel = 'Rule-based protocol ($activeGuideline) — not AI-generated';
    } else if (isCached) {
      sourceLabel = 'Cached AI · ${modelVersion.replaceFirst('cached:', '')} · $activeGuideline';
    } else {
      sourceLabel = 'AI-generated · $modelVersion · $activeGuideline';
    }

    final actions = (data['recommended_actions'] as List<dynamic>? ?? [])
        .map((e) => e.toString())
        .toList();

    final rawCitations = data['citation_list'] as List<dynamic>? ?? [];
    final citations = rawCitations.map((c) {
      final m = c as Map<String, dynamic>;
      final source = m['source'] as String? ?? '';
      final section = m['section'] as String? ?? '';
      final url = CitationItem.resolveDocumentUrl(source);
      return '$source — $section${url.isNotEmpty ? ': $url' : ''}';
    }).toList();

    final perDriverList = data['per_driver_explanations'] as List<dynamic>? ?? [];
    final driverBreakdown = perDriverList.map((e) {
      final m = e as Map<String, dynamic>;
      return '${m['factor'] ?? ''}: ${m['explanation'] ?? ''}';
    }).join('\n');

    final antibioticPlan = _deriveAntibioticPlan(actions);

    return StructuredExplanation(
      clinicalSummary: data['clinical_summary'] as String? ?? '',
      riskAnalysis: data['evidence_summary'] as String? ?? '',
      driverBreakdown: driverBreakdown,
      recommendedActions: actions,
      antibioticPlan: antibioticPlan,
      monitoringPlan: '',
      escalationCriteria: '',
      guidelineCitations: citations,
      isSimulated: isSimulated,
      sourceLabel: sourceLabel,
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Care plan (Feature 1) — structured 7-section clinical care plan
  // ─────────────────────────────────────────────────────────────────────────

  Future<ClinicalCarePlan> generateCarePlan({
    required String encounterId,
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    List<Map<String, dynamic>> previousAssessments = const [],
  }) async {
    final id = encounterId.isNotEmpty ? encounterId : patient.id;
    final available = await _client.isBackendAvailable();

    developer.log(
      '[ExplanationApi] carePlan backend=$available url=${_client.baseUrl} id=$id',
      name: 'ExplanationApi',
    );

    if (!available) {
      throw BackendUnreachableException(
        _client.baseUrl,
        'Health check failed. Is the backend running and is API_BASE_URL correct?',
      );
    }

    try {
      final payload = buildRiskPayload(patient, result);
      final response = await _client.dio.post(
        '/api/v1/encounters/$id/care-plan',
        data: {
          'risk_result': payload,
          'active_guideline': activeGuideline,
          'previous_assessments': previousAssessments,
        },
      );

      if (response.statusCode != 200 || response.data == null) {
        throw Exception('Unexpected status ${response.statusCode}');
      }

      final data = response.data as Map<String, dynamic>;
      developer.log(
        '[ExplanationApi] carePlan OK fallback=${data['fallback_used']} model=${data['model_version']}',
        name: 'ExplanationApi',
      );
      return _carePlanFromBackendResponse(data, activeGuideline);
    } on DioException catch (e, st) {
      developer.log('[ExplanationApi] carePlan Dio error: ${e.message}',
          name: 'ExplanationApi', error: e, stackTrace: st);
      rethrow;
    }
  }

  ClinicalCarePlan _carePlanFromBackendResponse(
    Map<String, dynamic> data,
    String activeGuideline,
  ) {
    final isFallback = data['fallback_used'] as bool? ?? false;
    final modelVersion = data['model_version'] as String? ?? 'unknown';
    final isCached = modelVersion.startsWith('cached:');
    final isSimulated = isFallback || modelVersion == 'rule-based-fallback';

    String sourceLabel;
    if (isSimulated) {
      sourceLabel = 'Rule-based protocol ($activeGuideline) — not AI-generated';
    } else if (isCached) {
      sourceLabel = 'Cached AI · ${modelVersion.replaceFirst('cached:', '')} · $activeGuideline';
    } else {
      sourceLabel = 'AI-generated · $modelVersion · $activeGuideline';
    }

    final actions = (data['recommended_actions'] as List<dynamic>? ?? [])
        .map((e) => e.toString())
        .toList();

    final rawCitations = data['citation_list'] as List<dynamic>? ?? [];
    final citations = rawCitations.map((c) {
      final m = c as Map<String, dynamic>;
      final source = m['source'] as String? ?? '';
      final section = m['section'] as String? ?? '';
      final url = CitationItem.resolveDocumentUrl(source);
      return '$source — $section${url.isNotEmpty ? ': $url' : ''}';
    }).toList();

    final abx = data['antibiotic_plan'] as Map<String, dynamic>? ?? {};
    final antibioticPlan = AntibioticPlan.fromJson(abx);

    return ClinicalCarePlan(
      clinicalSummary: data['clinical_summary'] as String? ?? '',
      riskAnalysis: data['risk_analysis'] as String? ?? '',
      trendNarrative: data['trend_narrative'] as String? ?? '',
      driverBreakdown: data['driver_breakdown'] as String? ?? '',
      recommendedActions: actions,
      antibioticPlan: antibioticPlan,
      monitoringPlan: data['monitoring_plan'] as String? ?? '',
      escalationCriteria: data['escalation_criteria'] as String? ?? '',
      guidelineCitations: citations,
      isSimulated: isSimulated,
      sourceLabel: sourceLabel,
    );
  }

  AntibioticPlan _deriveAntibioticPlan(List<String> actions) {
    final abxActions = actions.where((a) {
      final l = a.toLowerCase();
      return l.contains('antibiotic') || l.contains('penicillin') ||
          l.contains('gentamicin') || l.contains('ampicillin');
    }).toList();
    if (abxActions.isEmpty) return AntibioticPlan.none();
    String urgency = 'Within 1 hour';
    for (final a in actions) {
      if (a.toLowerCase().contains('immediate') || a.toLowerCase().contains('do not delay')) {
        urgency = 'Immediate';
        break;
      }
    }
    return AntibioticPlan(
      required: true,
      urgency: urgency,
      regimen: abxActions,
      duration: '48–72 hours pending culture results',
      stopCriteria: 'Blood culture negative at 36–48 h AND clinically well AND CRP < 10 mg/L',
    );
  }
}

final explanationApi = ExplanationApi();