import 'package:flutter/material.dart';
import '../../domain/eoscal_calculator.dart';
import '../theme.dart';

class RiskBanner extends StatelessWidget {
  final EoscalResult result;
  final DateTime? assessedAt;

  const RiskBanner({super.key, required this.result, this.assessedAt});

  @override
  Widget build(BuildContext context) {
    final color = result.riskCategory.color;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: color.withValues(alpha: 0.3), blurRadius: 10, offset: const Offset(0, 4)),
        ],
      ),
      child: Column(
        children: [
          Text(
            result.riskCategory.displayName.toUpperCase(),
            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 22),
          ),
          const SizedBox(height: 8),
          Text(
            '${result.totalScore} / 32',
            style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
          ),
          Text(
            'Est. ${result.probabilityPer1000} per 1,000 births',
            style: const TextStyle(color: Colors.white70, fontSize: 13),
          ),
          if (assessedAt != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'Assessed ${assessedAt!.hour}:${assessedAt!.minute.toString().padLeft(2, '0')}',
                style: const TextStyle(color: Colors.white60, fontSize: 11),
              ),
            ),
        ],
      ),
    );
  }
}

class LayerStrip extends StatelessWidget {
  final EoscalResult result;

  const LayerStrip({super.key, required this.result});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(child: LayerBadge(label: 'Layer 1 Maternal', score: result.layer1Score, concern: result.layer1Concern)),
        const SizedBox(width: 8),
        Expanded(child: LayerBadge(label: 'Layer 2 Clinical', score: result.layer2Score, concern: result.layer2Concern)),
        const SizedBox(width: 8),
        Expanded(child: LayerBadge(label: 'Layer 3 Laboratory', score: result.layer3Score, concern: result.layer3Concern)),
      ],
    );
  }
}

class LayerBadge extends StatelessWidget {
  final String label;
  final int score;
  final LayerConcern concern;

  const LayerBadge({super.key, required this.label, required this.score, required this.concern});

  Color get _color {
    switch (concern) {
      case LayerConcern.low:
        return WhoTheme.riskLow;
      case LayerConcern.moderate:
        return WhoTheme.riskIntermediate;
      case LayerConcern.high:
        return WhoTheme.riskHigh;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          children: [
            Text(label, style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold), textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(color: _color, borderRadius: BorderRadius.circular(8)),
              child: Text(
                '$score · ${EoscalCalculator.layerConcernLabel(concern)}',
                style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
