import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/theme.dart';
import '../../core/widgets/risk_banner.dart';
import '../../domain/eoscal_calculator.dart';
import 'explanation_screen.dart';
import 'xai_detail_screen.dart';

/// Screen 14 content — Risk Assessment Results
class RiskResultsTab extends ConsumerStatefulWidget {
  final PatientParameters patient;
  final String activeGuideline;
  final bool compact;

  const RiskResultsTab({
    super.key,
    required this.patient,
    required this.activeGuideline,
    this.compact = false,
  });

  @override
  ConsumerState<RiskResultsTab> createState() => _RiskResultsTabState();
}

class _RiskResultsTabState extends ConsumerState<RiskResultsTab> {
  late String _guideline;

  @override
  void initState() {
    super.initState();
    _guideline = widget.activeGuideline;
  }

  @override
  Widget build(BuildContext context) {
    final result = EoscalCalculator.calculate(widget.patient);
    final recommendation = EoscalCalculator.getRecommendation(
      guideline: _guideline,
      riskCategory: result.riskCategory,
      combinationNote: result.combinationNote,
    );

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          RiskBanner(result: result, assessedAt: DateTime.now()),
          if (result.belowEoscalScope)
            Container(
              margin: const EdgeInsets.only(top: 8),
              padding: const EdgeInsets.all(12),
              color: WhoTheme.riskIntermediate.withValues(alpha: 0.2),
              child: const Text('⚠ Below EOSCAL 2024 validated scope (≥35 weeks)'),
            ),
          const SizedBox(height: 16),
          LayerStrip(result: result),
          const SizedBox(height: 16),
          XaiWaterfallChart(
            result: result,
            onDriverTap: (d) => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => XaiDetailScreen(driver: d, result: result)),
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            children: ['NICE', 'AAP', 'WHO'].map((g) {
              final active = _guideline == g;
              return ChoiceChip(
                label: Text(g),
                selected: active,
                onSelected: (_) => setState(() => _guideline = g),
              );
            }).toList(),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Text('Recommended Actions', style: TextStyle(fontWeight: FontWeight.bold)),
                      const Spacer(),
                      Chip(label: Text(_guideline), backgroundColor: WhoTheme.secondaryTeal.withValues(alpha: 0.2)),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(recommendation),
                ],
              ),
            ),
          ),
          if (!widget.compact) ...[
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => ExplanationScreen(
                      patient: widget.patient,
                      result: result,
                      activeGuideline: _guideline,
                    ),
                  ),
                ),
                icon: const Icon(Icons.psychology),
                label: const Text('View Clinical Explanation →'),
                style: OutlinedButton.styleFrom(foregroundColor: WhoTheme.secondaryTeal),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
