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
  /// Public URL for the originating guideline document (may be empty for local chunks).
  final String documentUrl;

  const CitationItem({
    required this.source,
    required this.section,
    required this.chunkId,
    required this.similarityScore,
    this.documentUrl = '',
  });

  factory CitationItem.fromJson(Map<String, dynamic> json) {
    return CitationItem(
      source: json['source'] as String? ?? '',
      section: json['section'] as String? ?? '',
      chunkId: json['chunk_id'] as String? ?? '',
      similarityScore: (json['similarity_score'] as num?)?.toDouble() ?? 0.0,
      documentUrl: json['document_url'] as String? ?? '',
    );
  }

  /// Map well-known source names to their public guideline URLs.
  static String resolveDocumentUrl(String sourceName) {
    final s = sourceName.toUpperCase();
    if (s.contains('NICE') || s.contains('NG195')) {
      return 'https://www.nice.org.uk/guidance/ng195';
    }
    if (s.contains('AAP') || s.contains('PUOPOLO') || s.contains('AMERICAN ACADEMY')) {
      return 'https://publications.aap.org/pediatrics/article/150/6/e2022057091/190641';
    }
    if (s.contains('WHO')) {
      return 'https://www.who.int/publications/i/item/9789240058521';
    }
    if (s.contains('KUZNIEWICZ') || s.contains('EOSCAL') && s.contains('2024')) {
      return 'https://doi.org/10.1542/peds.2023-065267';
    }
    if (s.contains('2011') || s.contains('ORIGINAL EOSCAL')) {
      return 'https://doi.org/10.1542/peds.2011-1572';
    }
    if (s.contains('2019') || s.contains('EOSCAL UPDATE')) {
      return 'https://doi.org/10.1542/peds.2018-3090';
    }
    if (s.contains('QATAR') || s.contains('VELLAMGOT')) {
      return 'https://bmjopen.bmj.com/content/13/9/e073216';
    }
    return '';
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
    // citation_list from backend may or may not include document_url;
    // resolve it from source name if missing.
    final rawCitations = json['citation_list'] as List<dynamic>? ?? [];
    final citations = rawCitations.map((e) {
      final map = e as Map<String, dynamic>;
      final item = CitationItem.fromJson(map);
      if (item.documentUrl.isEmpty) {
        return CitationItem(
          source: item.source,
          section: item.section,
          chunkId: item.chunkId,
          similarityScore: item.similarityScore,
          documentUrl: CitationItem.resolveDocumentUrl(item.source),
        );
      }
      return item;
    }).toList();

    // Build citations from rag_chunks when citation_list is empty
    final rawChunks = (json['rag_chunks'] as List<dynamic>? ?? [])
        .map((e) => RagChunk.fromJson(e as Map<String, dynamic>))
        .toList();

    final effectiveCitations = citations.isNotEmpty
        ? citations
        : rawChunks
            .map((c) => CitationItem(
                  source: c.sourceName,
                  section: c.section,
                  chunkId: c.chunkId,
                  similarityScore: c.similarityScore,
                  documentUrl: CitationItem.resolveDocumentUrl(c.sourceName),
                ))
            .toList();

    return ClinicalExplanation(
      clinicalSummary: json['clinical_summary'] as String? ?? '',
      perDriverExplanations: (json['per_driver_explanations'] as List<dynamic>? ?? [])
          .map((e) => Map<String, String>.from(e as Map))
          .toList(),
      recommendedActions: (json['recommended_actions'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList(),
      evidenceSummary: json['evidence_summary'] as String? ?? '',
      citationList: effectiveCitations,
      confidenceDisclaimer: json['confidence_disclaimer'] as String? ?? '',
      ragChunks: rawChunks,
      modelVersion: json['model_version'] as String? ?? 'unknown',
      generatedOffline: json['generated_offline'] as bool? ?? false,
      fallbackUsed: json['fallback_used'] as bool? ?? false,
    );
  }
}