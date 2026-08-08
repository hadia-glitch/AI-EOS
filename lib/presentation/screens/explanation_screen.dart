import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../core/theme.dart';
import '../../core/widgets/evidence_citation.dart';
import '../../core/widgets/offline_banner.dart';
import '../../core/widgets/fact_check_badge.dart';
import '../../core/widgets/source_badge.dart';
import '../../data/api/explanation_api.dart';
import '../../data/auth_service.dart';
import '../../data/gemini_service.dart';
import '../../data/guidelines_data.dart';
import '../../data/models/rag_chunk.dart';
import '../../data/supabase_config.dart';
import '../../domain/eoscal_calculator.dart';
import 'guideline_pdf_viewer_screen.dart';

/// Screen 15 — Clinical Explanation
///
/// Source of explanation in priority order:
///   1. Backend /api/v1/encounters/{id}/explanation
///      → uses backend .env GEMINI_API_KEY
///      → checks llm_explanations cache first
///      → writes result to llm_explanations
///   2. Local rule-based fallback (backend unreachable)
///      → deterministic protocol-based output
///      → NEVER labelled as AI
class ExplanationScreen extends ConsumerStatefulWidget {
  final PatientParameters patient;
  final EoscalResult result;
  final String activeGuideline;
  final List<Map<String, dynamic>> previousAssessments;

  const ExplanationScreen({
    super.key,
    required this.patient,
    required this.result,
    required this.activeGuideline,
    this.previousAssessments = const [],
  });

  @override
  ConsumerState<ExplanationScreen> createState() => _ExplanationScreenState();
}

class _ExplanationScreenState extends ConsumerState<ExplanationScreen> {
  late Future<StructuredExplanation> _future;

  @override
  void initState() {
    super.initState();
    _future = _generate();
  }

  Future<StructuredExplanation> _generate() async {
    // Fetch risk_result id from Supabase for linking to llm_explanations
    String? riskResultId;
    if (SupabaseConfig.isConfigured && AuthService.instance.isSignedIn) {
      try {
        final rows = await Supabase.instance.client
            .from('risk_results')
            .select('id')
            .eq('encounter_id', widget.patient.id)
            .order('created_at', ascending: false)
            .limit(1);
        final list = rows as List<dynamic>;
        if (list.isNotEmpty) {
          riskResultId =
              (list.first as Map<String, dynamic>)['id'] as String?;
        }
      } catch (_) {}
    }

    // Backend holds the Gemini key — throws BackendUnreachableException if down.
    // We catch that here and surface a clear error with the IP fix hint.
    try {
      return await explanationApi.generate(
        encounterId: riskResultId ?? widget.patient.id,
        patient: widget.patient,
        result: widget.result,
        activeGuideline: widget.activeGuideline,
        previousAssessments: widget.previousAssessments,
      );
    } on BackendUnreachableException {
      // Re-throw so FutureBuilder shows the targeted error widget.
      rethrow;
    } catch (e) {
      rethrow;
    }
  }

  void _retry() => setState(() { _future = _generate(); });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinical Explanation'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Regenerate',
            onPressed: _retry,
          ),
        ],
      ),
      body: FutureBuilder<StructuredExplanation>(
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
      ),
    );
  }

  Widget _buildLoading() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const CircularProgressIndicator(),
          const SizedBox(height: 18),
          Text('Generating clinical explanation…',
              style: TextStyle(color: Colors.grey.shade600)),
          const SizedBox(height: 6),
          Text(
            'Checking cache · Querying ${widget.activeGuideline} evidence · Calling backend',
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
              isUnreachable ? 'Backend unreachable' : 'Could not generate explanation',
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
                      style: TextStyle(fontSize: 13, color: Colors.grey.shade700),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      (error as BackendUnreachableException).backendUrl,
                      style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 13,
                          fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'Is the backend running?  Make sure:',
                      style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 6),
                    const Text('1. Run:  cd backend && uvicorn main:app --host 0.0.0.0 --port 8000',
                        style: TextStyle(fontSize: 12, fontFamily: 'monospace')),
                    const SizedBox(height: 4),
                    const Text('2. For a physical device, pass your machine\'s LAN IP:',
                        style: TextStyle(fontSize: 12)),
                    const SizedBox(height: 4),
                    const Text('   flutter run -d <id> --dart-define=API_BASE_URL=http://192.168.x.x:8000',
                        style: TextStyle(fontSize: 11, fontFamily: 'monospace')),
                    const SizedBox(height: 4),
                    const Text(
                        '   Find your LAN IP: ipconfig (Windows) | ifconfig (Mac/Linux)',
                        style: TextStyle(fontSize: 11, color: Colors.grey)),
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

  Widget _buildContent(StructuredExplanation exp) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 36),
      children: [
        const SizedBox(height: 10),

        // ── Source banner — ALWAYS honest about what generated this ──────────
        _SourceBanner(exp: exp),
        FactCheckBadge(
          factCheck: exp.factCheck,
          evidenceLabels: exp.evidenceLabelMap,
        ),

        const SizedBox(height: 12),

        // ── 1. Clinical Summary ───────────────────────────────────────────────
        _SectionCard(
          title: 'Clinical Summary',
          icon: Icons.summarize_outlined,
          accentColor: const Color(0xFF1A56DB),
          children: [
            EvidenceCitationText(
              text: exp.clinicalSummary,
              labels: exp.evidenceLabelMap,
              style: const TextStyle(fontSize: 14, height: 1.7),
            ),
            if (!exp.isSimulated) ...[
              const SizedBox(height: 12),
              _AiDisclaimerChip(),
            ],
          ],
        ),

        // ── 2. Risk Analysis ─────────────────────────────────────────────────
        if (exp.riskAnalysis.isNotEmpty)
          _SectionCard(
            title: 'Risk Analysis',
            icon: Icons.analytics_outlined,
            accentColor: const Color(0xFF7C3AED),
            children: [
              EvidenceCitationText(
                text: exp.riskAnalysis,
                labels: exp.evidenceLabelMap,
                style: const TextStyle(fontSize: 14, height: 1.7),
              ),
            ],
          ),

        // ── 3. Driver Breakdown ──────────────────────────────────────────────
        if (exp.driverBreakdown.isNotEmpty)
          _SectionCard(
            title: 'Risk Driver Analysis',
            icon: Icons.radar,
            accentColor: const Color(0xFFD97706),
            children: [
              EvidenceCitationText(
                text: exp.driverBreakdown,
                labels: exp.evidenceLabelMap,
                style: const TextStyle(fontSize: 14, height: 1.7),
              ),
            ],
          ),

        // ── 4. Antibiotic Plan ───────────────────────────────────────────────
        _AntibioticPlanCard(plan: exp.antibioticPlan),

        // ── 5. Recommended Actions ───────────────────────────────────────────
        if (exp.recommendedActions.isNotEmpty)
          _SectionCard(
            title: 'Recommended Actions',
            icon: Icons.checklist_rounded,
            accentColor: const Color(0xFF059669),
            children: [
              ...exp.recommendedActions.asMap().entries.map(
                    (e) => _ActionItem(index: e.key + 1, text: e.value),
                  ),
            ],
          ),

        // ── 6. Monitoring Plan ───────────────────────────────────────────────
        if (exp.monitoringPlan.isNotEmpty)
          _SectionCard(
            title: 'Monitoring Plan',
            icon: Icons.monitor_heart_outlined,
            accentColor: const Color(0xFF0891B2),
            children: [
              EvidenceCitationText(
                text: exp.monitoringPlan,
                labels: exp.evidenceLabelMap,
                style: const TextStyle(fontSize: 14, height: 1.7),
              ),
            ],
          ),

        // ── 7. Escalation Criteria ───────────────────────────────────────────
        if (exp.escalationCriteria.isNotEmpty)
          _SectionCard(
            title: 'Escalation Criteria',
            icon: Icons.warning_amber_rounded,
            accentColor: const Color(0xFFDC2626),
            children: [
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.red.shade200),
                ),
                child: EvidenceCitationText(
                  text: exp.escalationCriteria,
                  labels: exp.evidenceLabelMap,
                  style: const TextStyle(fontSize: 14, height: 1.7),
                ),
              ),
            ],
          ),

        // ── 8. Guideline Sources ─────────────────────────────────────────────
        if (exp.guidelineCitations.isNotEmpty)
          _SectionCard(
            title: 'Guideline Sources',
            icon: Icons.menu_book_outlined,
            accentColor: Colors.grey.shade600,
            children: [
              ...exp.guidelineCitations.asMap().entries.map(
                    (e) => _CitationRow(index: e.key + 1, citation: e.value),
                  ),
            ],
          ),

        // ── Safety disclaimer ────────────────────────────────────────────────
        _SafetyDisclaimer(isSimulated: exp.isSimulated),
      ],
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Source banner — always tells user what generated this
// ─────────────────────────────────────────────────────────────────────────────

class _SourceBanner extends StatelessWidget {
  final StructuredExplanation exp;
  const _SourceBanner({required this.exp});

  @override
  Widget build(BuildContext context) {
    if (exp.isSimulated) {
      // Rule-based: grey, clearly not AI
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
                    'Rule-Based Protocol Summary',
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                        color: Colors.grey.shade800),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    exp.sourceLabel.isNotEmpty
                        ? exp.sourceLabel
                        : 'Generated from protocol rules only — no AI language model was used.',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
    }

    final isCached = exp.sourceLabel.toLowerCase().contains('cached');
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
              exp.sourceLabel.isNotEmpty
                  ? exp.sourceLabel
                  : 'AI-generated · clinical explanation',
              style: const TextStyle(fontSize: 12, color: Color(0xFF1A56DB)),
            ),
          ),
        ],
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Antibiotic plan card
// ─────────────────────────────────────────────────────────────────────────────

class _AntibioticPlanCard extends StatelessWidget {
  final AntibioticPlan plan;
  const _AntibioticPlanCard({required this.plan});

  @override
  Widget build(BuildContext context) {
    final notRequired = !plan.required;
    final bgColor = notRequired ? Colors.green.shade50 : const Color(0xFFFFF7ED);
    final borderColor =
        notRequired ? Colors.green.shade200 : Colors.orange.shade300;
    final headerColor =
        notRequired ? Colors.green.shade700 : Colors.orange.shade800;

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
              borderRadius:
                  const BorderRadius.vertical(top: Radius.circular(12)),
            ),
            child: Row(
              children: [
                Icon(
                  notRequired
                      ? Icons.check_circle
                      : Icons.medication_liquid_outlined,
                  color: headerColor,
                  size: 20,
                ),
                const SizedBox(width: 8),
                Text('Antibiotic Plan',
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 15,
                        color: headerColor)),
                const Spacer(),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                  decoration: BoxDecoration(
                    color: headerColor,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    notRequired ? 'Not indicated' : plan.urgency,
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 11,
                        fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: notRequired
                ? Text(
                    'Antibiotics are not currently indicated based on the EOSCAL '
                    'assessment. Continue enhanced observation and reassess if '
                    'clinical signs emerge.',
                    style: const TextStyle(fontSize: 14, height: 1.6),
                  )
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (plan.regimen.isNotEmpty) ...[
                        _fieldLabel('Regimen'),
                        const SizedBox(height: 6),
                        ...plan.regimen.map((r) => Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  const Icon(Icons.circle,
                                      size: 6, color: Color(0xFFD97706)),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Text(r,
                                        style: const TextStyle(
                                            fontSize: 14, height: 1.5)),
                                  ),
                                ],
                              ),
                            )),
                        const SizedBox(height: 8),
                      ],
                      _fieldLabel('Duration'),
                      const SizedBox(height: 4),
                      Text(plan.duration,
                          style: const TextStyle(fontSize: 14, height: 1.5)),
                      const SizedBox(height: 10),
                      _fieldLabel('Stop criteria'),
                      const SizedBox(height: 4),
                      Text(plan.stopCriteria,
                          style: const TextStyle(fontSize: 14, height: 1.5)),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Widget _fieldLabel(String text) => Text(text,
      style: const TextStyle(
          fontWeight: FontWeight.w600,
          fontSize: 13,
          color: Color(0xFF374151)));
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared widgets
// ─────────────────────────────────────────────────────────────────────────────

class _SectionCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final Color accentColor;
  final List<Widget> children;

  const _SectionCard({
    required this.title,
    required this.icon,
    required this.accentColor,
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
                Icon(icon, size: 18, color: accentColor),
                const SizedBox(width: 8),
                Text(title,
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 15,
                        color: accentColor)),
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
              child: Text('$index',
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 12,
                      fontWeight: FontWeight.bold)),
            ),
          ),
          Expanded(
              child:
                  Text(text, style: const TextStyle(fontSize: 14, height: 1.55))),
        ],
      ),
    );
  }
}

class _CitationRow extends StatelessWidget {
  final int index;
  final CitationItem citation;
  const _CitationRow({required this.index, required this.citation});

  String get _label {
    final source = citation.source.trim();
    final section = citation.section.trim();
    if (source.isNotEmpty && section.isNotEmpty) return '$source — $section';
    return source.isNotEmpty ? source : (section.isNotEmpty ? section : 'Guideline source');
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('$index.',
              style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Colors.grey.shade500,
                  fontSize: 13)),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _label,
                  style: const TextStyle(fontSize: 13, height: 1.5),
                ),
                if (citation.canOpenInApp)
                  GestureDetector(
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => GuidelinePdfViewerScreen(
                          fileName: citation.fileName,
                          documentName: citation.source.isNotEmpty
                              ? citation.source
                              : citation.fileName,
                          pageNumber: citation.pageNumber,
                          searchText: citation.section,
                        ),
                      ),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          citation.pageNumber != null
                              ? 'Open in PDF · p.${citation.pageNumber}'
                              : 'Open in PDF',
                          style: const TextStyle(
                              fontSize: 12, color: Color(0xFF1A56DB)),
                        ),
                        const SizedBox(width: 3),
                        const Icon(Icons.picture_as_pdf_outlined,
                            size: 12, color: Color(0xFF1A56DB)),
                      ],
                    ),
                  )
                else if (citation.documentUrl.isNotEmpty)
                  GestureDetector(
                    onTap: () async {
                      await launchUrl(Uri.parse(citation.documentUrl),
                          mode: LaunchMode.externalApplication);
                    },
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Flexible(
                          child: Text(citation.documentUrl,
                              style: const TextStyle(
                                  fontSize: 12, color: Color(0xFF1A56DB)),
                              overflow: TextOverflow.ellipsis),
                        ),
                        const SizedBox(width: 3),
                        const Icon(Icons.arrow_outward,
                            size: 12, color: Color(0xFF1A56DB)),
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
              Clipboard.setData(ClipboardData(text: _label));
              ScaffoldMessenger.of(context)
                  .showSnackBar(const SnackBar(content: Text('Citation copied')));
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
                  color: Colors.amber.shade900),
            ),
          ),
        ],
      ),
    );
  }
}

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
                  ? 'This is a rule-based protocol summary — no AI language model was '
                      'used. Recommendations are derived deterministically from the '
                      'EOSCAL score and active guideline. Clinical decisions must be '
                      'made by a qualified clinician.'
                  : 'This explanation was generated by an AI language model for '
                      'educational support only. Clinical decisions must be made by a '
                      'qualified clinician based on the full clinical picture. '
                      'NeoGuard AI does not replace clinical judgement.',
              style: TextStyle(
                  fontSize: 12, color: Colors.grey.shade600, height: 1.5),
            ),
          ),
        ],
      ),
    );
  }
}