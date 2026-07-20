import 'package:flutter/material.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';

/// Screen 17 — XAI Detail
class XaiDetailScreen extends StatelessWidget {
  final ScoreDriver driver;
  final EoscalResult result;

  const XaiDetailScreen({
    super.key,
    required this.driver,
    required this.result,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Risk Factor Detail — ${driver.name}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    driver.name,
                    style: Theme.of(context).textTheme.headlineMedium,
                  ),
                  const SizedBox(height: 8),
                  Text(driver.reason),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    children: [
                      Chip(
                        label: Text(
                          '+${driver.points} pts (${driver.contributionPercent.toStringAsFixed(0)}%)',
                        ),
                        backgroundColor: driver.points >= 0
                            ? WhoTheme.riskHigh.withValues(alpha: 0.2)
                            : WhoTheme.riskLow.withValues(alpha: 0.2),
                      ),
                      Chip(label: Text('Layer ${driver.layer}')),
                    ],
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          const Text(
            'Threshold table',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              columns: const [
                DataColumn(label: Text('Value')),
                DataColumn(label: Text('Rule')),
                DataColumn(label: Text('Points')),
              ],
              rows: [
                DataRow(
                  cells: [
                    DataCell(
                      Text(
                        driver.reason.split(':').last.trim(),
                        softWrap: true,
                      ),
                    ),
                    DataCell(Text(driver.name, softWrap: true)),
                    DataCell(Text('${driver.points}')),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          OutlinedButton.icon(
            onPressed: () {
              showDialog(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Flag factor'),
                  content: const TextField(
                    decoration: InputDecoration(hintText: 'Reason for flag'),
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('Cancel'),
                    ),
                    TextButton(
                      onPressed: () => Navigator.pop(ctx),
                      child: const Text('Submit'),
                    ),
                  ],
                ),
              );
            },
            icon: const Icon(Icons.flag, color: WhoTheme.riskCritical),
            label: const Text(
              'Flag this factor as clinically inappropriate',
              style: TextStyle(color: WhoTheme.riskCritical),
            ),
          ),
        ],
      ),
    );
  }
}

/// XAI waterfall chart widget for Screen 14
class XaiWaterfallChart extends StatelessWidget {
  final EoscalResult result;
  final void Function(ScoreDriver)? onDriverTap;

  const XaiWaterfallChart({super.key, required this.result, this.onDriverTap});

  @override
  Widget build(BuildContext context) {
    final drivers = result.allDrivers.where((d) => d.points != 0).toList();
    if (drivers.isEmpty) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text('No risk drivers identified'),
        ),
      );
    }

    final maxAbs = drivers
        .map((d) => d.points.abs())
        .reduce((a, b) => a > b ? a : b);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'XAI Driver Breakdown',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            const SizedBox(height: 12),
            ...drivers.map((d) {
              final widthFactor = d.points.abs() / maxAbs;
              final color = d.points >= 0
                  ? WhoTheme.riskCritical
                  : WhoTheme.riskLow;
              return InkWell(
                onTap: onDriverTap != null ? () => onDriverTap!(d) : null,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 110,
                        child: Text(
                          'L${d.layer} ${d.name}',
                          style: const TextStyle(fontSize: 11),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      Expanded(
                        child: Stack(
                          children: [
                            Container(height: 18, color: Colors.grey.shade100),
                            FractionallySizedBox(
                              widthFactor: widthFactor.clamp(0.05, 1.0),
                              child: Container(
                                height: 18,
                                color: color.withValues(alpha: 0.85),
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        '${d.points > 0 ? '+' : ''}${d.points} (${d.contributionPercent.toStringAsFixed(0)}%)',
                        style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }),
          ],
        ),
      ),
    );
  }
}
