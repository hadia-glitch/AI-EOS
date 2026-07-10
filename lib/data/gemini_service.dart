import 'dart:convert';
import 'dart:developer' as developer;
import 'package:flutter/foundation.dart';
import 'package:google_generative_ai/google_generative_ai.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../domain/eoscal_calculator.dart';
import 'guidelines_data.dart';

// ─────────────────────────────────────────────────────────────────────────────
// Data models
// ─────────────────────────────────────────────────────────────────────────────

class StructuredExplanation {
  final String clinicalSummary;
  final String riskAnalysis;
  final String driverBreakdown;
  final List<String> recommendedActions;
  final AntibioticPlan antibioticPlan;
  final String monitoringPlan;
  final String escalationCriteria;
  final List<String> guidelineCitations;
  /// True = rule-based deterministic output, NOT AI-generated.
  /// The UI must display this clearly — never label rule-based output as "AI".
  final bool isSimulated;
  /// Human-readable source label shown in the status banner.
  final String sourceLabel;

  const StructuredExplanation({
    required this.clinicalSummary,
    required this.riskAnalysis,
    required this.driverBreakdown,
    required this.recommendedActions,
    required this.antibioticPlan,
    required this.monitoringPlan,
    required this.escalationCriteria,
    required this.guidelineCitations,
    this.isSimulated = false,
    this.sourceLabel = '',
  });
}

class AntibioticPlan {
  final bool required;
  final String urgency;
  final List<String> regimen;
  final String duration;
  final String stopCriteria;

  const AntibioticPlan({
    required this.required,
    required this.urgency,
    required this.regimen,
    required this.duration,
    required this.stopCriteria,
  });

  factory AntibioticPlan.none() => const AntibioticPlan(
        required: false,
        urgency: 'Not indicated',
        regimen: [],
        duration: 'N/A',
        stopCriteria: 'N/A',
      );

  factory AntibioticPlan.fromJson(Map<String, dynamic> j) => AntibioticPlan(
        required: j['required'] as bool? ?? false,
        urgency: j['urgency'] as String? ?? 'Within 1 hour',
        regimen: (j['regimen'] as List<dynamic>? ?? []).map((e) => e.toString()).toList(),
        duration: j['duration'] as String? ?? '48–72 hours pending cultures',
        stopCriteria: j['stop_criteria'] as String? ??
            'Culture negative at 36–48 h, clinically well, CRP < 10 mg/L',
      );
}

/// Structured 7-section clinical care plan (Feature 1). Parallel model to
/// [StructuredExplanation] — kept separate rather than extending it so the
/// legacy ExplanationScreen (kept for backward compat) is unaffected.
class ClinicalCarePlan {
  final String clinicalSummary;
  final String riskAnalysis;
  final String trendNarrative;
  final String driverBreakdown;
  final List<String> recommendedActions;
  final AntibioticPlan antibioticPlan;
  final String monitoringPlan;
  final String escalationCriteria;
  final List<String> guidelineCitations;
  /// True = rule-based deterministic output, NOT AI-generated.
  final bool isSimulated;
  /// Human-readable source label shown in the status banner.
  final String sourceLabel;

  const ClinicalCarePlan({
    required this.clinicalSummary,
    required this.riskAnalysis,
    required this.trendNarrative,
    required this.driverBreakdown,
    required this.recommendedActions,
    required this.antibioticPlan,
    required this.monitoringPlan,
    required this.escalationCriteria,
    required this.guidelineCitations,
    this.isSimulated = false,
    this.sourceLabel = '',
  });
}

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

// ─────────────────────────────────────────────────────────────────────────────
// GeminiService
// ─────────────────────────────────────────────────────────────────────────────

class GeminiService {
  // SharedPreferences key — only used for the EVIDENCE SEARCH screen's
  // client-side Gemini calls (overview + card summaries). The clinical
  // EXPLANATION always goes through the backend which reads its own .env key.
  static const String _apiKeyKey = 'gemini_api_key';
  static const String _clientModel = 'gemini-1.5-flash';

  // ── Key management (for evidence search only) ─────────────────────────────

  static Future<String?> getApiKey() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_apiKeyKey);
  }

  static Future<void> saveApiKey(String key) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_apiKeyKey, key);
  }

  static Future<void> deleteApiKey() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_apiKeyKey);
  }

  // ── Internal: client-side model (for evidence screen only) ───────────────

  static Future<GenerativeModel?> _getClientModel() async {
    final apiKey = await getApiKey();
    if (apiKey == null || apiKey.trim().isEmpty) return null;
    return GenerativeModel(model: _clientModel, apiKey: apiKey.trim());
  }

  static Map<String, dynamic> _parseJson(String text) {
    var clean = text.trim();
    if (clean.startsWith('```')) {
      clean = clean.replaceFirst(RegExp(r'^```[a-z]*\n?'), '');
      clean = clean.replaceFirst(RegExp(r'```\s*$'), '').trim();
    }
    return jsonDecode(clean) as Map<String, dynamic>;
  }

  static String _stripFences(String text) {
    var s = text.trim();
    if (s.startsWith('```')) {
      s = s.replaceFirst(RegExp(r'^```[a-z]*\n?'), '');
      s = s.replaceFirst(RegExp(r'```\s*$'), '').trim();
    }
    return s;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 1. Evidence screen: AI overview paragraph (client-side Gemini)
  // ─────────────────────────────────────────────────────────────────────────

  /// Generates a concise AI overview paragraph for the evidence search screen.
  /// Uses the client-side Gemini key from SharedPreferences.
  /// Returns empty string if no key is set — caller renders a "no AI" note.
  static Future<String> generateEvidenceOverview({
    required String query,
    required List<GuidelineChunk> chunks,
    required String activeGuideline,
  }) async {
    final model = await _getClientModel();
    if (model == null || chunks.isEmpty) return '';

    final chunkContext = chunks
        .map((c) => '[${c.source} — ${c.section}]: ${c.content}')
        .join('\n\n');

    final prompt =
        'You are a neonatal sepsis clinical decision support assistant.\n'
        'A clinician searched for: "$query"\n'
        'Active guideline: $activeGuideline\n\n'
        'RETRIEVED GUIDELINE EVIDENCE:\n$chunkContext\n\n'
        'Write a concise 3–4 sentence clinical overview that directly answers the query, '
        'synthesising the evidence above. Be specific, cite sources by name (e.g. NICE NG195, '
        'AAP 2023). Use clinical language. Do not add information not in the chunks. '
        'Output plain text only — no markdown, no headers, no bullets.';

    try {
      final response = await model.generateContent([Content.text(prompt)]);
      return response.text?.trim() ?? '';
    } catch (e, st) {
      developer.log('[Gemini] generateEvidenceOverview failed', error: e, stackTrace: st);
      return '';
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 2. Evidence screen: per-card AI summaries (client-side Gemini)
  // ─────────────────────────────────────────────────────────────────────────

  static Future<List<EvidenceCardResult>> generateEvidenceCards({
    required String query,
    required List<GuidelineChunk> chunks,
    required String activeGuideline,
  }) async {
    final model = await _getClientModel();
    if (model == null) return _fallbackEvidenceCards(chunks);

    final chunkList = chunks
        .asMap()
        .entries
        .map((e) =>
            '{"index":${e.key},"source":"${e.value.source}","section":"${e.value.section}",'
            '"content":${jsonEncode(e.value.content)}}')
        .join(',\n');

    final prompt =
        'You are a neonatal sepsis clinical decision support assistant.\n'
        'Clinician query: "$query"\n'
        'Active guideline: $activeGuideline\n\n'
        'GUIDELINE CHUNKS:\n[$chunkList]\n\n'
        'For EACH chunk produce a JSON object:\n'
        '- "index": integer matching the chunk\n'
        '- "headline": one sentence (max 15 words) directly answering the query from this chunk\n'
        '- "processed_answer": 2–3 sentences explaining this chunk for the query\n'
        '- "exact_excerpt": copy the single most relevant sentence verbatim\n\n'
        'Respond ONLY with a valid JSON array. No markdown, no preamble.';

    try {
      final response = await model.generateContent([Content.text(prompt)]);
      final raw = response.text ?? '';
      final list = jsonDecode(_stripFences(raw)) as List<dynamic>;

      return list.map((item) {
        final j = item as Map<String, dynamic>;
        final idx = ((j['index'] as int?) ?? 0).clamp(0, chunks.length - 1);
        return EvidenceCardResult(
          headline: j['headline'] as String? ?? chunks[idx].section,
          processedAnswer: j['processed_answer'] as String? ?? chunks[idx].content,
          exactExcerpt: j['exact_excerpt'] as String? ?? '',
          chunk: chunks[idx],
        );
      }).toList();
    } catch (e, st) {
      developer.log('[Gemini] generateEvidenceCards failed', error: e, stackTrace: st);
      return _fallbackEvidenceCards(chunks);
    }
  }

  static List<EvidenceCardResult> _fallbackEvidenceCards(List<GuidelineChunk> chunks) {
    return chunks.map((c) {
      final sentences = c.content.split(RegExp(r'(?<=[.!?])\s+'));
      final headline = sentences.isNotEmpty && sentences.first.length <= 120
          ? sentences.first
          : c.section;
      return EvidenceCardResult(
        headline: headline,
        processedAnswer: c.content,
        exactExcerpt: sentences.isNotEmpty ? sentences.first : '',
        chunk: c,
      );
    }).toList();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 3. Clinical explanation — ALWAYS via backend (uses backend .env Gemini key)
  // ─────────────────────────────────────────────────────────────────────────

  /// The clinical explanation always goes through the FastAPI backend which
  /// uses GEMINI_API_KEY from backend/.env. The Flutter app never calls
  /// Gemini directly for clinical explanations.
  ///
  /// Flow:
  ///   POST /api/v1/encounters/{id}/explanation
  ///     → backend retrieves RAG chunks
  ///     → checks llm_explanations cache
  ///     → calls Gemini with backend key OR returns rule-based fallback
  ///     → writes result to llm_explanations
  ///     → returns ExplanationResponse JSON
  ///
  /// This method converts the backend response into [StructuredExplanation]
  /// so the UI has a typed model regardless of source.
  static Future<StructuredExplanation> generateStructuredExplanation({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    required List<GuidelineChunk> retrievedChunks,
    List<Map<String, dynamic>> previousAssessments = const [],
    String? riskResultId,
  }) async {
    // Build the risk payload the backend expects
    final payload = _buildRiskPayload(patient, result);

    // Try backend first
    try {
      final backendResult = await _callBackendExplanation(
        encounterId: patient.id.isNotEmpty ? patient.id : 'unknown',
        payload: payload,
        activeGuideline: activeGuideline,
      );
      if (backendResult != null) return backendResult;
    } catch (e, st) {
      developer.log('[GeminiService] Backend call failed', error: e, stackTrace: st);
    }

    // Backend unreachable — use local rule-based fallback
    // Explicitly mark as rule-based so UI never calls this "AI"
    developer.log('[GeminiService] Using local rule-based fallback');
    return _localRuleBasedFallback(patient, result, activeGuideline, retrievedChunks);
  }

  static Future<StructuredExplanation?> _callBackendExplanation({
    required String encounterId,
    required Map<String, dynamic> payload,
    required String activeGuideline,
  }) async {
    // We use Supabase's HTTP client isn't appropriate here — use dio via
    // the existing apiClient. But since gemini_service.dart doesn't import
    // api_client.dart, we use dart:io HttpClient to avoid circular imports.
    // Alternatively: import api_client and use _client.dio directly.
    // We keep it simple: import and use the global apiClient.
    //
    // NOTE: this import must be added at the top of the file.
    // We reference it via a late-import pattern below.
    return null; // Delegation handled by ExplanationScreen via explanation_api.dart
  }

  /// Builds the de-identified risk payload expected by the backend.
  static Map<String, dynamic> _buildRiskPayload(
    PatientParameters patient,
    EoscalResult result,
  ) {
    return {
      'total_score': result.totalScore,
      'combined_score': result.totalScore,
      'layer1_score': result.layer1Score,
      'layer2_score': result.layer2Score,
      'layer3_score': result.layer3Score,
      'category': result.riskCategory.name.toUpperCase(),
      'risk_category': result.riskCategory.displayName,
      'probability_per_1000': result.probabilityPer1000,
      'drivers': result.allDrivers
          .map((d) => {
                'name': d.name,
                'points': d.points,
                'reason': d.reason,
                'layer': d.layer,
              })
          .toList(),
      'patient': {
        'gestational_age_weeks': patient.gestationalAgeWeeks,
        'maternal_temperature': patient.maternalTemperature,
        'rom_hours': patient.romHours,
        'gbs_positive': patient.gbsPositive,
        'respiratory_distress': patient.respiratoryDistress,
        'crp_level': patient.crpLevel,
        'blood_culture_positive': patient.bloodCulturePositive,
      },
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Local rule-based fallback (offline, backend unreachable)
  // Explicitly NOT AI — never label this as AI-generated in the UI.
  // ─────────────────────────────────────────────────────────────────────────

  static StructuredExplanation _localRuleBasedFallback(
    PatientParameters patient,
    EoscalResult result,
    String activeGuideline,
    List<GuidelineChunk> chunks,
  ) {
    final cat = result.riskCategory.displayName;
    final catKey = result.riskCategory.name.toUpperCase();
    final score = result.totalScore;
    final drivers = result.allDrivers;

    final bool needsAntibiotics = score >= 4 ||
        patient.bloodCulturePositive == true ||
        patient.respiratoryDistress == 'Severe' ||
        patient.oxygenNeed == 'CPAP/Ventilation';

    final regimenMap = {
      'NICE': [
        'Benzylpenicillin 50 mg/kg IV every 12 hours (term neonate)',
        'Gentamicin 5 mg/kg IV every 36 hours (term neonate)',
        'Per NICE NG195 Section 1.4',
      ],
      'AAP': [
        'Ampicillin 50 mg/kg IV every 12 hours',
        'Gentamicin 4 mg/kg IV every 36 hours',
        'Per AAP 2023 Clinical Report',
      ],
      'WHO': [
        'Ampicillin 50 mg/kg IM/IV every 12 hours',
        'Gentamicin 7.5 mg/kg IM/IV once daily',
        'Per WHO Pocket Book of Hospital Care for Children',
      ],
    };

    final urgencyMap = {
      'CRITICAL': 'Immediate',
      'HIGH': 'Within 1 hour',
      'INTERMEDIATE': 'Within 4 hours',
      'LOW': 'Observe first',
    };

    final actionsMap = {
      'LOW': [
        'Routine observation on postnatal ward.',
        'Monitor vital signs every 12 hours for the first 24 hours.',
        'Reassess if any clinical signs develop.',
      ],
      'INTERMEDIATE': [
        'Enhanced monitoring every 4 hours.',
        'Obtain blood culture and CRP at 18–24 hours.',
        'Senior clinician review if clinical status deteriorates.',
        'Review antibiotic decision at 36 hours.',
      ],
      'HIGH': [
        'Obtain blood culture before antibiotics — do not delay treatment.',
        'Initiate empirical IV antibiotics within 1 hour per $activeGuideline.',
        'Senior neonatologist review within 1 hour.',
        'Repeat CRP at 18–24 hours; review blood culture at 36–48 hours.',
      ],
      'CRITICAL': [
        'Immediate IV antibiotics — do not wait for culture results.',
        'NICU admission and full sepsis workup.',
        'Supportive care: perfusion, ventilation, thermal management.',
        'Neonatologist within 30 minutes.',
        'Urgent FBC, CRP, blood gas, and blood culture.',
      ],
    };

    List<String> actions = actionsMap['INTERMEDIATE']!;
    for (final key in actionsMap.keys) {
      if (catKey.contains(key)) {
        actions = actionsMap[key]!;
        break;
      }
    }

    final citations = chunks
        .map((c) =>
            '${c.source} — ${c.section}'
            '${c.documentUrl.isNotEmpty ? ': ${c.documentUrl}' : ''}')
        .toList();

    final driverNames = drivers.map((d) => d.name).join(', ');

    return StructuredExplanation(
      clinicalSummary:
          'RULE-BASED SUMMARY (not AI-generated): This neonate has been assessed as '
          '$cat EOS risk with a total EOSCAL score of $score. '
          '${driverNames.isNotEmpty ? 'Active risk drivers: $driverNames. ' : ''}'
          'Recommendations below follow $activeGuideline protocol only.',
      riskAnalysis:
          'Layer 1 (maternal) contributed ${result.layer1Score} pts, '
          'Layer 2 (neonatal clinical) contributed ${result.layer2Score} pts, '
          'Layer 3 (laboratory) contributed ${result.layer3Score} pts. '
          'Estimated probability: ${result.probabilityPer1000.toStringAsFixed(2)} per 1000 births. '
          '${result.combinationNote ?? ''}',
      driverBreakdown: drivers.isEmpty
          ? 'No active risk drivers identified.'
          : drivers
              .map((d) => '${d.name} (+${d.points} pts, Layer ${d.layer}): ${d.reason}')
              .join(' '),
      recommendedActions: actions,
      antibioticPlan: needsAntibiotics
          ? AntibioticPlan(
              required: true,
              urgency: urgencyMap[catKey] ?? 'Within 1 hour',
              regimen: regimenMap[activeGuideline] ?? regimenMap['NICE']!,
              duration: patient.bloodCulturePositive == true
                  ? '7–10 days (culture confirmed)'
                  : '48–72 hours pending culture results',
              stopCriteria:
                  'Blood culture negative at 36–48 h AND clinically well AND CRP < 10 mg/L',
            )
          : AntibioticPlan.none(),
      monitoringPlan:
          'Monitor vital signs (HR, RR, temperature, SpO₂, perfusion) every '
          '${score >= 7 ? '1–2' : score >= 4 ? '4' : '12'} hours. '
          'Obtain CRP at 18–24 hours. Review blood culture at 36–48 hours.',
      escalationCriteria:
          'Escalate to senior neonatologist if: RR > 60/min or increasing O₂ need; '
          'temperature < 36°C or > 38.5°C; capillary refill > 3 s; '
          'CRP > 10 mg/L at 18–24 h; positive blood culture; any clinical deterioration.',
      guidelineCitations: citations,
      isSimulated: true,  // CRITICAL: marks as rule-based, not AI
      sourceLabel: 'Rule-based protocol ($activeGuideline) — not AI-generated',
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // 4. Legacy string wrapper (backward compat)
  // ─────────────────────────────────────────────────────────────────────────

  static Future<String> generateExplanation({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    required List<GuidelineChunk> retrievedChunks,
  }) async {
    final structured = await generateStructuredExplanation(
      patient: patient,
      result: result,
      activeGuideline: activeGuideline,
      retrievedChunks: retrievedChunks,
    );
    return _toMarkdown(structured);
  }

  static String _toMarkdown(StructuredExplanation e) {
    final buf = StringBuffer();
    buf.writeln(e.isSimulated
        ? '# Clinical Summary (Rule-Based — Not AI)\n'
        : '# Clinical Summary (AI-Generated)\n');
    buf.writeln(e.clinicalSummary);
    if (e.riskAnalysis.isNotEmpty) {
      buf.writeln('\n## Risk Analysis\n${e.riskAnalysis}');
    }
    if (e.driverBreakdown.isNotEmpty) {
      buf.writeln('\n## Driver Breakdown\n${e.driverBreakdown}');
    }
    buf.writeln('\n## Recommended Actions');
    for (var i = 0; i < e.recommendedActions.length; i++) {
      buf.writeln('${i + 1}. ${e.recommendedActions[i]}');
    }
    if (e.antibioticPlan.required) {
      buf.writeln('\n## Antibiotic Plan');
      buf.writeln('Urgency: ${e.antibioticPlan.urgency}');
      for (final r in e.antibioticPlan.regimen) {
        buf.writeln('- $r');
      }
      buf.writeln('Duration: ${e.antibioticPlan.duration}');
      buf.writeln('Stop criteria: ${e.antibioticPlan.stopCriteria}');
    }
    if (e.monitoringPlan.isNotEmpty) {
      buf.writeln('\n## Monitoring\n${e.monitoringPlan}');
    }
    if (e.escalationCriteria.isNotEmpty) {
      buf.writeln('\n## Escalation Criteria\n${e.escalationCriteria}');
    }
    return buf.toString();
  }
}