import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../core/theme.dart';
import '../../core/widgets/offline_banner.dart';
import '../../core/widgets/source_badge.dart';
import '../../data/api/evidence_api.dart';
import '../../data/guidelines_data.dart';
import '../../data/models/rag_chunk.dart';
import '../patient_state.dart';

/// Screen 07 — Clinical Evidence Search
///
/// All AI processing (overview + card summaries) uses the BACKEND's Gemini key
/// from backend/.env. The Flutter app never needs a client-side API key for this.
class EvidenceSearchScreen extends ConsumerStatefulWidget {
  const EvidenceSearchScreen({super.key});

  @override
  ConsumerState<EvidenceSearchScreen> createState() => _EvidenceSearchScreenState();
}

class _EvidenceSearchScreenState extends ConsumerState<EvidenceSearchScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final _filters = <String>{};

  List<RagChunk> _rawChunks = [];
  List<EvidenceCardResult> _cards = [];
  String _overview = '';

  bool _loadingSearch = false;
  bool _loadingAi = false;
  bool _offline = false;
  // true = backend has no Gemini key (cards are rule-based), false = AI cards
  bool _aiUnavailable = false;
  String? _lastQuery;

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  // ── Search ────────────────────────────────────────────────────────────────

  Future<void> _search() async {
    final query = _controller.text.trim();
    if (query.length < 2) return;
    if (query == _lastQuery) return;
    _lastQuery = query;

    setState(() {
      _loadingSearch = true;
      _loadingAi = false;
      _rawChunks = [];
      _cards = [];
      _overview = '';
      _aiUnavailable = false;
    });

    final guideline = ref.read(activeGuidelineProvider);
    final filters = _filters.isEmpty ? <String>[] : _filters.toList();

    // Step 1: fetch chunks
    final chunks = await evidenceApi.search(
      query: query,
      activeGuideline: guideline,
      sourceFilters: filters,
      limit: 8,
    );

    if (!mounted) return;
    setState(() {
      _rawChunks = chunks;
      _offline = chunks.isEmpty;
      _loadingSearch = false;
      _loadingAi = chunks.isNotEmpty;
    });

    if (chunks.isEmpty) return;

    // Step 2: fetch AI overview + cards from backend in parallel
    final overviewFuture = evidenceApi.fetchEvidenceOverview(
      query: query,
      activeGuideline: guideline,
      chunks: chunks,
    );
    final cardsFuture = evidenceApi.fetchEvidenceCards(
      query: query,
      activeGuideline: guideline,
      chunks: chunks,
    );

    final results = await Future.wait([overviewFuture, cardsFuture]);
    if (!mounted) return;

    final overview = results[0] as String;
    final cards = results[1] as List<EvidenceCardResult>;

    // Detect whether Gemini was actually used
    // (If overview is empty AND cards match raw chunk count with no headlines
    //  that differ from section names, Gemini was not available)
    final geminiUsed = overview.isNotEmpty;

    setState(() {
      _overview = overview;
      _cards = cards;
      _aiUnavailable = !geminiUsed;
      _loadingAi = false;
    });
  }

  void _toggleFilter(String source) {
    setState(() {
      _filters.contains(source) ? _filters.remove(source) : _filters.add(source);
    });
    if (_controller.text.trim().length >= 2) {
      _lastQuery = null;
      _search();
    }
  }

  void _openCard(EvidenceCardResult card) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _CardDetailSheet(card: card),
    );
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (_offline) const OfflineBanner(message: 'Offline — searching cached guidelines only'),
        _buildSearchBar(),
        Expanded(
          child: _loadingSearch
              ? _buildSearchSkeletons()
              : _rawChunks.isEmpty && _lastQuery != null
                  ? _buildNoResults()
                  : _rawChunks.isEmpty
                      ? _buildEmptyState()
                      : _buildResults(),
        ),
      ],
    );
  }

  Widget _buildSearchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Clinical Evidence', style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 4),
          Text(
            'Search NICE NG195 · AAP 2023 · WHO neonatal sepsis guidelines',
            style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _controller,
            decoration: InputDecoration(
              hintText: 'e.g. GBS prophylaxis, empiric antibiotics, CRP monitoring…',
              prefixIcon: const Icon(Icons.search),
              suffixIcon: _controller.text.isNotEmpty
                  ? IconButton(
                      icon: const Icon(Icons.clear),
                      onPressed: () {
                        _controller.clear();
                        _lastQuery = null;
                        setState(() {
                          _rawChunks = [];
                          _cards = [];
                          _overview = '';
                        });
                      },
                    )
                  : null,
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(28)),
              filled: true,
              fillColor: Colors.grey.shade50,
            ),
            onSubmitted: (_) => _search(),
            onChanged: (_) => setState(() {}),
            textInputAction: TextInputAction.search,
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
                    label: Text(s, style: const TextStyle(fontSize: 12)),
                    selected: selected,
                    onSelected: (_) => _toggleFilter(s),
                    selectedColor: WhoTheme.secondaryTeal.withValues(alpha: 0.2),
                    visualDensity: VisualDensity.compact,
                  ),
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 4),
        ],
      ),
    );
  }

  Widget _buildResults() {
    return ListView(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: [
        // ── AI Overview ────────────────────────────────────────────────────
        if (_loadingAi)
          _AiOverviewSkeleton()
        else if (_overview.isNotEmpty)
          _AiOverviewCard(overview: _overview)
        // No overview and no AI → show nothing (backend has no key, that's ok)
        // We do NOT tell the user to "add an API key" — they can't and shouldn't
        ,

        if (_rawChunks.isNotEmpty) ...[
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              children: [
                Text(
                  '${_cards.isNotEmpty ? _cards.length : _rawChunks.length} results',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
                ),
                if (_aiUnavailable) ...[
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: Colors.grey.shade100,
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.grey.shade300),
                    ),
                    child: Text(
                      'Protocol summaries',
                      style: TextStyle(fontSize: 10, color: Colors.grey.shade500),
                    ),
                  ),
                ] else if (!_loadingAi && _overview.isNotEmpty) ...[
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: const Color(0xFFEEF4FF),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFFBFD3FF)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.auto_awesome, size: 10, color: Color(0xFF1A56DB)),
                        const SizedBox(width: 3),
                        const Text('AI summaries', style: TextStyle(fontSize: 10, color: Color(0xFF1A56DB))),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],

        // ── Evidence cards ────────────────────────────────────────────────
        if (_loadingAi)
          ..._buildCardSkeletons()
        else if (_cards.isNotEmpty)
          ..._cards.map((card) => _EvidenceCard(
                card: card,
                onTap: () => _openCard(card),
              ))
        else
          ..._rawChunks.map((chunk) => _RawChunkCard(chunk: chunk)),
      ],
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.menu_book_outlined, size: 56, color: Colors.grey.shade300),
          const SizedBox(height: 16),
          Text('Search clinical guidelines',
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(color: Colors.grey.shade500)),
          const SizedBox(height: 6),
          Text('Try "prolonged ROM", "empiric antibiotics", "GBS prophylaxis"',
              style: TextStyle(fontSize: 13, color: Colors.grey.shade400)),
        ],
      ),
    );
  }

  Widget _buildNoResults() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.search_off, size: 48, color: Colors.grey.shade300),
          const SizedBox(height: 12),
          Text('No results for "${_lastQuery ?? ''}"',
              style: TextStyle(color: Colors.grey.shade500)),
        ],
      ),
    );
  }

  Widget _buildSearchSkeletons() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _shimmerBox(h: 100, radius: 12),
        const SizedBox(height: 16),
        ...List.generate(3, (_) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: _shimmerBox(h: 80, radius: 10),
            )),
      ],
    );
  }

  List<Widget> _buildCardSkeletons() {
    return List.generate(
      4,
      (_) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: _shimmerBox(h: 90, radius: 10),
      ),
    );
  }

  Widget _shimmerBox({required double h, double radius = 8}) {
    return Container(
      height: h,
      decoration: BoxDecoration(
        color: Colors.grey.shade100,
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AI overview card
// ─────────────────────────────────────────────────────────────────────────────

class _AiOverviewCard extends StatelessWidget {
  final String overview;
  const _AiOverviewCard({required this.overview});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFEEF4FF),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFBFD3FF)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: const Color(0xFF1A56DB),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.auto_awesome, size: 12, color: Colors.white),
                    SizedBox(width: 4),
                    Text('AI Overview',
                        style: TextStyle(
                            color: Colors.white,
                            fontSize: 11,
                            fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Text('From retrieved guidelines',
                  style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
            ],
          ),
          const SizedBox(height: 12),
          Text(overview, style: const TextStyle(fontSize: 14, height: 1.65)),
          const SizedBox(height: 8),
          Text(
            'AI-generated · verify against primary sources.',
            style: TextStyle(
                fontSize: 11,
                fontStyle: FontStyle.italic,
                color: Colors.grey.shade500),
          ),
        ],
      ),
    );
  }
}

class _AiOverviewSkeleton extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFEEF4FF),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFBFD3FF)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
              width: 90, height: 22,
              decoration: BoxDecoration(
                  color: const Color(0xFFBFD3FF),
                  borderRadius: BorderRadius.circular(6))),
          const SizedBox(height: 12),
          ...[double.infinity, double.infinity, 180.0].map((w) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Container(
                    height: 13, width: w,
                    decoration: BoxDecoration(
                        color: Colors.grey.shade200,
                        borderRadius: BorderRadius.circular(4))),
              )),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Evidence card (AI-processed or rule-based)
// ─────────────────────────────────────────────────────────────────────────────

class _EvidenceCard extends StatelessWidget {
  final EvidenceCardResult card;
  final VoidCallback onTap;

  const _EvidenceCard({required this.card, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final docUrl = card.chunk.documentUrl;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.grey.shade200),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.03),
              blurRadius: 6,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SourceBadge(source: card.chunk.source),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    card.chunk.section,
                    style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              card.headline,
              style: const TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w600,
                height: 1.4,
                color: Color(0xFF1A1A2E),
              ),
            ),
            const SizedBox(height: 8),
            if (docUrl.isNotEmpty)
              GestureDetector(
                onTap: () async {
                  await launchUrl(Uri.parse(docUrl),
                      mode: LaunchMode.externalApplication);
                },
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: Text(
                        _citationLabel(card.chunk),
                        style: const TextStyle(
                            fontSize: 12, color: Color(0xFF1A56DB)),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 3),
                    const Icon(Icons.arrow_outward,
                        size: 13, color: Color(0xFF1A56DB)),
                  ],
                ),
              )
            else
              Text(_citationLabel(card.chunk),
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500)),
          ],
        ),
      ),
    );
  }

  String _citationLabel(GuidelineChunk c) {
    final url = c.documentUrl;
    if (url.isEmpty) return '${c.source} — ${c.section}';
    try {
      final uri = Uri.parse(url);
      return '${c.source} — ${c.section} · ${uri.host}${uri.path}';
    } catch (_) {
      return '${c.source} — ${c.section}';
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Raw chunk fallback card (no AI)
// ─────────────────────────────────────────────────────────────────────────────

class _RawChunkCard extends StatelessWidget {
  final RagChunk chunk;
  const _RawChunkCard({required this.chunk});

  @override
  Widget build(BuildContext context) {
    final docUrl = CitationItem.resolveDocumentUrl(chunk.sourceName);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.grey.shade200),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              SourceBadge(source: chunk.source),
              const SizedBox(width: 8),
              Expanded(
                child: Text(chunk.section,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(chunk.chunkText,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 13, height: 1.5)),
          if (docUrl.isNotEmpty) ...[
            const SizedBox(height: 8),
            GestureDetector(
              onTap: () async {
                await launchUrl(Uri.parse(docUrl),
                    mode: LaunchMode.externalApplication);
              },
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    chunk.sourceName.isNotEmpty ? chunk.sourceName : chunk.source,
                    style: const TextStyle(fontSize: 12, color: Color(0xFF1A56DB)),
                  ),
                  const SizedBox(width: 3),
                  const Icon(Icons.arrow_outward,
                      size: 13, color: Color(0xFF1A56DB)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Card detail bottom sheet
// ─────────────────────────────────────────────────────────────────────────────

class _CardDetailSheet extends StatelessWidget {
  final EvidenceCardResult card;
  const _CardDetailSheet({required this.card});

  @override
  Widget build(BuildContext context) {
    final docUrl = card.chunk.documentUrl;

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.65,
      maxChildSize: 0.93,
      builder: (_, scroll) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
        child: ListView(
          controller: scroll,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.only(bottom: 18),
                decoration: BoxDecoration(
                    color: Colors.grey.shade300,
                    borderRadius: BorderRadius.circular(2)),
              ),
            ),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SourceBadge(source: card.chunk.source),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(card.chunk.section,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 15)),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(card.headline,
                style: const TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w700,
                    height: 1.4,
                    color: Color(0xFF1A1A2E))),
            const SizedBox(height: 4),
            if (docUrl.isNotEmpty)
              GestureDetector(
                onTap: () async {
                  await launchUrl(Uri.parse(docUrl),
                      mode: LaunchMode.externalApplication);
                },
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: Text(docUrl,
                          style: const TextStyle(
                              fontSize: 12, color: Color(0xFF1A56DB)),
                          overflow: TextOverflow.ellipsis),
                    ),
                    const SizedBox(width: 3),
                    const Icon(Icons.arrow_outward,
                        size: 13, color: Color(0xFF1A56DB)),
                  ],
                ),
              ),
            const Divider(height: 28),

            // AI-processed answer
            _sectionLabel(context, Icons.auto_awesome, 'AI Analysis'),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: const Color(0xFFEEF4FF),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: const Color(0xFFBFD3FF)),
              ),
              child: Text(card.processedAnswer,
                  style: const TextStyle(fontSize: 14, height: 1.65)),
            ),

            if (card.exactExcerpt.isNotEmpty) ...[
              const SizedBox(height: 20),
              _sectionLabel(context, Icons.format_quote, 'Exact excerpt from source'),
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                decoration: BoxDecoration(
                  color: Colors.grey.shade50,
                  borderRadius: BorderRadius.circular(10),
                  border: Border(
                    left: BorderSide(
                        color: Theme.of(context).colorScheme.primary,
                        width: 4),
                  ),
                ),
                child: Text(
                  '"${card.exactExcerpt}"',
                  style: TextStyle(
                      fontSize: 13,
                      height: 1.6,
                      fontStyle: FontStyle.italic,
                      color: Colors.grey.shade800),
                ),
              ),
              const SizedBox(height: 6),
              Text('— ${card.chunk.source}, ${card.chunk.section}',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500)),
            ],

            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () {
                      final citation =
                          '${card.chunk.source} — ${card.chunk.section}. $docUrl';
                      Clipboard.setData(ClipboardData(text: citation));
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Citation copied')),
                      );
                    },
                    icon: const Icon(Icons.copy, size: 16),
                    label: const Text('Copy citation'),
                  ),
                ),
                if (docUrl.isNotEmpty) ...[
                  const SizedBox(width: 10),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: () async {
                        await launchUrl(Uri.parse(docUrl),
                            mode: LaunchMode.externalApplication);
                      },
                      icon: const Icon(Icons.open_in_new, size: 16),
                      label: const Text('Open guideline'),
                    ),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _sectionLabel(BuildContext context, IconData icon, String label) {
    return Row(
      children: [
        Icon(icon, size: 16, color: Theme.of(context).colorScheme.primary),
        const SizedBox(width: 6),
        Text(label,
            style: TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 13,
                color: Theme.of(context).colorScheme.primary)),
      ],
    );
  }
}