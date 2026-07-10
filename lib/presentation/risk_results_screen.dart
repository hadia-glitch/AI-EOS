import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/theme.dart';
import '../domain/eoscal_calculator.dart';
import 'patient_state.dart';
import 'patient_entry_flow.dart';
import 'screens/risk_results_tab.dart';
import 'screens/care_plan_screen.dart';

class RiskResultsScreen extends ConsumerStatefulWidget {
  final PatientParameters patient;

  const RiskResultsScreen({super.key, required this.patient});

  @override
  ConsumerState<RiskResultsScreen> createState() => _RiskResultsScreenState();
}

class _RiskResultsScreenState extends ConsumerState<RiskResultsScreen> {
  @override
  Widget build(BuildContext context) {
    final patients = ref.watch(patientsProvider);
    final patient = patients.firstWhere((p) => p.id == widget.patient.id, orElse: () => widget.patient);
    final guideline = ref.watch(activeGuidelineProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('EOS Risk Assessment'),
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => Navigator.pop(context)),
        actions: [
          IconButton(
            icon: const Icon(Icons.edit_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => PatientEntryFlow(existingPatient: patient)),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline),
            onPressed: () async {
              final ok = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Delete patient?'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
                    TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
                  ],
                ),
              );
              if (ok == true && context.mounted) {
                await ref.read(patientsProvider.notifier).deletePatient(patient.id);
                Navigator.pop(context);
              }
            },
          ),
        ],
      ),
      body: RiskResultsTab(patient: patient, activeGuideline: guideline),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () {
          final result = EoscalCalculator.calculate(patient);
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => CarePlanScreen(patient: patient, result: result, activeGuideline: guideline),
            ),
          );
        },
        backgroundColor: WhoTheme.secondaryTeal,
        icon: const Icon(Icons.auto_awesome),
        label: const Text('AI Care Plan'),
      ),
    );
  }
}