import 'package:flutter/material.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';

/// Screen 19 — Antibiotic Status
class AntibioticStatusScreen extends StatelessWidget {
  final PatientParameters patient;
  final String activeGuideline;

  const AntibioticStatusScreen({super.key, required this.patient, required this.activeGuideline});

  @override
  Widget build(BuildContext context) {
    final result = EoscalCalculator.calculate(patient);
    final needsAbx = result.riskCategory == RiskCategory.high || result.riskCategory == RiskCategory.critical;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Align(alignment: Alignment.centerLeft, child: Chip(label: Text(activeGuideline))),
        Card(
          child: ListTile(
            title: const Text('Current Courses'),
            subtitle: Text(needsAbx ? 'Empirical antibiotics recommended' : 'No antibiotics indicated'),
            trailing: needsAbx ? const Icon(Icons.medication, color: WhoTheme.riskHigh) : const Icon(Icons.check, color: WhoTheme.riskLow),
          ),
        ),
        if (needsAbx)
          Card(
            color: WhoTheme.riskIntermediate.withValues(alpha: 0.15),
            child: const ListTile(
              title: Text('Antibiotic Review Due at 36h'),
              subtitle: Text('Culture negative / CRP falling / Clinical review pending'),
            ),
          ),
        if (patient.bloodCulturePositive == true)
          Card(
            child: ListTile(
              title: const Text('Culture-guided rationalisation'),
              subtitle: const Text('Narrow spectrum recommended when sensitivities available'),
              trailing: Chip(label: Text('Organism confirmed'), backgroundColor: WhoTheme.riskCritical.withValues(alpha: 0.2)),
            ),
          ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Stewardship note', style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Text(
                  'Risk ${result.riskCategory.displayName}. ${EoscalCalculator.getRecommendation(guideline: activeGuideline, riskCategory: result.riskCategory)}',
                ),
                TextButton(onPressed: () {}, child: const Text('Copy to clipboard')),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
