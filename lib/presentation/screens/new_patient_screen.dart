import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';
import '../patient_entry_flow.dart';

/// Screen 10 — New Patient Setup
class NewPatientScreen extends StatefulWidget {
  const NewPatientScreen({super.key});

  @override
  State<NewPatientScreen> createState() => _NewPatientScreenState();
}

class _NewPatientScreenState extends State<NewPatientScreen> {
  final _refController = TextEditingController(text: const Uuid().v4().substring(0, 8).toUpperCase());
  DateTime _birthDateTime = DateTime.now();
  int _gaWeeks = 38;
  int _gaDays = 0;
  int _birthWeight = 3200;

  double get _gaTotal => _gaWeeks + _gaDays / 7.0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New Patient')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  TextField(controller: _refController, decoration: const InputDecoration(labelText: 'Patient Reference')),
                  ListTile(
                    title: const Text('Date & Time of Birth'),
                    subtitle: Text(_birthDateTime.toString().substring(0, 16)),
                    trailing: IconButton(
                      icon: const Icon(Icons.calendar_today),
                      onPressed: () async {
                        final d = await showDatePicker(context: context, firstDate: DateTime.now().subtract(const Duration(days: 7)), lastDate: DateTime.now(), initialDate: _birthDateTime);
                        if (d != null) setState(() => _birthDateTime = d);
                      },
                    ),
                  ),
                  Row(
                    children: [
                      Expanded(
                        child: Row(
                          children: [
                            IconButton(onPressed: () => setState(() => _gaWeeks = (_gaWeeks - 1).clamp(22, 43)), icon: const Icon(Icons.remove)),
                            Text('$_gaWeeks w'),
                            IconButton(onPressed: () => setState(() => _gaWeeks = (_gaWeeks + 1).clamp(22, 43)), icon: const Icon(Icons.add)),
                          ],
                        ),
                      ),
                      Expanded(
                        child: Row(
                          children: [
                            IconButton(onPressed: () => setState(() => _gaDays = (_gaDays - 1).clamp(0, 6)), icon: const Icon(Icons.remove)),
                            Text('$_gaDays d'),
                            IconButton(onPressed: () => setState(() => _gaDays = (_gaDays + 1).clamp(0, 6)), icon: const Icon(Icons.add)),
                          ],
                        ),
                      ),
                    ],
                  ),
                  TextField(
                    decoration: const InputDecoration(labelText: 'Birth Weight (grams)'),
                    keyboardType: TextInputType.number,
                    onChanged: (v) => _birthWeight = int.tryParse(v) ?? _birthWeight,
                  ),
                  if (_gaTotal < 35)
                    Container(
                      margin: const EdgeInsets.only(top: 12),
                      padding: const EdgeInsets.all(12),
                      color: WhoTheme.riskIntermediate.withValues(alpha: 0.2),
                      child: const Text('EOSCAL 2024 validated for ≥35 weeks. Outputs flagged as unvalidated below this threshold.'),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          ElevatedButton(
            onPressed: () {
              final patient = PatientParameters(
                id: _refController.text,
                name: 'Patient ${_refController.text}',
                mrn: 'MRN-${_refController.text}',
                gestationalAgeWeeks: _gaTotal,
                birthDateTime: _birthDateTime,
                maternalTemperature: 37.0,
                romHours: 4,
                gbsPositive: false,
                adequateIntrapartumAntibiotics: false,
                clinicalChorioamnionitis: false,
                deliveryMode: 'Vaginal',
                respiratoryDistress: 'None',
                oxygenNeed: 'None',
                apgar5Min: 9,
                poorPerfusion: false,
                neonatalTemperature: 36.8,
                neurologicalStatus: 'Normal',
              );
              Navigator.pushReplacement(
                context,
                MaterialPageRoute(builder: (_) => PatientEntryFlow(existingPatient: patient)),
              );
            },
            child: const Text('Create Patient & Begin Assessment'),
          ),
        ],
      ),
    );
  }
}
