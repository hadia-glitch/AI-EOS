import '../data/gemini_service.dart';
import '../data/offline_guideline_cache.dart';
import 'curated_care_plan_rules.dart';
import 'eoscal_calculator.dart';

/// Builds an offline clinical care plan. Three tiers, tried in order:
///
///   1. Synced guideline knowledge cache (OfflineGuidelineCache) — LLM- or
///      curated-synthesized per (guideline, category), pulled from the
///      backend whenever online. Reflects any local/institutional guideline
///      uploaded via Settings > Guideline Configuration, once synced.
///   2. Built-in rule-based protocol (CuratedCarePlanRules) — hardcoded in
///      the app binary, evidence-based from NICE/AAP/WHO, requires ZERO
///      network access ever. This is what makes offline care plans work
///      even on a device that has never successfully synced anything —
///      the gap that used to show "no offline care plan is cached yet".
///
/// [build] therefore practically never returns null — tier 2 always has an
/// answer. It can still fail to produce a plan only if something throws
/// while formatting (defensive only; not expected in practice).
class OfflineCarePlanBuilder {
  static Future<ClinicalCarePlan> build({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
  }) async {
    final category = result.riskCategory.name.toUpperCase();

    // ── Tier 1: synced guideline knowledge cache ────────────────────────────
    final synced = await OfflineGuidelineCache.getProtocol(activeGuideline, category);
    if (synced != null) {
      final lastSynced = await OfflineGuidelineCache.lastSynced();
      final syncedLabel = lastSynced != null ? _formatDate(lastSynced.toLocal()) : 'unknown date';

      final actions = (synced['recommended_actions'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList();
      final abx = synced['antibiotic_plan'] as Map<String, dynamic>? ?? {};
      final antibioticPlan = AntibioticPlan(
        required: abx['required'] as bool? ?? false,
        urgency: abx['urgency'] as String? ?? 'Reassess when connectivity returns',
        regimen: (abx['regimen'] as List<dynamic>? ?? []).map((e) => e.toString()).toList(),
        duration: abx['duration'] as String? ?? '',
        stopCriteria: abx['stop_criteria'] as String? ?? '',
      );

      return ClinicalCarePlan(
        clinicalSummary: _fill(synced['clinical_summary_template'] as String?, patient, result),
        riskAnalysis: _fill(synced['risk_analysis_template'] as String?, patient, result),
        trendNarrative: '',
        driverBreakdown: _buildDriverBreakdown(result),
        recommendedActions: actions,
        antibioticPlan: antibioticPlan,
        monitoringPlan: synced['monitoring_plan'] as String? ?? '',
        escalationCriteria: synced['escalation_criteria'] as String? ?? '',
        guidelineCitations: const [],
        isSimulated: true,
        sourceLabel: 'Offline · cached $activeGuideline protocol (synced $syncedLabel) '
            '— not generated for this specific patient',
      );
    }

    // ── Tier 2: built-in curated rules (guaranteed, zero network) ───────────
    // Reached when this device has never synced a protocol for this exact
    // (guideline, category) pair — e.g. first launch with no connectivity
    // yet, or the backend cache genuinely has no entry. CuratedCarePlanRules
    // always returns something, so this branch always succeeds.
    final curated = CuratedCarePlanRules.getProtocol(activeGuideline, category);
    final isKnownGuideline = CuratedCarePlanRules.hasExplicitProtocolFor(activeGuideline);

    final antibioticPlan = AntibioticPlan(
      required: curated.antibioticRequired,
      urgency: curated.antibioticUrgency,
      regimen: curated.antibioticRegimen,
      duration: curated.antibioticDuration,
      stopCriteria: curated.antibioticStopCriteria,
    );

    // Be explicit when we've had to substitute a generic NICE-equivalent
    // protocol for an unrecognised guideline (e.g. a local/institutional
    // source this device hasn't synced real content for yet) — a clinician
    // should never mistake this for their institution's actual uploaded
    // guideline just because the label says "offline".
    final guidelineNote = isKnownGuideline
        ? activeGuideline
        : '$activeGuideline (using built-in general-practice defaults — this '
            'guideline\'s specific content hasn\'t synced to this device yet)';

    return ClinicalCarePlan(
      clinicalSummary: _fill(curated.clinicalSummaryTemplate, patient, result),
      riskAnalysis: _fill(curated.riskAnalysisTemplate, patient, result),
      trendNarrative: '',
      driverBreakdown: _buildDriverBreakdown(result),
      recommendedActions: curated.recommendedActions,
      antibioticPlan: antibioticPlan,
      monitoringPlan: curated.monitoringPlan,
      escalationCriteria: curated.escalationCriteria,
      guidelineCitations: const [],
      isSimulated: true,
      sourceLabel: 'Offline · built-in rule-based protocol · $guidelineNote '
          '— not AI-generated, not specific to this patient',
    );
  }

  static String _fill(String? template, PatientParameters patient, EoscalResult result) {
    if (template == null || template.isEmpty) return '';
    return template
        .replaceAll('{patient_ref}', patient.name.isNotEmpty ? patient.name : 'This patient')
        .replaceAll('{total_score}', '${result.totalScore}')
        .replaceAll('{layer1_score}', '${result.layer1Score}')
        .replaceAll('{layer2_score}', '${result.layer2Score}')
        .replaceAll('{layer3_score}', '${result.layer3Score}');
  }

  /// Builds a real, patient-specific driver breakdown from EoscalResult —
  /// the same deterministic data XaiWaterfallChart on the Risk tab uses.
  /// Both offline tiers were previously just filling in a static template
  /// string ("See individual risk drivers below.") that promised detail
  /// nothing else on the Care Plan screen actually provided — this
  /// generates the real thing instead, and needs no AI or network since
  /// EoscalResult.allDrivers is already fully computed client-side.
  static String _buildDriverBreakdown(EoscalResult result) {
    final drivers = result.allDrivers.where((d) => d.points != 0).toList()
      ..sort((a, b) => b.points.abs().compareTo(a.points.abs()));

    if (drivers.isEmpty) {
      return 'No individual risk drivers contributed to this score — all assessed '
          'parameters were within the low-risk range.';
    }

    const layerNames = {1: 'Maternal', 2: 'Clinical', 3: 'Laboratory'};

    return drivers.map((d) {
      final sign = d.points > 0 ? '+' : '';
      final layerName = layerNames[d.layer] ?? 'Layer ${d.layer}';
      return '• ${d.name} ($layerName, $sign${d.points} pts, '
          '${d.contributionPercent.toStringAsFixed(0)}% of score): ${d.reason}';
    }).join('\n');
  }

  static String _formatDate(DateTime d) {
    final months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return '${months[d.month - 1]} ${d.day}, ${d.year}';
  }
}