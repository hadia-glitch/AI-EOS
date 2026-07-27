import 'dart:developer' as developer;
import '../deidentify.dart';
import '../guidelines_data.dart';
import '../models/rag_chunk.dart';
import '../rag_service.dart';
import '../../domain/eoscal_calculator.dart';
import 'api_client.dart';

class EvidenceApi {
  final ApiClient _client;

  EvidenceApi({ApiClient? client}) : _client = client ?? apiClient;

  // ── Evidence search ───────────────────────────────────────────────────────

  Future<List<RagChunk>> search({
    required String query,
    String activeGuideline = 'NICE',
    List<String> sourceFilters = const [],
    int limit = 8,
    // When set (a patient is loaded — see EvidenceSearchScreen's picker),
    // the backend biases retrieval itself with that patient's current
    // symptoms + sustained trends (rag/patient_context_builder.py), not
    // just the post-retrieval AI overview/card narration. Null = "ask
    // generally", unchanged behavior.
    String? encounterId,
  }) async {
    final backendAvailable = await _client.isBackendAvailable();
    developer.log(
      '[EvidenceApi] search backend=$backendAvailable query="$query"',
      name: 'EvidenceApi',
    );

    if (!backendAvailable) {
      return _deduplicate(_offlineSearch(query, activeGuideline, limit));
    }

    try {
      final response = await _client.dio.post(
        '/api/v1/evidence/search',
        data: {
          'query': query,
          'active_guideline': activeGuideline,
          'source_filters': sourceFilters,
          'limit': limit,
          if (encounterId != null) 'encounter_id': encounterId,
        },
      );
      final list = response.data as List<dynamic>;
      return _deduplicate(
        list.map((e) => RagChunk.fromJson(e as Map<String, dynamic>)).toList(),
      );
    } on Exception catch (e, st) {
      developer.log('[EvidenceApi] search failed',
          name: 'EvidenceApi', error: e, stackTrace: st);
      return _deduplicate(_offlineSearch(query, activeGuideline, limit));
    }
  }

  // ── Evidence AI overview (backend uses its .env Gemini key) ──────────────

  /// Returns the AI overview paragraph.
  /// Empty string means Gemini is not configured on the backend — caller
  /// should show nothing (not "add API key" — the user has no key to add).
  Future<String> fetchEvidenceOverview({
    required String query,
    required String activeGuideline,
    required List<RagChunk> chunks,
    Map<String, dynamic>? patientContext,
  }) async {
    final backendAvailable = await _client.isBackendAvailable();
    if (!backendAvailable) return '';

    try {
      final response = await _client.dio.post(
        '/api/v1/evidence/overview',
        data: {
          'query': query,
          'active_guideline': activeGuideline,
          'chunks': chunks
              .map((c) => {
                    'source': c.source,
                    'source_name': c.sourceName,
                    'section': c.section,
                    'chunk_text': c.chunkText,
                  })
              .toList(),
          'patient_context': ?patientContext,
        },
      );
      final data = response.data as Map<String, dynamic>;
      return data['overview'] as String? ?? '';
    } on Exception catch (e, st) {
      developer.log('[EvidenceApi] fetchEvidenceOverview failed',
          name: 'EvidenceApi', error: e, stackTrace: st);
      return '';
    }
  }

  // ── Evidence AI cards (backend uses its .env Gemini key) ─────────────────

  Future<List<EvidenceCardResult>> fetchEvidenceCards({
    required String query,
    required String activeGuideline,
    required List<RagChunk> chunks,
    Map<String, dynamic>? patientContext,
  }) async {
    final backendAvailable = await _client.isBackendAvailable();
    if (!backendAvailable) {
      return _fallbackCards(chunks);
    }

    try {
      final response = await _client.dio.post(
        '/api/v1/evidence/cards',
        data: {
          'query': query,
          'active_guideline': activeGuideline,
          'chunks': chunks
              .map((c) => {
                    'source': c.source,
                    'source_name': c.sourceName,
                    'section': c.section,
                    'chunk_text': c.chunkText,
                  })
              .toList(),
          'patient_context': ?patientContext,
        },
      );

      final data = response.data as Map<String, dynamic>;
      final cardList = data['cards'] as List<dynamic>? ?? [];

      return cardList.asMap().entries.map((entry) {
        final j = entry.value as Map<String, dynamic>;
        final idx = (j['index'] as int? ?? entry.key).clamp(0, chunks.length - 1);
        final chunk = chunks[idx];

        // Resolve document URL: prefer backend response, fall back to local map
        final backendUrl = j['document_url'] as String? ?? '';
        final docUrl = backendUrl.isNotEmpty
            ? backendUrl
            : CitationItem.resolveDocumentUrl(chunk.sourceName);

        return EvidenceCardResult(
          headline: j['headline'] as String? ?? chunk.section,
          processedAnswer: j['processed_answer'] as String? ?? chunk.chunkText,
          exactExcerpt: j['exact_excerpt'] as String? ?? '',
          chunk: _toGuidelineChunk(chunk, docUrl),
        );
      }).toList();
    } on Exception catch (e, st) {
      developer.log('[EvidenceApi] fetchEvidenceCards failed',
          name: 'EvidenceApi', error: e, stackTrace: st);
      return _fallbackCards(chunks);
    }
  }

  // ── Assessment evidence ───────────────────────────────────────────────────

  Future<List<RagChunk>> retrieveForAssessment({
    required String encounterId,
    required PatientParameters patient,
    required EoscalResult result,
    String activeGuideline = 'NICE',
  }) async {
    final backendAvailable = await _client.isBackendAvailable();
    if (!backendAvailable) {
      return _deduplicate(_offlineAssessment(patient, result, activeGuideline));
    }

    try {
      final response = await _client.dio.post(
        '/api/v1/encounters/$encounterId/evidence',
        data: {
          'risk_result': buildRiskPayload(patient, result),
          'active_guideline': activeGuideline,
        },
      );
      final list = response.data as List<dynamic>;
      return _deduplicate(
        list.map((e) => RagChunk.fromJson(e as Map<String, dynamic>)).toList(),
      );
    } on Exception catch (e, st) {
      developer.log('[EvidenceApi] retrieveForAssessment failed',
          name: 'EvidenceApi', error: e, stackTrace: st);
      return _deduplicate(_offlineAssessment(patient, result, activeGuideline));
    }
  }

  // ── Offline helpers ───────────────────────────────────────────────────────

  List<RagChunk> _offlineSearch(String query, String guideline, int limit) {
    final keywords = query.toLowerCase().split(RegExp(r'\s+'));
    final scored = <_ScoredChunk>[];

    for (final chunk in GuidelinesData.chunks) {
      var score = 0.0;
      final contentLower = chunk.content.toLowerCase();
      for (final word in keywords) {
        if (word.length > 3 &&
            (contentLower.contains(word) ||
                chunk.keywords.any((k) => k.toLowerCase().contains(word)))) {
          score += 1.0;
        }
      }
      if (chunk.source.toUpperCase() == guideline.toUpperCase()) score += 2.0;
      if (score > 0) scored.add(_ScoredChunk(chunk, score));
    }

    scored.sort((a, b) => b.score.compareTo(a.score));
    return (scored.isEmpty
            ? GuidelinesData.chunks.take(limit)
            : scored.take(limit).map((s) => s.chunk))
        .map((c) => _toRagChunk(c, score: 0.5))
        .toList();
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
    ).asMap().entries.map((e) => _toRagChunk(e.value, score: 0.9 - e.key * 0.1)).toList();
  }

  List<EvidenceCardResult> _fallbackCards(List<RagChunk> chunks) {
    return chunks.map((c) {
      final sentences = c.chunkText.split(RegExp(r'(?<=[.!?])\s+'));
      // Google-style: show the document/section title, not a raw text
      // fragment — matches the backend's _fallback_cards behaviour so the
      // headline looks the same whether the AI-cards call failed (this
      // path) or the backend itself had no LLM provider.
      final headline = c.section.isNotEmpty ? '${c.sourceName} — ${c.section}' : c.sourceName;
      return EvidenceCardResult(
        headline: headline,
        processedAnswer: c.chunkText,
        exactExcerpt: sentences.isNotEmpty ? sentences.first : '',
        chunk: _toGuidelineChunk(c, CitationItem.resolveDocumentUrl(c.sourceName)),
      );
    }).toList();
  }

  RagChunk _toRagChunk(GuidelineChunk chunk, {double score = 0.5}) {
    return RagChunk(
      chunkId: chunk.id,
      source: chunk.source,
      sourceName: chunk.source,
      section: chunk.section,
      chunkText: chunk.content,
      similarityScore: score,
      regionTag: 'GLOBAL',
      version: '2023',
    );
  }

  GuidelineChunk _toGuidelineChunk(RagChunk c, String docUrl) {
    final local = GuidelinesData.chunks.cast<GuidelineChunk?>().firstWhere(
          (g) => g!.id == c.chunkId || g.section == c.section,
          orElse: () => null,
        );
    return GuidelineChunk(
      id: c.chunkId,
      source: c.source,
      section: c.section,
      content: c.chunkText,
      keywords: const [],
      documentUrl: local?.documentUrl.isNotEmpty == true ? local!.documentUrl : docUrl,
      fileName: c.fileName,
      pageNumber: c.pageNumber,
    );
  }

  List<RagChunk> _deduplicate(List<RagChunk> chunks) {
    final seen = <String>{};
    return chunks.where((c) => seen.add(c.chunkId)).toList();
  }
}

// ── EvidenceCardResult lives here so evidence_search_screen can import it ────

class EvidenceCardResult {
  final String headline;
  final String processedAnswer;
  final String exactExcerpt;
  final GuidelineChunk chunk;

  const EvidenceCardResult({
    required this.headline,
    required this.processedAnswer,
    required this.exactExcerpt,
    required this.chunk,
  });
}

class _ScoredChunk {
  final GuidelineChunk chunk;
  final double score;
  const _ScoredChunk(this.chunk, this.score);
}

final evidenceApi = EvidenceApi();