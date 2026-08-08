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
  /// PDF file name (matches guideline_documents.name / evidence_chunks
  /// metadata.file_name on the backend) — used to look up/download the
  /// cached local PDF for offline "jump to source" viewing.
  final String fileName;

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
    this.fileName = '',
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
      fileName: json['file_name'] as String? ?? '',
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

/// A retrieved evidence chunk addressable by a short inline label (e.g.
/// "E1") instead of its raw UUID, with enough of its content to show in a
/// hover/tap citation popup. Every `[E#]` token that can appear in
/// AI-generated prose (driver breakdown, fact-check flagged claims,
/// resolution notes) resolves to exactly one of these — see
/// ClinicalCarePlan.evidenceLabels / StructuredExplanation.evidenceLabels
/// and core/widgets/evidence_citation.dart, which renders them.
class EvidenceLabelRef {
  final String label;
  final String chunkId;
  final String source;
  final String sourceName;
  final String section;
  final String snippet;
  final int? pageNumber;
  final String fileName;

  const EvidenceLabelRef({
    required this.label,
    required this.chunkId,
    required this.source,
    required this.sourceName,
    required this.section,
    this.snippet = '',
    this.pageNumber,
    this.fileName = '',
  });

  factory EvidenceLabelRef.fromJson(Map<String, dynamic> json) {
    return EvidenceLabelRef(
      label: json['label'] as String? ?? '',
      chunkId: json['chunk_id'] as String? ?? '',
      source: json['source'] as String? ?? '',
      sourceName: json['source_name'] as String? ?? '',
      section: json['section'] as String? ?? '',
      snippet: json['snippet'] as String? ?? '',
      pageNumber: json['page_number'] as int?,
      fileName: json['file_name'] as String? ?? '',
    );
  }

  /// True when there's enough information to jump straight to the source
  /// PDF page from the citation popup (same convention as CitationItem).
  bool get canOpenInApp => fileName.isNotEmpty;
}

class CitationItem {
  final String source;
  final String section;
  final String chunkId;
  final double similarityScore;
  /// Public URL for the originating guideline document (may be empty for local chunks).
  final String documentUrl;
  /// 1-indexed PDF page this citation's source chunk was extracted from, if
  /// known. Together with [fileName], lets a citation be opened directly in
  /// GuidelinePdfViewerScreen instead of only linking out to an external URL.
  final int? pageNumber;
  /// PDF file name (matches guideline_documents.name / evidence_chunks
  /// metadata.file_name) — the cache key GuidelinePdfViewerScreen/
  /// OfflinePdfCache use to locate this document. Empty when unknown (e.g.
  /// a citation resolved only from a document title with no backing chunk).
  final String fileName;

  const CitationItem({
    required this.source,
    required this.section,
    required this.chunkId,
    required this.similarityScore,
    this.documentUrl = '',
    this.pageNumber,
    this.fileName = '',
  });

  factory CitationItem.fromJson(Map<String, dynamic> json) {
    return CitationItem(
      source: json['source'] as String? ?? '',
      section: json['section'] as String? ?? '',
      chunkId: json['chunk_id'] as String? ?? '',
      similarityScore: (json['similarity_score'] as num?)?.toDouble() ?? 0.0,
      documentUrl: json['document_url'] as String? ?? '',
      pageNumber: json['page_number'] as int?,
      fileName: json['file_name'] as String? ?? '',
    );
  }

  /// True when there's enough information to open this citation directly
  /// in the in-app PDF viewer rather than only an external browser link.
  bool get canOpenInApp => fileName.isNotEmpty;

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

/// One member chunk within an EvidenceSection — see EvidenceSection's
/// docstring for why a section is assembled from ALL of a source heading's
/// chunks, not just the ones a given search matched.
class EvidenceSectionChunk {
  final String chunkId;
  final String chunkText;
  final int? pageNumber;
  /// True when this specific chunk was part of what the search actually
  /// matched — the UI highlights these within the full section text so a
  /// clinician can see exactly which sentences answered their query,
  /// distinct from the surrounding section context pulled in for legibility.
  final bool highlighted;

  const EvidenceSectionChunk({
    required this.chunkId,
    required this.chunkText,
    this.pageNumber,
    this.highlighted = false,
  });

  factory EvidenceSectionChunk.fromJson(Map<String, dynamic> json) {
    return EvidenceSectionChunk(
      chunkId: json['chunk_id'] as String? ?? '',
      chunkText: json['chunk_text'] as String? ?? '',
      pageNumber: json['page_number'] as int?,
      highlighted: json['highlighted'] as bool? ?? false,
    );
  }
}

/// A COMPLETE source section (everything under one document heading, e.g.
/// "3. Recommendations and evidence"), assembled server-side from every
/// chunk filed under that heading — not just the individual fragments a
/// particular search query happened to retrieve. Replaces per-chunk
/// evidence cards: instead of several separately-scored, sometimes
/// illegible-in-isolation fragments, the clinician sees the real section as
/// it reads in the source guideline, with the chunks that actually matched
/// their query marked via [EvidenceSectionChunk.highlighted].
class EvidenceSection {
  final String source;
  final String sourceName;
  final String section;
  final String fileName;
  final int? pageNumber;
  final String regionTag;
  final String version;
  final double similarityScore;
  final List<EvidenceSectionChunk> chunks;
  final String fullText;
  /// True if the section had more member chunks than the server returns —
  /// the UI should say so rather than silently showing a partial section.
  final bool truncated;
  /// Concise AI-generated summary of the section. Empty when no LLM
  /// provider was available — the UI shows [fullText] directly in that
  /// case rather than a truncated or regurgitated fallback; never both.
  final String aiSummary;

  const EvidenceSection({
    required this.source,
    required this.sourceName,
    required this.section,
    this.fileName = '',
    this.pageNumber,
    this.regionTag = '',
    this.version = '',
    this.similarityScore = 0.0,
    required this.chunks,
    required this.fullText,
    this.truncated = false,
    this.aiSummary = '',
  });

  factory EvidenceSection.fromJson(Map<String, dynamic> json) {
    final rawChunks = json['chunks'] as List<dynamic>? ?? [];
    return EvidenceSection(
      source: json['source'] as String? ?? '',
      sourceName: json['source_name'] as String? ?? '',
      section: json['section'] as String? ?? '',
      fileName: json['file_name'] as String? ?? '',
      pageNumber: json['page_number'] as int?,
      regionTag: json['region_tag'] as String? ?? '',
      version: json['version'] as String? ?? '',
      similarityScore: (json['similarity_score'] as num?)?.toDouble() ?? 0.0,
      chunks: rawChunks
          .map((c) => EvidenceSectionChunk.fromJson(c as Map<String, dynamic>))
          .toList(),
      fullText: json['full_text'] as String? ?? '',
      truncated: json['truncated'] as bool? ?? false,
      aiSummary: json['ai_summary'] as String? ?? '',
    );
  }

  bool get canOpenInApp => fileName.isNotEmpty;
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