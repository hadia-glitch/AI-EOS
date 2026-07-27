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
import '../../domain/eoscal_calculator.dart';
import '../patient_state.dart';
import 'guideline_pdf_viewer_screen.dart';

/// Screen 07 — Clinical Evidence Search
///
/// All AI processing (overview + card summaries) uses the BACKEND's Gemini key
/// from backend/.env. The Flutter app never needs a client-side API key for this.
class EvidenceSearchScreen extends ConsumerStatefulWidget {
  const EvidenceSearchScreen({super.key});

  @override
  ConsumerState<EvidenceSearchScreen> createState() =>
      _EvidenceSearchScreenState();
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

  // Feature 2: patient context selector — when a patient is selected, their
  // de-identified clinical snapshot is injected into the RAG retrieval
  // itself (via encounter_id -> patient_context_builder.py on the backend)
  // AND the AI overview/card prompts, so answers apply to this specific
  // patient rather than generically. "No patient selected" in the picker
  // below is the explicit "ask generally" choice.
  PatientParameters? _selectedPatient;

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
      // _selectedPatient is null when "No patient selected" (ask generally)
      // is chosen in the picker below -- this is the retrieval-side half of
      // the same toggle that already drives the AI overview/card narration.
      encounterId: _selectedPatient?.id,
    );

    if (!mounted) return;
    setState(() {
      _rawChunks = chunks;
      _offline = chunks.isEmpty;
      _loadingSearch = false;
      _loadingAi = chunks.isNotEmpty;
    });

    if (chunks.isEmpty) return;

    // Step 2: fetch AI overview + cards from backend, one at a time.
    //
    // These are deliberately sequential, not Future.wait([...]) in parallel:
    // both calls hit the same backend LLM provider chain, and a CPU-served
    // local model (Ollama) processes one generation at a time regardless —
    // it does not run two chat completions concurrently. Firing them
    // together doesn't save wall-clock time (the second one queues behind
    // the first on the backend either way), it just means the second call's
    // full-duration wait sits inside the *same* client timeout window as the
    // first, making a slow-but-working local model look like two failures
    // instead of one success + one legitimately slow request.
    final patientContext = _buildPatientContext(guideline);
    final overview = await evidenceApi.fetchEvidenceOverview(
      query: query,
      activeGuideline: guideline,
      chunks: chunks,
      patientContext: patientContext,
    );
    if (!mounted) return;

    final cards = await evidenceApi.fetchEvidenceCards(
      query: query,
      activeGuideline: guideline,
      chunks: chunks,
      patientContext: patientContext,
    );
    if (!mounted) return;

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
      _filters.contains(source)
          ? _filters.remove(source)
          : _filters.add(source);
    });
    if (_controller.text.trim().length >= 2) {
      _lastQuery = null;
      _search();
    }
  }

  /// De-identified snapshot of the selected patient, computed entirely
  /// locally via EoscalCalculator — no network call. Injected as
  /// patient_context into the evidence AI overview/cards requests so the
  /// LLM answers apply to this specific patient rather than generically.
  Map<String, dynamic>? _buildPatientContext(String activeGuideline) {
    final patient = _selectedPatient;
    if (patient == null) return null;
    final result = EoscalCalculator.calculate(patient);
    final sortedDrivers = [...result.allDrivers]
      ..sort((a, b) => b.contributionPercent.compareTo(a.contributionPercent));
    final activeDrivers = sortedDrivers
        .where((d) => d.points != 0)
        .take(3)
        .map((d) => d.name)
        .toList();
    final hoursOfLife = DateTime.now()
        .difference(patient.birthDateTime)
        .inHours;

    return {
      'gestational_age_weeks': patient.gestationalAgeWeeks,
      'current_risk_category': result.riskCategory.displayName,
      'current_eoscal_score': result.totalScore,
      'active_drivers': activeDrivers,
      'latest_crp': patient.crpLevel,
      'blood_culture_status': patient.bloodCulturePositive == null
          ? 'pending'
          : (patient.bloodCulturePositive! ? 'positive' : 'negative'),
      'hours_of_life': hoursOfLife,
      'active_guideline': activeGuideline,
    };
  }

  void _onPatientSelected(PatientParameters? patient) {
    setState(() => _selectedPatient = patient);
    // Re-run the current search (if any) so the new/removed patient context
    // is reflected in the AI overview and cards immediately.
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
        if (_offline)
          const OfflineBanner(
            message: 'Offline — searching cached guidelines only',
          ),
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
          Text(
            'Clinical Evidence',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 4),
          Text(
            'Search NICE NG195 · AAP 2023 · WHO neonatal sepsis guidelines',
            style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _controller,
            decoration: InputDecoration(
              hintText:
                  'e.g. GBS prophylaxis, empiric antibiotics, CRP monitoring…',
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
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(28),
              ),
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
                    selectedColor: WhoTheme.secondaryTeal.withValues(
                      alpha: 0.2,
                    ),
                    visualDensity: VisualDensity.compact,
                  ),
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 10),
          _buildPatientSelector(),
          const SizedBox(height: 4),
        ],
      ),
    );
  }

  Widget _buildPatientSelector() {
    final patients = ref.watch(patientsProvider);
    final activeGuideline = ref.watch(activeGuidelineProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
          decoration: BoxDecoration(
            color: Colors.grey.shade50,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: Colors.grey.shade200),
          ),
          child: Row(
            children: [
              Icon(Icons.person_outline, size: 16, color: Colors.grey.shade600),
              const SizedBox(width: 8),
              Expanded(
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String?>(
                    isExpanded: true,
                    isDense: true,
                    value: _selectedPatient?.id,
                    hint: const Text(
                      'No patient selected',
                      style: TextStyle(fontSize: 13),
                    ),
                    items: [
                      const DropdownMenuItem<String?>(
                        value: null,
                        child: Text(
                          'No patient selected',
                          style: TextStyle(fontSize: 13),
                        ),
                      ),
                      ...patients.map(
                        (p) => DropdownMenuItem<String?>(
                          value: p.id,
                          child: Text(
                            '${p.name} · ${p.mrn}',
                            style: const TextStyle(fontSize: 13),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ),
                    ],
                    onChanged: (id) {
                      final patient = id == null
                          ? null
                          : patients.cast<PatientParameters?>().firstWhere(
                              (p) => p?.id == id,
                              orElse: () => null,
                            );
                      _onPatientSelected(patient);
                    },
                  ),
                ),
              ),
            ],
          ),
        ),
        if (_selectedPatient != null) ...[
          const SizedBox(height: 8),
          _PatientContextStrip(
            patient: _selectedPatient!,
            activeGuideline: activeGuideline,
          ),
        ],
      ],
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
          _AiOverviewCard(
            overview: _overview,
            personalizedFor: _selectedPatient?.mrn,
          ),

        // No overview and no AI → show nothing (backend has no key, that's ok)
        // We do NOT tell the user to "add an API key" — they can't and shouldn't
        if (_rawChunks.isNotEmpty) ...[
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                Text(
                  '${_cards.isNotEmpty ? _cards.length : _rawChunks.length} results',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
                ),
                if (_aiUnavailable)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 7,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.grey.shade100,
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.grey.shade300),
                    ),
                    child: Text(
                      'Protocol summaries',
                      style: TextStyle(
                        fontSize: 10,
                        color: Colors.grey.shade500,
                      ),
                    ),
                  )
                else if (!_loadingAi && _overview.isNotEmpty)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 7,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFFEEF4FF),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFFBFD3FF)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(
                          Icons.auto_awesome,
                          size: 10,
                          color: Color(0xFF1A56DB),
                        ),
                        const SizedBox(width: 3),
                        const Text(
                          'AI summaries',
                          style: TextStyle(
                            fontSize: 10,
                            color: Color(0xFF1A56DB),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],

        // ── Evidence cards ────────────────────────────────────────────────
        if (_loadingAi)
          ..._buildCardSkeletons()
        else if (_cards.isNotEmpty)
          ..._cards.map(
            (card) => _EvidenceCard(card: card, onTap: () => _openCard(card)),
          )
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
          Text(
            'Search clinical guidelines',
            style: Theme.of(
              context,
            ).textTheme.titleMedium?.copyWith(color: Colors.grey.shade500),
          ),
          const SizedBox(height: 6),
          Text(
            'Try "prolonged ROM", "empiric antibiotics", "GBS prophylaxis"',
            style: TextStyle(fontSize: 13, color: Colors.grey.shade400),
          ),
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
          Text(
            'No results for "${_lastQuery ?? ''}"',
            style: TextStyle(color: Colors.grey.shade500),
          ),
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
        ...List.generate(
          3,
          (_) => Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _shimmerBox(h: 80, radius: 10),
          ),
        ),
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
// Patient context strip (Feature 2) — shown below the patient selector once
// a patient is chosen. Purely local computation (EoscalCalculator), so it
// renders identically online and offline.
// ─────────────────────────────────────────────────────────────────────────────

class _PatientContextStrip extends StatelessWidget {
  final PatientParameters patient;
  final String activeGuideline;
  const _PatientContextStrip({
    required this.patient,
    required this.activeGuideline,
  });

  @override
  Widget build(BuildContext context) {
    final result = EoscalCalculator.calculate(patient);
    final riskColor = result.riskCategory.color;
    final sortedDrivers = [...result.allDrivers]
      ..sort((a, b) => b.contributionPercent.compareTo(a.contributionPercent));
    final topDrivers = sortedDrivers
        .where((d) => d.points != 0)
        .take(3)
        .toList();

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: riskColor.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: riskColor.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: riskColor,
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              '${result.riskCategory.displayName.split(' ').first} · ${result.totalScore}',
              style: const TextStyle(
                color: Colors.white,
                fontSize: 11,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  topDrivers.isEmpty
                      ? 'No active risk drivers'
                      : topDrivers.map((d) => d.name).join(' · '),
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 3),
                Row(
                  children: [
                    Icon(
                      Icons.check_circle,
                      size: 11,
                      color: WhoTheme.secondaryTeal,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Patient context active',
                      style: TextStyle(
                        fontSize: 10,
                        color: WhoTheme.secondaryTeal,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AI overview card
// ─────────────────────────────────────────────────────────────────────────────

class _AiOverviewCard extends StatelessWidget {
  final String overview;

  /// Feature 2: patient reference (mrn or name) when the overview was
  /// generated with patient context — shows a "Personalised for X" tag.
  final String? personalizedFor;
  const _AiOverviewCard({required this.overview, this.personalizedFor});

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
                    Text(
                      'AI Overview',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'From retrieved guidelines',
                  style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                ),
              ),
            ],
          ),
          if (personalizedFor != null) ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
              decoration: BoxDecoration(
                color: WhoTheme.secondaryTeal.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                  color: WhoTheme.secondaryTeal.withValues(alpha: 0.4),
                ),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(
                    Icons.person,
                    size: 10,
                    color: WhoTheme.secondaryTeal,
                  ),
                  const SizedBox(width: 3),
                  Flexible(
                    child: Text(
                      'Personalised for $personalizedFor',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 10,
                        color: WhoTheme.secondaryTeal,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 12),
          Text(overview, style: const TextStyle(fontSize: 14, height: 1.65)),
          const SizedBox(height: 8),
          Text(
            'AI-generated · verify against primary sources.',
            style: TextStyle(
              fontSize: 11,
              fontStyle: FontStyle.italic,
              color: Colors.grey.shade500,
            ),
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
            width: 90,
            height: 22,
            decoration: BoxDecoration(
              color: const Color(0xFFBFD3FF),
              borderRadius: BorderRadius.circular(6),
            ),
          ),
          const SizedBox(height: 12),
          ...[double.infinity, double.infinity, 180.0].map(
            (w) => Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Container(
                height: 13,
                width: w,
                decoration: BoxDecoration(
                  color: Colors.grey.shade200,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
            ),
          ),
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
                  await launchUrl(
                    Uri.parse(docUrl),
                    mode: LaunchMode.externalApplication,
                  );
                },
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: Text(
                        _citationLabel(card.chunk),
                        style: const TextStyle(
                          fontSize: 12,
                          color: Color(0xFF1A56DB),
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 3),
                    const Icon(
                      Icons.arrow_outward,
                      size: 13,
                      color: Color(0xFF1A56DB),
                    ),
                  ],
                ),
              )
            else
              Text(
                _citationLabel(card.chunk),
                style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
              ),
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
                child: Text(
                  chunk.section,
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    fontSize: 14,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            chunk.chunkText,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 13, height: 1.5),
          ),
          if (docUrl.isNotEmpty) ...[
            const SizedBox(height: 8),
            GestureDetector(
              onTap: () async {
                await launchUrl(
                  Uri.parse(docUrl),
                  mode: LaunchMode.externalApplication,
                );
              },
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    chunk.sourceName.isNotEmpty
                        ? chunk.sourceName
                        : chunk.source,
                    style: const TextStyle(
                      fontSize: 12,
                      color: Color(0xFF1A56DB),
                    ),
                  ),
                  const SizedBox(width: 3),
                  const Icon(
                    Icons.arrow_outward,
                    size: 13,
                    color: Color(0xFF1A56DB),
                  ),
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
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SourceBadge(source: card.chunk.source),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    card.chunk.section,
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 15,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              card.headline,
              style: const TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                height: 1.4,
                color: Color(0xFF1A1A2E),
              ),
            ),
            const SizedBox(height: 4),
            if (docUrl.isNotEmpty)
              GestureDetector(
                onTap: () async {
                  await launchUrl(
                    Uri.parse(docUrl),
                    mode: LaunchMode.externalApplication,
                  );
                },
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: Text(
                        docUrl,
                        style: const TextStyle(
                          fontSize: 12,
                          color: Color(0xFF1A56DB),
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 3),
                    const Icon(
                      Icons.arrow_outward,
                      size: 13,
                      color: Color(0xFF1A56DB),
                    ),
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
              child: Text(
                card.processedAnswer,
                style: const TextStyle(fontSize: 14, height: 1.65),
              ),
            ),

            if (card.exactExcerpt.isNotEmpty) ...[
              const SizedBox(height: 20),
              _sectionLabel(
                context,
                Icons.format_quote,
                'Exact excerpt from source',
              ),
              const SizedBox(height: 8),
              InkWell(
                borderRadius: BorderRadius.circular(10),
                onTap: card.chunk.fileName.isNotEmpty
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => GuidelinePdfViewerScreen(
                              fileName: card.chunk.fileName,
                              documentName: card.chunk.source.isNotEmpty
                                  ? '${card.chunk.source} — ${card.chunk.section}'
                                  : card.chunk.section,
                              pageNumber: card.chunk.pageNumber,
                              searchText: card.exactExcerpt,
                            ),
                          ),
                        );
                      }
                    : null,
                child: Container(
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                  decoration: BoxDecoration(
                    color: Colors.grey.shade50,
                    borderRadius: BorderRadius.circular(10),
                    border: Border(
                      left: BorderSide(
                        color: Theme.of(context).colorScheme.primary,
                        width: 4,
                      ),
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '"${card.exactExcerpt}"',
                        style: TextStyle(
                          fontSize: 13,
                          height: 1.6,
                          fontStyle: FontStyle.italic,
                          color: Colors.grey.shade800,
                        ),
                      ),
                      if (card.chunk.fileName.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Row(
                          children: [
                            Icon(
                              Icons.picture_as_pdf_outlined,
                              size: 14,
                              color: Theme.of(context).colorScheme.primary,
                            ),
                            const SizedBox(width: 5),
                            Text(
                              card.chunk.pageNumber != null
                                  ? 'View in source PDF · page ${card.chunk.pageNumber}'
                                  : 'View in source PDF',
                              style: TextStyle(
                                fontSize: 12,
                                fontWeight: FontWeight.w600,
                                color: Theme.of(context).colorScheme.primary,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                '— ${card.chunk.source}, ${card.chunk.section}',
                style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
              ),
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
                        await launchUrl(
                          Uri.parse(docUrl),
                          mode: LaunchMode.externalApplication,
                        );
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
        Text(
          label,
          style: TextStyle(
            fontWeight: FontWeight.bold,
            fontSize: 13,
            color: Theme.of(context).colorScheme.primary,
          ),
        ),
      ],
    );
  }
}