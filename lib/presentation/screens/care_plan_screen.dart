import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../core/theme.dart';
import '../../core/widgets/fact_check_badge.dart';
import '../../data/api/explanation_api.dart';
import '../../data/auth_service.dart';
import '../../data/gemini_service.dart';
import '../../data/models/rag_chunk.dart';
import '../../data/supabase_config.dart';
import '../../domain/eoscal_calculator.dart';
import '../../domain/offline_care_plan_builder.dart';

/// Screen 15 (v2) — Clinical Care Plan
///
/// Replaces ExplanationScreen everywhere except where ExplanationScreen is
/// explicitly kept for backward compat. Produces a complete, chronologically
/// aware clinical care plan any doctor on duty can follow — not just a
/// neonatologist:
///   A. Patient Development Summary (with trend across prior assessments)
///   B. Immediate Actions (next 60 minutes)
///   C. Antibiotic Plan (3 explicit conditional branches, always shown)
///   D. Monitoring Schedule (hard-coded NICE NG195 table — never LLM output)
///   E. Escalation Criteria (hard-coded triggers — never LLM output)
///   F. Driver Breakdown
///   G. Guideline Sources
///
/// Source of care plan, in priority order:
///   1. Backend /api/v1/encounters/{id}/care-plan
///      → uses backend .env GEMINI_API_KEY, Gemini→Groq→rule-based fallback
///      → checks llm_explanations cache first (cache_key prefixed "careplan:")
///   2. If backend is unreachable: a clear, targeted error — NEVER a silent
///      client-side substitute. Clinicians must always know when a plan is
///      AI-generated vs rule-based vs unavailable.
class CarePlanScreen extends ConsumerStatefulWidget {
  final PatientParameters patient;
  final EoscalResult result;
  final String activeGuideline;

  /// When true, renders without Scaffold/AppBar for embedding inside a tab
  /// (PatientDetailScreen tab 4).
  final bool embeddedInTab;

  const CarePlanScreen({
    super.key,
    required this.patient,
    required this.result,
    required this.activeGuideline,
    this.embeddedInTab = false,
  });

  @override
  ConsumerState<CarePlanScreen> createState() => _CarePlanScreenState();
}

enum _Trend { improving, stable, deteriorating, none }

class _CarePlanScreenState extends ConsumerState<CarePlanScreen> {
  late Future<ClinicalCarePlan> _future;
  List<Map<String, dynamic>> _previousAssessments = [];
  _Trend _trend = _Trend.none;

  @override
  void initState() {
    super.initState();
    _future = _loadAndGenerate();
  }

  // ── Fetch chronological history, then generate the plan ──────────────────

  Future<ClinicalCarePlan> _loadAndGenerate() async {
    _previousAssessments = await _fetchPreviousAssessments();
    _trend = _computeTrend(_previousAssessments, widget.result.totalScore);
    try {
      return await explanationApi.generateCarePlan(
        encounterId: widget.patient.id,
        patient: widget.patient,
        result: widget.result,
        activeGuideline: widget.activeGuideline,
        previousAssessments: _previousAssessments,
      );
    } on BackendUnreachableException {
      // Health check itself failed — genuinely offline. Try the locally-
      // synced guideline knowledge cache before giving up.
      return await _fallbackToOfflinePlan();
    } on DioException catch (e) {
      // Health check PASSED (backend is up) but the actual generation call
      // then failed. Two cases we treat the same way — fall back to the
      // offline protocol if one is cached, since a guideline-based answer
      // beats a technically-correct error either way:
      //   1. Timeout: care plan generation chains retrieval + rerank, an
      //      LLM call, and for HIGH/CRITICAL a second fact-check judge LLM
      //      call — on a slow connection or cold LLM provider this can
      //      genuinely exceed the client timeout even though the backend
      //      itself is healthy.
      //   2. 5xx server error: the backend responded but failed internally
      //      — e.g. it has no internet to reach Supabase/HuggingFace/the
      //      LLM providers even though it's reachable from this device on
      //      the local network (a real scenario during offline testing:
      //      the dev machine running the backend loses its own internet
      //      while staying reachable to the emulator/phone over LAN).
      final isNetworkIssue =
          e.type == DioExceptionType.receiveTimeout ||
          e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.sendTimeout ||
          e.type == DioExceptionType.connectionError ||
          e.type == DioExceptionType.badResponse;
      if (isNetworkIssue) {
        return await _fallbackToOfflinePlan(dioError: e);
      }
      rethrow;
    }
  }

  Future<ClinicalCarePlan> _fallbackToOfflinePlan({
    DioException? dioError,
  }) async {
    // Covers every configured guideline, including any local protocol
    // uploaded via Settings > Guideline Configuration once it has synced —
    // and now ALWAYS succeeds even before that first sync, via the
    // built-in rule-based protocol tier (see CuratedCarePlanRules). The
    // "no offline plan available" error path is effectively retired.
    return OfflineCarePlanBuilder.build(
      patient: widget.patient,
      result: widget.result,
      activeGuideline: widget.activeGuideline,
    );
  }

  /// Same join pattern as PatientTimelineScreen: clinical_assessments +
  /// risk_results by encounter_id, chronological (oldest first), excluding
  /// nothing — the backend only uses the last 5 for its trend narrative but
  /// we compute the UI trend chip from the full list deterministically.
  Future<List<Map<String, dynamic>>> _fetchPreviousAssessments() async {
    if (!SupabaseConfig.isConfigured || !AuthService.instance.isSignedIn) {
      return [];
    }
    try {
      final client = AuthService.instance.client;
      final assessmentsRows = await client
          .from('clinical_assessments')
          .select('id, created_at')
          .eq('encounter_id', widget.patient.id)
          .order('created_at', ascending: true);

      final riskRows = await client
          .from('risk_results')
          .select('assessment_id, combined_score, category, created_at')
          .eq('encounter_id', widget.patient.id);

      final Map<String, Map<String, dynamic>> riskByAssessment = {};
      for (final r in riskRows) {
        final aId = r['assessment_id'] as String?;
        if (aId != null) riskByAssessment[aId] = r;
      }

      final items = <Map<String, dynamic>>[];
      for (final row in assessmentsRows) {
        final aId = row['id'] as String;
        final risk = riskByAssessment[aId];
        if (risk == null) continue;
        items.add({
          'created_at': row['created_at'],
          'combined_score': (risk['combined_score'] as num?)?.toInt() ?? 0,
          'category': risk['category'] as String? ?? 'LOW',
        });
      }
      return items;
    } catch (_) {
      return [];
    }
  }

  _Trend _computeTrend(List<Map<String, dynamic>> previous, int currentScore) {
    if (previous.isEmpty) return _Trend.none;
    final lastScore =
        (previous.last['combined_score'] as num?)?.toInt() ?? currentScore;
    final delta = currentScore - lastScore;
    if (delta.abs() <= 1) return _Trend.stable;
    return delta > 0 ? _Trend.deteriorating : _Trend.improving;
  }

  void _retry() => setState(() {
    _future = _loadAndGenerate();
  });

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final body = FutureBuilder<ClinicalCarePlan>(
      future: _future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return _buildLoading();
        }
        if (snapshot.hasError || !snapshot.hasData) {
          return _buildError(snapshot.error);
        }
        return _buildContent(snapshot.data!);
      },
    );

    if (widget.embeddedInTab) return body;

    return Scaffold(
      appBar: AppBar(
        title: const Text('AI Care Plan'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Regenerate',
            onPressed: _retry,
          ),
        ],
      ),
      body: body,
    );
  }

  Widget _buildLoading() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const CircularProgressIndicator(),
          const SizedBox(height: 18),
          Text(
            'Building clinical care plan…',
            style: TextStyle(color: Colors.grey.shade600),
          ),
          const SizedBox(height: 6),
          Text(
            'Reviewing history · Checking cache · Querying ${widget.activeGuideline} evidence',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade400),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  Widget _buildError(Object? error) {
    final isUnreachable = error is BackendUnreachableException;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isUnreachable ? Icons.cloud_off : Icons.error_outline,
              size: 52,
              color: isUnreachable ? Colors.orange : Colors.red,
            ),
            const SizedBox(height: 14),
            Text(
              isUnreachable
                  ? 'Backend unreachable'
                  : 'Could not generate care plan',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 10),
            if (isUnreachable) ...[
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.orange.shade50,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.orange.shade200),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'The NeoGuard backend could not be reached at:',
                      style: TextStyle(
                        fontSize: 13,
                        color: Colors.grey.shade700,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      (error as BackendUnreachableException).backendUrl,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'Is the backend running?  Make sure:',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      '1. Run:  cd backend && uvicorn main:app --host 0.0.0.0 --port 8000',
                      style: TextStyle(fontSize: 12, fontFamily: 'monospace'),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      '2. For a physical device, pass your machine\'s LAN IP:',
                      style: TextStyle(fontSize: 12),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      '   flutter run -d <id> --dart-define=API_BASE_URL=http://192.168.x.x:8000',
                      style: TextStyle(fontSize: 11, fontFamily: 'monospace'),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      '   Find your LAN IP: ipconfig (Windows) | ifconfig (Mac/Linux)',
                      style: TextStyle(fontSize: 11, color: Colors.grey),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.grey.shade100,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.grey.shade300),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.info_outline,
                      size: 16,
                      color: Colors.grey.shade600,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'The app normally shows a built-in offline protocol here automatically — '
                        'seeing this error instead means something unexpected went wrong generating '
                        'even that. Try again, or restart the app if this persists.',
                        style: TextStyle(
                          fontSize: 12,
                          color: Colors.grey.shade700,
                          height: 1.5,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ] else
              Text(
                error?.toString() ?? 'Unknown error',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
              ),
            const SizedBox(height: 18),
            ElevatedButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('Retry'),
              onPressed: _retry,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildContent(ClinicalCarePlan plan) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 36),
      children: [
        const SizedBox(height: 10),
        _SourceBanner(plan: plan),
        FactCheckBadge(factCheck: plan.factCheck),
        const SizedBox(height: 12),

        // ── A. Patient Development Summary ────────────────────────────────
        _LetteredSection(
          letter: 'A',
          title: 'Patient Development Summary',
          icon: Icons.timeline_outlined,
          accentColor: const Color(0xFF1A56DB),
          children: [
            if (_previousAssessments.isNotEmpty) ...[
              _TrendChip(trend: _trend, count: _previousAssessments.length),
              const SizedBox(height: 12),
            ],
            Text(
              plan.clinicalSummary,
              style: const TextStyle(fontSize: 14, height: 1.7),
            ),
            if (plan.trendNarrative.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(
                plan.trendNarrative,
                style: TextStyle(
                  fontSize: 13,
                  height: 1.6,
                  fontStyle: FontStyle.italic,
                  color: Colors.grey.shade700,
                ),
              ),
            ],
            if (!plan.isSimulated) ...[
              const SizedBox(height: 12),
              _AiDisclaimerChip(),
            ],
          ],
        ),

        if (plan.riskAnalysis.isNotEmpty)
          _LetteredSection(
            letter: null,
            title: 'Risk Analysis',
            icon: Icons.analytics_outlined,
            accentColor: const Color(0xFF7C3AED),
            children: [
              Text(
                plan.riskAnalysis,
                style: const TextStyle(fontSize: 14, height: 1.7),
              ),
            ],
          ),

        // ── B. Immediate Actions ─────────────────────────────────────────
        _LetteredSection(
          letter: 'B',
          title: 'Immediate Actions',
          icon: Icons.bolt,
          accentColor: const Color(0xFF059669),
          headerBadge: 'Within 60 min',
          children: [
            if (plan.recommendedActions.isEmpty)
              Text(
                'No immediate actions identified.',
                style: TextStyle(color: Colors.grey.shade600),
              )
            else
              ...plan.recommendedActions.asMap().entries.map(
                (e) => _ActionItem(index: e.key + 1, text: e.value),
              ),
          ],
        ),

        // ── C. Antibiotic Plan ────────────────────────────────────────────
        _AntibioticPlanCard(plan: plan.antibioticPlan),

        // ── D. Monitoring Schedule (hard-coded — never LLM output) ───────
        const _MonitoringScheduleCard(),
        if (plan.monitoringPlan.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Text(
              plan.monitoringPlan,
              style: TextStyle(
                fontSize: 13,
                height: 1.6,
                color: Colors.grey.shade700,
              ),
            ),
          ),

        // ── E. Escalation Criteria (hard-coded — never LLM output) ───────
        const _EscalationCriteriaCard(),
        if (plan.escalationCriteria.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Text(
              plan.escalationCriteria,
              style: TextStyle(
                fontSize: 13,
                height: 1.6,
                color: Colors.grey.shade700,
              ),
            ),
          ),

        // ── F. Driver Breakdown ────────────────────────────────────────────
        if (plan.driverBreakdown.isNotEmpty)
          _LetteredSection(
            letter: 'F',
            title: 'Driver Breakdown',
            icon: Icons.radar,
            accentColor: const Color(0xFFD97706),
            children: [
              Text(
                plan.driverBreakdown,
                style: const TextStyle(fontSize: 14, height: 1.7),
              ),
            ],
          ),

        // ── G. Guideline Sources ──────────────────────────────────────────
        if (plan.guidelineCitations.isNotEmpty)
          _LetteredSection(
            letter: 'G',
            title: 'Guideline Sources',
            icon: Icons.menu_book_outlined,
            accentColor: Colors.grey.shade600,
            children: [
              ...plan.guidelineCitations.asMap().entries.map(
                (e) => _CitationRow(index: e.key + 1, text: e.value),
              ),
            ],
          ),

        _SafetyDisclaimer(isSimulated: plan.isSimulated),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Trend chip — computed deterministically in Dart, correct even offline
// ─────────────────────────────────────────────────────────────────────────────

class _TrendChip extends StatelessWidget {
  final _Trend trend;
  final int count;
  const _TrendChip({required this.trend, required this.count});

  @override
  Widget build(BuildContext context) {
    late final IconData icon;
    late final Color color;
    late final String label;
    switch (trend) {
      case _Trend.improving:
        icon = Icons.trending_down;
        color = WhoTheme.riskLow;
        label = 'Improving vs last assessment';
        break;
      case _Trend.deteriorating:
        icon = Icons.trending_up;
        color = WhoTheme.riskCritical;
        label = 'Deteriorating vs last assessment';
        break;
      case _Trend.stable:
        icon = Icons.trending_flat;
        color = WhoTheme.riskIntermediate;
        label = 'Stable vs last assessment';
        break;
      case _Trend.none:
        return const SizedBox.shrink();
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 6,
        runSpacing: 4,
        children: [
          Icon(icon, size: 14, color: color),
          Text(
            '$label · $count prior assessment${count == 1 ? '' : 's'}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Source banner
// ─────────────────────────────────────────────────────────────────────────────

class _SourceBanner extends StatelessWidget {
  final ClinicalCarePlan plan;
  const _SourceBanner({required this.plan});

  @override
  Widget build(BuildContext context) {
    if (plan.isSimulated) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.grey.shade100,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: Colors.grey.shade400),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.rule, size: 18, color: Colors.grey.shade600),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Protocol-Based Plan',
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                      color: Colors.grey.shade800,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    plan.sourceLabel.isNotEmpty
                        ? plan.sourceLabel
                        : 'Generated from EOSCAL score and guideline rules only — no AI language model was used.',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
    }

    final isCached = plan.sourceLabel.toLowerCase().contains('cached');
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFFEEF4FF),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFBFD3FF)),
      ),
      child: Row(
        children: [
          Icon(
            isCached ? Icons.cached : Icons.auto_awesome,
            size: 16,
            color: const Color(0xFF1A56DB),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              plan.sourceLabel.isNotEmpty
                  ? plan.sourceLabel
                  : 'AI-generated · clinical care plan',
              style: const TextStyle(fontSize: 12, color: Color(0xFF1A56DB)),
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Lettered section card (A, B, F, G, ...)
// ─────────────────────────────────────────────────────────────────────────────

class _LetteredSection extends StatelessWidget {
  final String? letter;
  final String title;
  final IconData icon;
  final Color accentColor;
  final String? headerBadge;
  final List<Widget> children;

  const _LetteredSection({
    required this.letter,
    required this.title,
    required this.icon,
    required this.accentColor,
    this.headerBadge,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                if (letter != null) ...[
                  Container(
                    width: 24,
                    height: 24,
                    decoration: BoxDecoration(
                      color: accentColor,
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      letter!,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                ] else ...[
                  Icon(icon, size: 18, color: accentColor),
                  const SizedBox(width: 8),
                ],
                Expanded(
                  child: Text(
                    title,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 15,
                      color: accentColor,
                    ),
                  ),
                ),
                if (headerBadge != null)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: accentColor,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      headerBadge!,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
              ],
            ),
            const Divider(height: 20),
            ...children,
          ],
        ),
      ),
    );
  }
}

class _ActionItem extends StatelessWidget {
  final int index;
  final String text;
  const _ActionItem({required this.index, required this.text});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 26,
            height: 26,
            margin: const EdgeInsets.only(right: 10, top: 1),
            decoration: const BoxDecoration(
              color: Color(0xFF059669),
              shape: BoxShape.circle,
            ),
            child: Center(
              child: Text(
                '$index',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(fontSize: 14, height: 1.55),
            ),
          ),
        ],
      ),
    );
  }
}

class _CitationRow extends StatelessWidget {
  final int index;
  final String text;
  const _CitationRow({required this.index, required this.text});

  String? _extractUrl(String t) {
    final m = RegExp(r'https?://\S+').firstMatch(t);
    return m?.group(0);
  }

  @override
  Widget build(BuildContext context) {
    final url = _extractUrl(text);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$index.',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              color: Colors.grey.shade500,
              fontSize: 13,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  url != null ? text.replaceAll(url, '').trim() : text,
                  style: const TextStyle(fontSize: 13, height: 1.5),
                ),
                if (url != null)
                  GestureDetector(
                    onTap: () async {
                      await launchUrl(
                        Uri.parse(url),
                        mode: LaunchMode.externalApplication,
                      );
                    },
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Flexible(
                          child: Text(
                            url,
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
                          size: 12,
                          color: Color(0xFF1A56DB),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            icon: const Icon(Icons.copy, size: 14),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: text));
              ScaffoldMessenger.of(
                context,
              ).showSnackBar(const SnackBar(content: Text('Citation copied')));
            },
          ),
        ],
      ),
    );
  }
}

class _AiDisclaimerChip extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.amber.shade50,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.amber.shade200),
      ),
      child: Row(
        children: [
          Icon(Icons.info_outline, size: 14, color: Colors.amber.shade700),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              'AI-generated. Clinical judgement of the responsible clinician supersedes this output.',
              style: TextStyle(
                fontSize: 11,
                fontStyle: FontStyle.italic,
                color: Colors.amber.shade900,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// C. Antibiotic plan — 3 explicit conditional branches ALWAYS shown,
// regardless of what the LLM (or rule-based fallback) returned. The plan's
// own required/urgency/regimen/duration fields populate the summary strip;
// the three branches below are static guidance from NICE NG195 §4 /
// AAP 2023 / WHO, present so a generalist doctor always has clear next steps
// whether labs come back negative, positive, or aren't back yet.
// ─────────────────────────────────────────────────────────────────────────────

class _AntibioticPlanCard extends StatelessWidget {
  final AntibioticPlan plan;
  const _AntibioticPlanCard({required this.plan});

  @override
  Widget build(BuildContext context) {
    final notRequired = !plan.required;
    final bgColor = notRequired
        ? Colors.green.shade50
        : const Color(0xFFFFF7ED);
    final borderColor = notRequired
        ? Colors.green.shade200
        : Colors.orange.shade300;
    final headerColor = notRequired
        ? Colors.green.shade700
        : Colors.orange.shade800;

    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: borderColor),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              color: bgColor,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(12),
              ),
            ),
            child: Row(
              children: [
                Container(
                  width: 24,
                  height: 24,
                  decoration: BoxDecoration(
                    color: headerColor,
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: const Text(
                    'C',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  'Antibiotic Plan',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 15,
                    color: headerColor,
                  ),
                ),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 3,
                  ),
                  decoration: BoxDecoration(
                    color: headerColor,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    notRequired ? 'Not indicated' : plan.urgency,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (plan.required) ...[
                  if (plan.regimen.isNotEmpty) ...[
                    _fieldLabel('Regimen'),
                    const SizedBox(height: 6),
                    ...plan.regimen.map(
                      (r) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.circle,
                              size: 6,
                              color: Color(0xFFD97706),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                r,
                                style: const TextStyle(
                                  fontSize: 14,
                                  height: 1.5,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
                  if (plan.duration.isNotEmpty && plan.duration != 'N/A') ...[
                    _fieldLabel('Duration'),
                    const SizedBox(height: 4),
                    Text(
                      plan.duration,
                      style: const TextStyle(fontSize: 14, height: 1.5),
                    ),
                    const SizedBox(height: 10),
                  ],
                ] else
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(
                      'Antibiotics are not currently indicated based on the EOSCAL assessment. '
                      'Continue enhanced observation and reassess if clinical signs emerge.',
                      style: const TextStyle(fontSize: 14, height: 1.6),
                    ),
                  ),
                const Divider(height: 24),
                _fieldLabel(
                  'If labs come back — always follow one of these three paths',
                ),
                const SizedBox(height: 10),
                _ConditionalBranch(
                  color: WhoTheme.riskLow,
                  title:
                      'Culture NEGATIVE at 36–48 h + CRP < 10 mg/L + clinically well',
                  action: 'STOP antibiotics.',
                ),
                const SizedBox(height: 8),
                _ConditionalBranch(
                  color: WhoTheme.riskCritical,
                  title: 'Culture POSITIVE',
                  action:
                      'CONTINUE for 7 days minimum; adjust to sensitivities.',
                ),
                const SizedBox(height: 8),
                _ConditionalBranch(
                  color: WhoTheme.riskIntermediate,
                  title:
                      'Labs UNAVAILABLE (culture/CRP cannot be obtained or resulted)',
                  action:
                      'Treat as INTERMEDIATE risk — continue empirical antibiotics and '
                      'arrange transfer to a facility with lab capability within 6 hours.',
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _fieldLabel(String text) => Text(
    text,
    style: const TextStyle(
      fontWeight: FontWeight.w600,
      fontSize: 13,
      color: Color(0xFF374151),
    ),
  );
}

class _ConditionalBranch extends StatelessWidget {
  final Color color;
  final String title;
  final String action;
  const _ConditionalBranch({
    required this.color,
    required this.title,
    required this.action,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(8),
        border: Border(left: BorderSide(color: color, width: 4)),
      ),
      child: RichText(
        text: TextSpan(
          style: const TextStyle(
            fontSize: 13,
            height: 1.5,
            color: Color(0xFF1A1A2E),
          ),
          children: [
            TextSpan(
              text: 'If $title\n',
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            TextSpan(
              text: '→ $action',
              style: TextStyle(color: color, fontWeight: FontWeight.bold),
            ),
          ],
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// D. Monitoring Schedule — hard-coded from NICE NG195 Table 2 / NHS GGC.
// Deliberately clock-based and explicit ("Every 1-4 h", "At H+6") so a
// non-neonatologist can follow it without additional training. Never
// generated by the LLM.
// ─────────────────────────────────────────────────────────────────────────────

class _MonitoringScheduleCard extends StatelessWidget {
  const _MonitoringScheduleCard();

  static const _rows = [
    (
      'Every 1–4 h (CRITICAL/HIGH)',
      'HR, RR, SpO2, temp, perfusion, consciousness',
    ),
    ('At H+6', 'Repeat clinical assessment. Escalate if worse.'),
    ('At H+18–24', 'CRP, FBC if not done. Review blood culture status.'),
    ('At H+36–48', 'Decision point: stop or continue antibiotics.'),
    (
      'If labs unavailable',
      'Clinical reassessment every 4 h. Transfer for labs within 6 h.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 24,
                  height: 24,
                  decoration: const BoxDecoration(
                    color: Color(0xFF0891B2),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: const Text(
                    'D',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                const Text(
                  'Monitoring Schedule',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 15,
                    color: Color(0xFF0891B2),
                  ),
                ),
                const Spacer(),
                Text(
                  'NICE NG195',
                  style: TextStyle(fontSize: 10, color: Colors.grey.shade500),
                ),
              ],
            ),
            const Divider(height: 20),
            ..._rows.map(
              (r) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      r.$1,
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      r.$2,
                      style: const TextStyle(fontSize: 13, height: 1.5),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// E. Escalation Criteria — hard-coded from NICE NG195. Each trigger written
// as "If X → do Y immediately" so no judgement call is needed at the
// bedside. Never generated by the LLM.
// ─────────────────────────────────────────────────────────────────────────────

class _EscalationCriteriaCard extends StatelessWidget {
  const _EscalationCriteriaCard();

  static const _triggers = [
    'RR > 70/min or increasing O2 need → escalate to senior neonatologist NOW',
    'Capillary refill > 3 s or HR > 180 → suspect septic shock, call resuscitation team',
    'Positive blood culture → do not wait for ward round, call attending immediately',
    'CRP > 10 at 18–24 h AND culture negative → senior review, consider continuation of antibiotics',
    'Temperature < 36.0°C or > 38.5°C persistent → escalate',
  ];

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.red.shade200),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 24,
                  height: 24,
                  decoration: const BoxDecoration(
                    color: Color(0xFFDC2626),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: const Text(
                    'E',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                const Text(
                  'Escalation Criteria',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    fontSize: 15,
                    color: Color(0xFFDC2626),
                  ),
                ),
              ],
            ),
            const Divider(height: 20),
            ..._triggers.map(
              (t) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: Colors.red.shade50,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.red.shade200),
                  ),
                  child: Text(
                    t,
                    style: const TextStyle(fontSize: 13, height: 1.5),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Safety disclaimer
// ─────────────────────────────────────────────────────────────────────────────

class _SafetyDisclaimer extends StatelessWidget {
  final bool isSimulated;
  const _SafetyDisclaimer({required this.isSimulated});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.grey.shade50,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.shield_outlined, size: 18, color: Colors.grey.shade400),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              isSimulated
                  ? 'This is a protocol-based plan generated from EOSCAL score and guideline rules '
                        'only — no AI language model was used. Clinical judgement of the responsible '
                        'clinician takes precedence.'
                  : 'This care plan was generated by an AI language model for educational support '
                        'only. Clinical decisions must be made by a qualified clinician based on the '
                        'full clinical picture. NeoGuard AI does not replace clinical judgement.',
              style: TextStyle(
                fontSize: 12,
                color: Colors.grey.shade600,
                height: 1.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
