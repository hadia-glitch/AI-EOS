import 'package:flutter/material.dart';
import '../../data/gemini_service.dart';
import '../theme.dart';

/// Shows the result of the backend's Phase-3 fact-checking judge pass.
///
/// Renders nothing when the judge didn't run (`performed == false`) — that
/// happens for LOW/INTERMEDIATE risk (checked only for HIGH/CRITICAL, see
/// backend config.fact_check_categories) or when no LLM provider was
/// available. Absence of the badge should read as "not applicable", not as
/// a silent failure — the surrounding source banner already communicates
/// AI vs rule-based vs cached.
class FactCheckBadge extends StatelessWidget {
  final FactCheckInfo factCheck;
  const FactCheckBadge({super.key, required this.factCheck});

  @override
  Widget build(BuildContext context) {
    if (!factCheck.performed) return const SizedBox.shrink();

    if (factCheck.hasSeriousFlag) {
      return _buildBanner(
        icon: Icons.warning_amber_rounded,
        iconColor: WhoTheme.riskCritical,
        bgColor: WhoTheme.riskCritical.withValues(alpha: 0.1),
        borderColor: WhoTheme.riskCritical.withValues(alpha: 0.4),
        title: 'Fact-check flagged this output',
        subtitle: factCheck.flaggedClaims.isNotEmpty
            ? 'Unsupported claim: ${factCheck.flaggedClaims.first}'
                '${factCheck.flaggedClaims.length > 1 ? ' (+${factCheck.flaggedClaims.length - 1} more)' : ''}'
            : 'One or more claims could not be verified against the retrieved '
                'guideline evidence. Review before acting.',
        titleColor: WhoTheme.riskCritical,
      );
    }

    final hasMinorFlags = factCheck.flaggedClaims.isNotEmpty;
    return _buildBanner(
      icon: hasMinorFlags ? Icons.fact_check_outlined : Icons.verified_outlined,
      iconColor: hasMinorFlags ? WhoTheme.riskIntermediate : const Color(0xFF1A8A5F),
      bgColor: hasMinorFlags
          ? WhoTheme.riskIntermediate.withValues(alpha: 0.1)
          : const Color(0xFFE7F7EF),
      borderColor: hasMinorFlags
          ? WhoTheme.riskIntermediate.withValues(alpha: 0.4)
          : const Color(0xFFB7E4CC),
      title: hasMinorFlags
          ? 'Fact-checked · minor notes'
          : 'Fact-checked against retrieved guideline evidence',
      subtitle: hasMinorFlags
          ? '${factCheck.flaggedClaims.length} minor unsupported phrase'
              '${factCheck.flaggedClaims.length == 1 ? '' : 's'} noted — clinical '
              'recommendation unaffected.'
          : 'Confidence ${(factCheck.confidence * 100).round()}%'
              '${factCheck.notes.isNotEmpty ? ' — ${factCheck.notes}' : ''}',
      titleColor: hasMinorFlags ? WhoTheme.riskIntermediate : const Color(0xFF1A8A5F),
    );
  }

  Widget _buildBanner({
    required IconData icon,
    required Color iconColor,
    required Color bgColor,
    required Color borderColor,
    required String title,
    required String subtitle,
    required Color titleColor,
  }) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: iconColor),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: titleColor)),
                const SizedBox(height: 2),
                Text(subtitle, style: TextStyle(fontSize: 12, color: titleColor.withValues(alpha: 0.85))),
              ],
            ),
          ),
        ],
      ),
    );
  }
}