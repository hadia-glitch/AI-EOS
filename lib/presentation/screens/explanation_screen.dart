import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/theme.dart';
import '../../core/widgets/offline_banner.dart';
import '../../core/widgets/source_badge.dart';
import '../../data/api/explanation_api.dart';
import '../../data/models/rag_chunk.dart';
import '../../domain/eoscal_calculator.dart';

/// Screen 15 — AI Clinical Explanation (backend RAG + Gemini)
class ExplanationScreen extends ConsumerStatefulWidget {
  final PatientParameters patient;
  final EoscalResult result;
  final String activeGuideline;

  const ExplanationScreen({
    super.key,
    required this.patient,
    required this.result,
    required this.activeGuideline,
  });

  @override
  ConsumerState<ExplanationScreen> createState() => _ExplanationScreenState();
}

class _ExplanationScreenState extends ConsumerState<ExplanationScreen> {
  late Future<ClinicalExplanation> _future;

  @override
  void initState() {
    super.initState();
    _future = explanationApi.generate(
      encounterId: widget.patient.id,
      patient: widget.patient,
      result: widget.result,
      activeGuideline: widget.activeGuideline,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinical Explanation'),
        actions: [
          IconButton(
            icon: const Icon(Icons.format_list_bulleted),
            tooltip: 'Citations',
            onPressed: () {
              Scrollable.ensureVisible(context);
            },
          ),
        ],
      ),
      body: FutureBuilder<ClinicalExplanation>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }

          if (snapshot.hasError || !snapshot.hasData) {
            return Center(child: Text('Error: ${snapshot.error}'));
          }

          final exp = snapshot.data!;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (exp.generatedOffline)
                const OfflineBanner(message: 'Generated offline — using cached explanation'),
              if (exp.fallbackUsed && !exp.generatedOffline)
                Container(
                  padding: const EdgeInsets.all(12),
                  color: WhoTheme.riskIntermediate.withValues(alpha: 0.2),
                  child: const Text('Fallback explanation — rule-based'),
                ),
              if (!exp.fallbackUsed && !exp.generatedOffline)
                Container(
                  padding: const EdgeInsets.all(8),
                  color: WhoTheme.riskLow.withValues(alpha: 0.15),
                  child: Text('Explanation generated · ${exp.modelVersion}',
                      style: const TextStyle(fontSize: 12)),
                ),
              const SizedBox(height: 12),
              _sectionCard('Summary', [
                Text(exp.clinicalSummary, style: const TextStyle(height: 1.5)),
                const SizedBox(height: 8),
                Text(
                  'AI-generated — clinical judgement supersedes this output',
                  style: TextStyle(fontSize: 11, fontStyle: FontStyle.italic, color: Colors.grey.shade600),
                ),
              ]),
              if (exp.perDriverExplanations.isNotEmpty)
                _sectionCard(
                  'Risk Factor Explanations',
                  exp.perDriverExplanations.map((d) {
                    return ExpansionTile(
                      title: Text(d['factor'] ?? ''),
                      children: [Padding(padding: const EdgeInsets.all(12), child: Text(d['explanation'] ?? ''))],
                    );
                  }).toList(),
                ),
              _sectionCard('Guideline Evidence', [Text(exp.evidenceSummary, style: const TextStyle(height: 1.5))]),
              _sectionCard(
                'Sources Used',
                exp.citationList.isNotEmpty
                    ? exp.citationList.map((c) => ListTile(
                          leading: SourceBadge(source: c.source.length > 6 ? c.source.substring(0, 4) : c.source),
                          title: Text('${c.source} — ${c.section}'),
                          subtitle: Text('Chunk ${c.chunkId} · ${(c.similarityScore * 100).toStringAsFixed(0)}%'),
                        )).toList()
                    : exp.ragChunks.map((c) => ListTile(
                          leading: SourceBadge(source: c.source),
                          title: Text('${c.sourceName} — ${c.section}'),
                          subtitle: Text('Chunk ${c.chunkId}'),
                        )).toList(),
              ),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.grey.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(exp.confidenceDisclaimer, style: TextStyle(fontSize: 12, color: Colors.grey.shade700)),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _sectionCard(String title, List<Widget> children) {
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            const SizedBox(height: 12),
            ...children,
          ],
        ),
      ),
    );
  }
}
