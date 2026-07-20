/// Shared trend computation used by care plan, timeline, and alerts flows.
library;

enum ClinicalTrend { improving, stable, deteriorating, none }

class FieldDelta {
  final String field;
  final double? delta;
  final String? unit;

  const FieldDelta({required this.field, this.delta, this.unit});
}

class TrendResult {
  final ClinicalTrend trend;
  final int assessmentCount;
  final FieldDelta? crpDelta;
  final FieldDelta? tempDelta;
  final FieldDelta? scoreDelta;
  final double? hoursSinceLast;

  const TrendResult({
    required this.trend,
    this.assessmentCount = 0,
    this.crpDelta,
    this.tempDelta,
    this.scoreDelta,
    this.hoursSinceLast,
  });

  static const none = TrendResult(trend: ClinicalTrend.none);
}

class TrendCalculator {
  /// Computes Improving/Deteriorating/Stable from score history (unchanged
  /// behaviour from the original care_plan_screen _computeTrend).
  static ClinicalTrend computeScoreTrend(
    List<Map<String, dynamic>> previousAssessments,
    int currentScore, {
    int stableThreshold = 1,
  }) {
    if (previousAssessments.isEmpty) return ClinicalTrend.none;
    final lastScore =
        (previousAssessments.last['combined_score'] as num?)?.toInt() ??
            currentScore;
    final delta = currentScore - lastScore;
    if (delta.abs() <= stableThreshold) return ClinicalTrend.stable;
    return delta > 0 ? ClinicalTrend.deteriorating : ClinicalTrend.improving;
  }

  /// Full trend result including per-field deltas from assessment_deltas view.
  static TrendResult compute({
    required List<Map<String, dynamic>> previousAssessments,
    required int currentScore,
    Map<String, dynamic>? latestDeltaRow,
    int stableThreshold = 1,
  }) {
    final trend = computeScoreTrend(
      previousAssessments,
      currentScore,
      stableThreshold: stableThreshold,
    );

    if (previousAssessments.isEmpty && latestDeltaRow == null) {
      return TrendResult.none;
    }

    FieldDelta? crpDelta;
    FieldDelta? tempDelta;
    FieldDelta? scoreDelta;
    double? hoursSinceLast;

    if (latestDeltaRow != null) {
      final crp = latestDeltaRow['crp_delta'];
      if (crp != null) {
        crpDelta = FieldDelta(
          field: 'CRP',
          delta: (crp as num).toDouble(),
          unit: 'mg/L',
        );
      }
      final temp = latestDeltaRow['temp_delta'];
      if (temp != null) {
        tempDelta = FieldDelta(
          field: 'Temperature',
          delta: (temp as num).toDouble(),
          unit: '°C',
        );
      }
      final score = latestDeltaRow['score_delta'];
      if (score != null) {
        scoreDelta = FieldDelta(
          field: 'EOSCAL score',
          delta: (score as num).toDouble(),
        );
      }
      final hours = latestDeltaRow['hours_since_last'];
      if (hours != null) {
        hoursSinceLast = (hours as num).toDouble();
      }
    }

    return TrendResult(
      trend: trend,
      assessmentCount: previousAssessments.length,
      crpDelta: crpDelta,
      tempDelta: tempDelta,
      scoreDelta: scoreDelta,
      hoursSinceLast: hoursSinceLast,
    );
  }

  /// Whether two consecutive delta rows show a rising CRP trend.
  static bool hasCrpRisingTrend(List<Map<String, dynamic>> deltaRows) {
    if (deltaRows.length < 2) return false;
    final lastTwo = deltaRows.sublist(deltaRows.length - 2);
    return lastTwo.every((d) {
      final delta = d['crp_delta'];
      return delta != null && (delta as num) > 0;
    });
  }

  /// Whether two consecutive delta rows show temperature instability.
  static bool hasTempInstabilityTrend(
    List<Map<String, dynamic>> deltaRows, {
    double threshold = 0.5,
  }) {
    if (deltaRows.length < 2) return false;
    final lastTwo = deltaRows.sublist(deltaRows.length - 2);
    return lastTwo.every((d) {
      final delta = d['temp_delta'];
      return delta != null && (delta as num).abs() >= threshold;
    });
  }
}
