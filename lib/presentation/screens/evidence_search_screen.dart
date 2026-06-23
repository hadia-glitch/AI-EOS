import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/theme.dart';
import '../../core/widgets/offline_banner.dart';
import '../../core/widgets/source_badge.dart';
import '../../data/api/api_client.dart';
import '../../data/api/evidence_api.dart';
import '../../data/models/rag_chunk.dart';
import '../patient_state.dart';

/// Screen 07 — Evidence Search (RAG-backed)
class EvidenceSearchScreen extends ConsumerStatefulWidget {
  const EvidenceSearchScreen({super.key});

  @override
  ConsumerState<EvidenceSearchScreen> createState() => _EvidenceSearchScreenState();
}

class _EvidenceSearchScreenState extends ConsumerState<EvidenceSearchScreen> {
  final _controller = TextEditingController();
  final _filters = <String>{};
  List<RagChunk> _results = [];
  bool _loading = false;
  bool _offline = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final query = _controller.text.trim();
    if (query.length < 2) return;

    setState(() => _loading = true);
    final online = await apiClient.isBackendAvailable();
    final guideline = ref.read(activeGuidelineProvider);
    final filters = _filters.isEmpty ? <String>[] : _filters.toList();

    final results = await evidenceApi.search(
      query: query,
      activeGuideline: guideline,
      sourceFilters: filters,
      limit: 10,
    );

    if (mounted) {
      setState(() {
        _results = results;
        _offline = !online;
        _loading = false;
      });
    }
  }

  void _toggleFilter(String source) {
    setState(() {
      if (_filters.contains(source)) {
        _filters.remove(source);
      } else {
        _filters.add(source);
      }
    });
    if (_controller.text.trim().length >= 2) _search();
  }

  void _showChunkModal(RagChunk chunk) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.6,
        maxChildSize: 0.9,
        builder: (_, scroll) => Padding(
          padding: const EdgeInsets.all(20),
          child: ListView(
            controller: scroll,
            children: [
              Row(
                children: [
                  SourceBadge(source: chunk.source),
                  const SizedBox(width: 8),
                  Expanded(child: Text(chunk.section, style: const TextStyle(fontWeight: FontWeight.bold))),
                ],
              ),
              Text('Similarity: ${(chunk.similarityScore * 100).toStringAsFixed(1)}%',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
              const Divider(height: 24),
              Text(chunk.chunkText),
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: () {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('Citation copied: ${chunk.sourceName} — ${chunk.section}')),
                  );
                },
                icon: const Icon(Icons.copy),
                label: const Text('Copy citation'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (_offline) const OfflineBanner(message: 'Offline — searching cached results only'),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Clinical Evidence', style: Theme.of(context).textTheme.headlineMedium),
              const SizedBox(height: 12),
              TextField(
                controller: _controller,
                decoration: InputDecoration(
                  hintText: 'Search NICE, AAP, WHO guidelines…',
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: _controller.text.isNotEmpty
                      ? IconButton(icon: const Icon(Icons.clear), onPressed: () {
                          _controller.clear();
                          setState(() => _results = []);
                        })
                      : null,
                ),
                onSubmitted: (_) => _search(),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 8),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: ['NICE', 'AAP', 'WHO', 'EOSCAL'].map((s) {
                    final selected = _filters.contains(s);
                    return Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: FilterChip(
                        label: Text(s),
                        selected: selected,
                        onSelected: (_) => _toggleFilter(s),
                        selectedColor: WhoTheme.secondaryTeal.withValues(alpha: 0.2),
                      ),
                    );
                  }).toList(),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: _loading
              ? ListView.builder(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  itemCount: 4,
                  itemBuilder: (_, __) => Card(
                    child: Container(height: 100, margin: const EdgeInsets.all(8), color: Colors.grey.shade200),
                  ),
                )
              : _results.isEmpty
                  ? Center(child: Text('Search clinical evidence…', style: TextStyle(color: Colors.grey.shade500)))
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      itemCount: _results.length,
                      itemBuilder: (context, index) {
                        final chunk = _results[index];
                        return Card(
                          margin: const EdgeInsets.only(bottom: 12),
                          child: ListTile(
                            title: Row(
                              children: [
                                SourceBadge(source: chunk.source),
                                const SizedBox(width: 8),
                                Expanded(
                                  child: Text(chunk.section, style: const TextStyle(fontWeight: FontWeight.bold)),
                                ),
                              ],
                            ),
                            subtitle: Text(
                              chunk.chunkText,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            trailing: Text('${(chunk.similarityScore * 100).toStringAsFixed(0)}%'),
                            onTap: () => _showChunkModal(chunk),
                          ),
                        );
                      },
                    ),
        ),
      ],
    );
  }
}
