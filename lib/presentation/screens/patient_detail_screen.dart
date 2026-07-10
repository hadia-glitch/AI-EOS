import 'package:flutter/material.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';
import '../patient_entry_flow.dart';
import 'care_plan_screen.dart';
import 'patient_timeline_screen.dart';
import 'risk_results_tab.dart';
import 'antibiotic_status_screen.dart';

/// Screen 06 — Patient Detail with 5 tabs
class PatientDetailScreen extends StatefulWidget {
  final PatientParameters patient;
  final String activeGuideline;

  const PatientDetailScreen({super.key, required this.patient, required this.activeGuideline});

  @override
  State<PatientDetailScreen> createState() => _PatientDetailScreenState();
}

class _PatientDetailScreenState extends State<PatientDetailScreen> with SingleTickerProviderStateMixin {
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 5, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final result = EoscalCalculator.calculate(widget.patient);
    final ageHours = DateTime.now().difference(widget.patient.birthDateTime).inHours;

    return Scaffold(
      appBar: AppBar(
        title: Text('Ref #${widget.patient.id}'),
        actions: [
          Container(
            margin: const EdgeInsets.only(right: 12),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(color: result.riskCategory.color, borderRadius: BorderRadius.circular(12)),
            child: Text(result.riskCategory.displayName, style: const TextStyle(color: Colors.white, fontSize: 11)),
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          isScrollable: true,
          indicatorColor: WhoTheme.secondaryTeal,
          tabs: const [
            Tab(text: 'Overview'),
            Tab(text: 'Timeline'),
            Tab(text: 'Risk'),
            Tab(text: 'Care Plan'),
            Tab(text: 'Antibiotics'),
          ],
        ),
      ),
      body: Column(
        children: [
          Card(
            margin: const EdgeInsets.all(8),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  _summaryCell('GA', '${widget.patient.gestationalAgeWeeks}w'),
                  _summaryCell('Age', '${ageHours}h'),
                  _summaryCell('Score', '${result.totalScore}'),
                  _summaryCell('Risk', result.riskCategory.displayName.split(' ').first),
                ],
              ),
            ),
          ),
          Expanded(
            child: TabBarView(
              controller: _tabController,
              children: [
                RiskResultsTab(patient: widget.patient, activeGuideline: widget.activeGuideline, compact: true),
                PatientTimelineScreen(patient: widget.patient, result: result),
                RiskResultsTab(patient: widget.patient, activeGuideline: widget.activeGuideline),
                CarePlanScreen(
                  patient: widget.patient,
                  result: result,
                  activeGuideline: widget.activeGuideline,
                  embeddedInTab: true,
                ),
                AntibioticStatusScreen(patient: widget.patient, activeGuideline: widget.activeGuideline),
              ],
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => PatientEntryFlow(existingPatient: widget.patient)),
        ),
        backgroundColor: WhoTheme.riskHigh,
        icon: const Icon(Icons.add),
        label: const Text('Add Update'),
      ),
    );
  }

  Widget _summaryCell(String label, String value) {
    return Column(
      children: [
        Text(label, style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
        Text(value, style: const TextStyle(fontWeight: FontWeight.bold)),
      ],
    );
  }
}