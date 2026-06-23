import '../deidentify.dart';
import '../gemini_service.dart';
import '../guidelines_data.dart';
import '../models/rag_chunk.dart';
import '../rag_service.dart';
import '../../domain/eoscal_calculator.dart';
import 'api_client.dart';
import 'evidence_api.dart';

class ExplanationApi {
  final ApiClient _client;
  final EvidenceApi _evidenceApi;

  ExplanationApi({ApiClient? client, EvidenceApi? evidenceApi})
      : _client = client ?? apiClient,
        _evidenceApi = evidenceApi ?? EvidenceApi();

  Future<ClinicalExplanation> generate({
    required String encounterId,
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
  }) async {
    try {
      if (!await _client.isBackendAvailable()) {
        return _offlineExplanation(patient, result, activeGuideline);
      }

      final response = await _client.dio.post(
        '/api/v1/encounters/$encounterId/explanation',
        data: {
          'risk_result': buildRiskPayload(patient, result),
          'active_guideline': activeGuideline,
        },
      );

      return ClinicalExplanation.fromJson(response.data as Map<String, dynamic>);
    } catch (_) {
      return _offlineExplanation(patient, result, activeGuideline);
    }
  }

  Future<ClinicalExplanation> _offlineExplanation(
    PatientParameters patient,
    EoscalResult result,
    String activeGuideline,
  ) async {
    final chunks = RagService.retrieveRelevantChunks(
      patient: patient,
      result: result,
      activeGuideline: activeGuideline,
      limit: 5,
    );

    final guidelineChunks = chunks
        .map((c) => GuidelineChunk(
              id: c.id,
              source: c.source,
              section: c.section,
              content: c.content,
              keywords: c.keywords,
            ))
        .toList();

    final markdown = await GeminiService.generateExplanation(
      patient: patient,
      result: result,
      activeGuideline: activeGuideline,
      retrievedChunks: guidelineChunks,
    );

    final ragChunks = chunks
        .asMap()
        .entries
        .map((e) => RagChunk.fromGuidelineChunk(e.value, score: 0.8 - e.key * 0.05))
        .toList();

    return ClinicalExplanation(
      clinicalSummary: markdown.split('\n').firstWhere(
            (l) => l.trim().isNotEmpty && !l.startsWith('#'),
            orElse: () => 'Offline clinical explanation generated from local guidelines.',
          ),
      perDriverExplanations: result.allDrivers
          .take(6)
          .map((d) => {'factor': d.name, 'explanation': d.reason})
          .toList(),
      recommendedActions: [
        EoscalCalculator.getRecommendation(
          guideline: activeGuideline,
          riskCategory: result.riskCategory,
          combinationNote: result.combinationNote,
        ),
      ],
      evidenceSummary: ragChunks.isNotEmpty ? ragChunks.first.chunkText : '',
      citationList: ragChunks
          .map((c) => CitationItem(
                source: c.sourceName,
                section: c.section,
                chunkId: c.chunkId,
                similarityScore: c.similarityScore,
              ))
          .toList(),
      confidenceDisclaimer:
          'Clinical judgement of the responsible clinician takes precedence.',
      ragChunks: ragChunks,
      modelVersion: 'offline-fallback',
      generatedOffline: true,
      fallbackUsed: true,
    );
  }
}

final explanationApi = ExplanationApi();
