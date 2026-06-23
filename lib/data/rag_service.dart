import '../domain/eoscal_calculator.dart';
import 'guidelines_data.dart';

class RagService {
  /// Searches for relevant guideline chunks based on patient parameters and active guideline.
  static List<GuidelineChunk> retrieveRelevantChunks({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline, // "NICE", "AAP", or "WHO"
    int limit = 4,
  }) {
    // Generate a set of query keywords based on patient parameters and findings
    final Set<String> queryKeywords = {};

    // Baseline/Demographics
    if (patient.gestationalAgeWeeks < 37.0) {
      queryKeywords.addAll(['preterm', 'premature', 'gestational']);
    }

    // Layer 1
    if (patient.maternalTemperature >= 38.0) {
      queryKeywords.addAll(['fever', 'temperature', 'maternal']);
    }
    if (patient.romHours >= 18) {
      queryKeywords.addAll(['rom', 'rupture', 'membranes', 'prolonged']);
    }
    if (patient.gbsPositive) {
      queryKeywords.addAll(['gbs', 'streptococcus', 'antibiotics', 'prophylaxis', 'iap']);
    }
    if (patient.clinicalChorioamnionitis) {
      queryKeywords.addAll(['chorioamnionitis', 'fever', 'infection']);
    }

    // Layer 2
    if (patient.respiratoryDistress != 'None') {
      queryKeywords.addAll(['respiratory', 'distress', 'breathing', 'tachypnea', 'grunting', 'retracting']);
    }
    if (patient.oxygenNeed != 'None') {
      queryKeywords.addAll(['oxygen', 'cpap', 'ventilation', 'supportive']);
    }
    if (patient.apgar5Min <= 6) {
      queryKeywords.addAll(['apgar', 'neonatal']);
    }
    if (patient.poorPerfusion) {
      queryKeywords.addAll(['perfusion', 'shock', 'blood pressure', 'refill']);
    }
    if (patient.neonatalTemperature < 36.5 || patient.neonatalTemperature > 37.5) {
      queryKeywords.addAll(['temperature', 'hypothermia', 'hyperthermia', 'thermal']);
    }
    if (patient.neurologicalStatus != 'Normal') {
      queryKeywords.addAll(['lethargy', 'irritability', 'seizures', 'neurological', 'convulsions']);
    }

    // Layer 3
    if (patient.wbcCount != null) {
      queryKeywords.addAll(['wbc', 'cbc', 'leukocyte', 'neutrophil']);
    }
    if (patient.itRatio != null) {
      queryKeywords.addAll(['it ratio', 'neutrophil', 'ratio']);
    }
    if (patient.plateletCount != null) {
      queryKeywords.addAll(['platelet', 'thrombocytopenia']);
    }
    if (patient.crpLevel != null) {
      queryKeywords.addAll(['crp', 'reactive protein', 'markers']);
    }
    if (patient.pctLevel != null) {
      queryKeywords.addAll(['pct', 'procalcitonin', 'markers']);
    }
    if (patient.bloodCulturePositive == true) {
      queryKeywords.addAll(['culture', 'blood culture', 'bacteremia', 'sepsis']);
    }

    // If total score is high, ensure treatment terms are included
    if (result.totalScore >= 4) {
      queryKeywords.addAll(['antibiotics', 'treatment', 'empiric', 'gentamicin', 'ampicillin', 'penicillin']);
    }

    // Score and rank each chunk
    final List<MapEntry<GuidelineChunk, double>> scoredChunks = [];

    for (var chunk in GuidelinesData.chunks) {
      double score = 0.0;

      // 1. Keyword Overlap (TF-IDF light approximation)
      for (var queryTerm in queryKeywords) {
        for (var chunkKeyword in chunk.keywords) {
          if (chunkKeyword.toLowerCase() == queryTerm.toLowerCase() ||
              chunk.content.toLowerCase().contains(queryTerm.toLowerCase())) {
            score += 1.0;
          }
        }
      }

      // 2. Active Guideline Boost (Highly prioritize the active guideline chosen by clinician)
      if (chunk.source.toUpperCase() == activeGuideline.toUpperCase()) {
        score *= 2.5; // Multiply match score if it corresponds to current active guideline
        score += 2.0; // Give a baseline boost for matching the source
      } else {
        // Still allow matching other guidelines for comparative medical evidence, but rank lower
        score *= 0.8;
      }

      if (score > 0.0) {
        scoredChunks.add(MapEntry(chunk, score));
      }
    }

    // Sort by descending score
    scoredChunks.sort((a, b) => b.value.compareTo(a.value));

    // Return the top N chunks
    return scoredChunks.map((entry) => entry.key).take(limit).toList();
  }
}
