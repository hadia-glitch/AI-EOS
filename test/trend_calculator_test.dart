import 'package:flutter_test/flutter_test.dart';
import 'package:eos/domain/trend_calculator.dart';

void main() {
  group('TrendCalculator', () {
    test('computeScoreTrend returns none with no history', () {
      expect(
        TrendCalculator.computeScoreTrend([], 5),
        ClinicalTrend.none,
      );
    });

    test('computeScoreTrend detects deteriorating score', () {
      final previous = [
        {'combined_score': 3},
      ];
      expect(
        TrendCalculator.computeScoreTrend(previous, 6),
        ClinicalTrend.deteriorating,
      );
    });

    test('hasCrpRisingTrend requires two consecutive positive deltas', () {
      final deltas = [
        {'crp_delta': 1.0},
        {'crp_delta': 0.5},
      ];
      expect(TrendCalculator.hasCrpRisingTrend(deltas), isTrue);
      expect(
        TrendCalculator.hasCrpRisingTrend([{'crp_delta': 1.0}]),
        isFalse,
      );
    });

    test('compute surfaces per-field deltas from latest row', () {
      final result = TrendCalculator.compute(
        previousAssessments: [{'combined_score': 4}],
        currentScore: 6,
        latestDeltaRow: {
          'crp_delta': 2.5,
          'temp_delta': -0.3,
          'score_delta': 2,
          'hours_since_last': 12.0,
        },
      );
      expect(result.crpDelta?.delta, 2.5);
      expect(result.tempDelta?.delta, -0.3);
      expect(result.scoreDelta?.delta, 2);
      expect(result.hoursSinceLast, 12.0);
    });
  });
}
