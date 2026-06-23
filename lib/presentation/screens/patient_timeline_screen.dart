import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';

/// Screen 16 — Patient Timeline
class PatientTimelineScreen extends StatelessWidget {
  final PatientParameters patient;
  final EoscalResult result;

  const PatientTimelineScreen({super.key, required this.patient, required this.result});

  @override
  Widget build(BuildContext context) {
    final ageHours = DateTime.now().difference(patient.birthDateTime).inHours.clamp(0, 72);
    final spots = [FlSpot(0, result.totalScore.toDouble()), FlSpot(ageHours.toDouble(), result.totalScore.toDouble())];

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SizedBox(
          height: 220,
          child: LineChart(
            LineChartData(
              minY: 0,
              maxY: 32,
              lineBarsData: [
                LineChartBarData(
                  spots: spots,
                  isCurved: true,
                  color: result.riskCategory.color,
                  barWidth: 3,
                  dotData: const FlDotData(show: true),
                ),
              ],
              titlesData: const FlTitlesData(show: true),
              gridData: FlGridData(show: true, drawVerticalLine: false),
              borderData: FlBorderData(show: true),
            ),
          ),
        ),
        const SizedBox(height: 16),
        if (patient.crpLevel != null)
          ListTile(
            leading: const Icon(Icons.show_chart, color: WhoTheme.secondaryTeal),
            title: Text('CRP: ${patient.crpLevel} mg/L'),
          ),
        Card(
          child: ListTile(
            leading: Icon(Icons.event, color: result.riskCategory.color),
            title: const Text('Initial assessment'),
            subtitle: Text('Score ${result.totalScore} → ${result.riskCategory.displayName}'),
          ),
        ),
      ],
    );
  }
}
