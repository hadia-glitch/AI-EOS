class RagChunk {
  final String chunkId;
  final String source;
  final String sourceName;
  final String section;
  final String chunkText;
  final double similarityScore;
  final String regionTag;
  final String version;
  final int? chunkIndex;
  final int? pageNumber;

  const RagChunk({
    required this.chunkId,
    required this.source,
    required this.sourceName,
    required this.section,
    required this.chunkText,
    required this.similarityScore,
    required this.regionTag,
    required this.version,
    this.chunkIndex,
    this.pageNumber,
  });

  factory RagChunk.fromJson(Map<String, dynamic> json) {
    return RagChunk(
      chunkId: json['chunk_id'] as String? ?? json['id'] as String? ?? '',
      source: json['source'] as String? ?? '',
      sourceName: json['source_name'] as String? ?? '',
      section: json['section'] as String? ?? 'General',
      chunkText: json['chunk_text'] as String? ?? '',
      similarityScore: (json['similarity_score'] as num?)?.toDouble() ?? 0.0,
      regionTag: json['region_tag'] as String? ?? 'GLOBAL',
      version: json['version'] as String? ?? '1.0',
      chunkIndex: json['chunk_index'] as int?,
      pageNumber: json['page_number'] as int?,
    );
  }

  /// Convert from local GuidelineChunk for offline fallback display.
  factory RagChunk.fromGuidelineChunk(dynamic chunk, {double score = 0.5}) {
    return RagChunk(
      chunkId: chunk.id as String,
      source: chunk.source as String,
      sourceName: chunk.source as String,
      section: chunk.section as String,
      chunkText: chunk.content as String,
      similarityScore: score,
      regionTag: 'GLOBAL',
      version: '2023',
    );
  }
}

class CitationItem {
  final String source;
  final String section;
  final String chunkId;
  final double similarityScore;

  const CitationItem({
    required this.source,
    required this.section,
    required this.chunkId,
    required this.similarityScore,
  });

  factory CitationItem.fromJson(Map<String, dynamic> json) {
    return CitationItem(
      source: json['source'] as String? ?? '',
      section: json['section'] as String? ?? '',
      chunkId: json['chunk_id'] as String? ?? '',
      similarityScore: (json['similarity_score'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

class ClinicalExplanation {
  final String clinicalSummary;
  final List<Map<String, String>> perDriverExplanations;
  final List<String> recommendedActions;
  final String evidenceSummary;
  final List<CitationItem> citationList;
  final String confidenceDisclaimer;
  final List<RagChunk> ragChunks;
  final String modelVersion;
  final bool generatedOffline;
  final bool fallbackUsed;

  const ClinicalExplanation({
    required this.clinicalSummary,
    required this.perDriverExplanations,
    required this.recommendedActions,
    required this.evidenceSummary,
    required this.citationList,
    required this.confidenceDisclaimer,
    required this.ragChunks,
    required this.modelVersion,
    this.generatedOffline = false,
    this.fallbackUsed = false,
  });

  factory ClinicalExplanation.fromJson(Map<String, dynamic> json) {
    return ClinicalExplanation(
      clinicalSummary: json['clinical_summary'] as String? ?? '',
      perDriverExplanations: (json['per_driver_explanations'] as List<dynamic>? ?? [])
          .map((e) => Map<String, String>.from(e as Map))
          .toList(),
      recommendedActions: (json['recommended_actions'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList(),
      evidenceSummary: json['evidence_summary'] as String? ?? '',
      citationList: (json['citation_list'] as List<dynamic>? ?? [])
          .map((e) => CitationItem.fromJson(e as Map<String, dynamic>))
          .toList(),
      confidenceDisclaimer: json['confidence_disclaimer'] as String? ?? '',
      ragChunks: (json['rag_chunks'] as List<dynamic>? ?? [])
          .map((e) => RagChunk.fromJson(e as Map<String, dynamic>))
          .toList(),
      modelVersion: json['model_version'] as String? ?? 'unknown',
      generatedOffline: json['generated_offline'] as bool? ?? false,
      fallbackUsed: json['fallback_used'] as bool? ?? false,
    );
  }
}
