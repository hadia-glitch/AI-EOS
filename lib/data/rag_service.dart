import '../domain/eoscal_calculator.dart';
import 'guidelines_data.dart';

/// Offline RAG service — keyword scoring with proper deduplication and ranking.
///
/// Scoring model:
///   1. Term frequency: count of query terms present in chunk content/keywords.
///   2. Guideline boost: ×2.5 multiplier if chunk matches the active guideline.
///   3. Layer relevance: +1.5 if the chunk topic matches the patient's
///      highest-scoring EOSCAL layer.
///   4. Deduplication: each chunk ID returned at most once.
///   5. Sorted descending by score; ties broken by source order.
class RagService {
  static List<GuidelineChunk> retrieveRelevantChunks({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    int limit = 5,
  }) {
    // ── Build query term set from patient state ──────────────────────────────
    final terms = _buildQueryTerms(patient, result);

    // ── Score every chunk ────────────────────────────────────────────────────
    final scored = <_Scored>[];
    final seen = <String>{};

    for (final chunk in GuidelinesData.chunks) {
      // Deduplicate
      if (seen.contains(chunk.id)) continue;
      seen.add(chunk.id);

      double score = _scoreChunk(chunk, terms, activeGuideline, result);
      if (score > 0) scored.add(_Scored(chunk, score));
    }

    // Sort descending
    scored.sort((a, b) => b.score.compareTo(a.score));

    // Return top N
    if (scored.isEmpty) {
      // Absolute fallback: return the first chunk for the active guideline,
      // then fill with others — never return random order
      final preferred = GuidelinesData.chunks
          .where((c) => c.source.toUpperCase() == activeGuideline.toUpperCase())
          .take(limit)
          .toList();
      final rest = GuidelinesData.chunks
          .where((c) => c.source.toUpperCase() != activeGuideline.toUpperCase())
          .take(limit - preferred.length)
          .toList();
      return [...preferred, ...rest].take(limit).toList();
    }

    return scored.take(limit).map((s) => s.chunk).toList();
  }

  // ── Query term builder ─────────────────────────────────────────────────────

  static Set<String> _buildQueryTerms(
      PatientParameters patient, EoscalResult result) {
    final terms = <String>{};

    // Layer 1: Maternal
    if (patient.maternalTemperature >= 38.0) {
      terms.addAll(['fever', 'temperature', 'maternal', 'intrapartum']);
    }
    if (patient.romHours >= 18) {
      terms.addAll(['rom', 'rupture', 'membranes', 'prolonged', 'prom']);
    }
    if (patient.gbsPositive) {
      terms.addAll(['gbs', 'streptococcus', 'prophylaxis', 'iap', 'penicillin']);
    }
    if (!patient.adequateIntrapartumAntibiotics && patient.gbsPositive) {
      terms.addAll(['inadequate', 'antibiotics', 'prophylaxis']);
    }
    if (patient.clinicalChorioamnionitis) {
      terms.addAll(['chorioamnionitis', 'infection', 'fever']);
    }

    // Layer 2: Clinical
    if (patient.respiratoryDistress != 'None') {
      terms.addAll(['respiratory', 'distress', 'breathing', 'tachypnea',
          'grunting', 'retracting', 'cpap']);
    }
    if (patient.oxygenNeed != 'None') {
      terms.addAll(['oxygen', 'ventilation', 'supportive', 'cpap']);
    }
    if (patient.apgar5Min <= 6) {
      terms.addAll(['apgar', 'resuscitation', 'depression']);
    }
    if (patient.poorPerfusion) {
      terms.addAll(['perfusion', 'shock', 'capillary', 'refill', 'hypotension']);
    }
    if (patient.neonatalTemperature < 36.0) {
      terms.addAll(['hypothermia', 'temperature', 'thermal']);
    } else if (patient.neonatalTemperature > 38.0) {
      terms.addAll(['hyperthermia', 'temperature', 'fever']);
    }
    if (patient.neurologicalStatus != 'Normal') {
      terms.addAll(['lethargy', 'irritability', 'seizures', 'neurological']);
    }
    if (patient.gestationalAgeWeeks < 37.0) {
      terms.addAll(['preterm', 'premature', 'gestational']);
    }

    // Layer 3: Lab
    if (patient.wbcCount != null) {
      terms.addAll(['wbc', 'cbc', 'neutrophil', 'leukocyte']);
    }
    if (patient.itRatio != null && patient.itRatio! >= 0.2) {
      terms.addAll(['it ratio', 'immature', 'neutrophil', 'ratio']);
    }
    if (patient.crpLevel != null && patient.crpLevel! >= 10) {
      terms.addAll(['crp', 'reactive protein', 'inflammatory', 'markers']);
    }
    if (patient.pctLevel != null) {
      terms.addAll(['pct', 'procalcitonin', 'markers']);
    }
    if (patient.bloodCulturePositive == true) {
      terms.addAll(['culture', 'blood culture', 'bacteremia', 'positive']);
    }
    if (patient.plateletCount != null && patient.plateletCount! < 150000) {
      terms.addAll(['platelet', 'thrombocytopenia']);
    }

    // Always include treatment terms if any risk
    final anyRisk = result.totalScore >= 2;
    if (anyRisk) {
      terms.addAll(['antibiotics', 'treatment', 'empiric', 'gentamicin', 'management']);
    }

    // Monitoring always relevant
    terms.addAll(['monitoring', 'observation', 'crp', 'culture']);

    return terms;
  }

  // ── Chunk scorer ──────────────────────────────────────────────────────────

  static double _scoreChunk(
    GuidelineChunk chunk,
    Set<String> queryTerms,
    String activeGuideline,
    EoscalResult result,
  ) {
    double score = 0.0;
    final contentLower = chunk.content.toLowerCase();
    final keywordsLower = chunk.keywords.map((k) => k.toLowerCase()).toSet();

    // Term frequency scoring
    for (final term in queryTerms) {
      final termLower = term.toLowerCase();
      // Keyword exact match — higher weight
      if (keywordsLower.contains(termLower)) {
        score += 2.0;
      } else if (contentLower.contains(termLower)) {
        // Count occurrences but cap at 3 to avoid keyword stuffing bias
        final count = _countOccurrences(contentLower, termLower).clamp(0, 3);
        score += count * 0.8;
      }
    }

    // Guideline boost
    if (chunk.source.toUpperCase() == activeGuideline.toUpperCase()) {
      score *= 2.5;
    } else {
      // Still useful — comparative evidence, but ranked lower
      score *= 0.7;
    }

    // Layer relevance boost
    if (result.layer1Score >= result.layer2Score &&
        result.layer1Score >= result.layer3Score) {
      // Maternal risk dominant
      if (_chunkIsAbout(chunk, ['maternal', 'gbs', 'rom', 'fever', 'chorioamnionitis'])) {
        score += 1.5;
      }
    } else if (result.layer2Score >= result.layer3Score) {
      // Clinical signs dominant
      if (_chunkIsAbout(chunk, ['respiratory', 'clinical', 'neonatal', 'signs', 'cpap'])) {
        score += 1.5;
      }
    } else {
      // Lab dominant
      if (_chunkIsAbout(chunk, ['crp', 'wbc', 'culture', 'laboratory', 'markers'])) {
        score += 1.5;
      }
    }

    return score;
  }

  static int _countOccurrences(String text, String term) {
    int count = 0;
    int idx = 0;
    while ((idx = text.indexOf(term, idx)) != -1) {
      count++;
      idx += term.length;
    }
    return count;
  }

  static bool _chunkIsAbout(GuidelineChunk chunk, List<String> topics) {
    final contentLower = chunk.content.toLowerCase();
    final sectionLower = chunk.section.toLowerCase();
    return topics.any((t) =>
        contentLower.contains(t) ||
        sectionLower.contains(t) ||
        chunk.keywords.any((k) => k.toLowerCase().contains(t)));
  }
}

class _Scored {
  final GuidelineChunk chunk;
  final double score;
  const _Scored(this.chunk, this.score);
}