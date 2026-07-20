import 'package:flutter/material.dart';
import '../theme.dart';
import '../../data/local_draft_service.dart';

/// Shows a small banner when the signed-in-looking session is actually just
/// a local, not-yet-synced offline signup draft. Safe to drop into any
/// screen's build method — returns an empty widget once there's nothing to
/// show (i.e. once the draft has synced and been cleared).
///
/// Usage in home_dashboard.dart — add near the top of the body Column:
///   const OfflineDraftBanner(),
class OfflineDraftBanner extends StatelessWidget {
  const OfflineDraftBanner({super.key});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<bool>(
      future: LocalDraftService.instance.hasPendingDraft(),
      builder: (context, snapshot) {
        if (snapshot.data != true) return const SizedBox.shrink();
        return Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: WhoTheme.riskIntermediate.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(8),
          ),
          child: const Row(
            children: [
              Icon(Icons.cloud_off, size: 18, color: Colors.black87),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  "Working offline — your account will finish setting up automatically once you're back online.",
                  style: TextStyle(fontSize: 12),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}