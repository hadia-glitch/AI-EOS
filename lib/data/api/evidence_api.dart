import '../deidentify.dart';
import '../guidelines_data.dart';
import '../models/rag_chunk.dart';
import '../rag_service.dart';
import '../../domain/eoscal_calculator.dart';
import 'api_client.dart';

class EvidenceApi {
  final ApiClient _client;

  EvidenceApi({ApiClient? client}) : _client = client ?? apiClient;

  Future<List<RagChunk>> search({
    required String query,
    String activeGuideline = 'NICE',
    List<String> sourceFilters = const [],
    int limit = 5,
  }) async {
    try {
      if (!await _client.isBackendAvailable()) {
        return _offlineSearch(query, activeGuideline, limit);
      }

      final response = await _client.dio.post(
        '/api/v1/evidence/search',
        data: {
          'query': query,
          'active_guideline': activeGuideline,
          'source_filters': sourceFilters,
          'limit': limit,
        },
      );

      final list = response.data as List<dynamic>;
      return list.map((e) => RagChunk.fromJson(e as Map<String, dynamic>)).toList();
    } catch (_) {
      return _offlineSearch(query, activeGuideline, limit);
    }
  }

  Future<List<RagChunk>> retrieveForAssessment({
    required String encounterId,
    required PatientParameters patient,
    required EoscalResult result,
    String activeGuideline = 'NICE',
  }) async {
    try {
      if (!await _client.isBackendAvailable()) {
        return _offlineAssessment(patient, result, activeGuideline);
      }

      final response = await _client.dio.post(
        '/api/v1/encounters/$encounterId/evidence',
        data: {
          'risk_result': buildRiskPayload(patient, result),
          'active_guideline': activeGuideline,
        },
      );

      final list = response.data as List<dynamic>;
      return list.map((e) => RagChunk.fromJson(e as Map<String, dynamic>)).toList();
    } catch (_) {
      return _offlineAssessment(patient, result, activeGuideline);
    }
  }

  List<RagChunk> _offlineSearch(String query, String guideline, int limit) {
    final keywords = query.toLowerCase().split(' ');
    final scored = <RagChunk>[];

    for (final chunk in GuidelinesData.chunks) {
      var score = 0.0;
      for (final word in keywords) {
        if (word.length > 3 &&
            (chunk.content.toLowerCase().contains(word) ||
                chunk.keywords.any((k) => k.contains(word)))) {
          score += 1.0;
        }
      }
      if (chunk.source.toUpperCase() == guideline.toUpperCase()) score += 2.0;
      if (score > 0) {
        scored.add(RagChunk.fromGuidelineChunk(chunk, score: score / 10));
      }
    }

    scored.sort((a, b) => b.similarityScore.compareTo(a.similarityScore));
    if (scored.isEmpty) {
      return GuidelinesData.chunks
          .take(limit)
          .map((c) => RagChunk.fromGuidelineChunk(c))
          .toList();
    }
    return scored.take(limit).toList();
  }

  List<RagChunk> _offlineAssessment(
    PatientParameters patient,
    EoscalResult result,
    String guideline,
  ) {
    return RagService.retrieveRelevantChunks(
      patient: patient,
      result: result,
      activeGuideline: guideline,
      limit: 5,
    )
        .asMap()
        .entries
        .map((e) => RagChunk.fromGuidelineChunk(
              e.value,
              score: 0.9 - (e.key * 0.1),
            ))
        .toList();
  }
}

final evidenceApi = EvidenceApi();
