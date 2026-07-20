import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:intl/intl.dart';
import '../core/theme.dart';
import '../domain/eoscal_calculator.dart';
import 'patient_state.dart';
import 'risk_results_screen.dart';

class PatientEntryFlow extends ConsumerStatefulWidget {
  final PatientParameters? existingPatient; // If editing an existing record

  const PatientEntryFlow({super.key, this.existingPatient});

  @override
  ConsumerState<PatientEntryFlow> createState() => _PatientEntryFlowState();
}

class _PatientEntryFlowState extends ConsumerState<PatientEntryFlow> {
  int _currentStep = 0;
  final _formKey = GlobalKey<FormState>();

  // --- Step 1 Form Data (Maternal & Baseline) ---
  final _nameController = TextEditingController();
  final _mrnController = TextEditingController();
  double _gestationalAge = 38.0;
  DateTime _birthDate = DateTime.now().subtract(const Duration(hours: 12));
  TimeOfDay _birthTime = TimeOfDay.fromDateTime(
    DateTime.now().subtract(const Duration(hours: 12)),
  );
  double _maternalTemp = 37.0;
  double _romHours = 0.0;
  bool _gbsPositive = false;
  bool _adequateAbx = false;
  bool _chorio = false;
  String _deliveryMode = 'Vaginal';

  // --- Step 2 Form Data (Neonatal Status) ---
  String _respiratoryDistress = 'None';
  String _oxygenNeed = 'None';
  int _apgar5Min = 9;
  bool _poorPerfusion = false;
  double _neonatalTemp = 36.8;
  String _neurologicalStatus = 'Normal';

  // --- Step 3 Form Data (Laboratory - Optional) ---
  final _wbcController = TextEditingController();
  final _itRatioController = TextEditingController();
  final _plateletsController = TextEditingController();
  final _crpController = TextEditingController();
  final _pctController = TextEditingController();
  bool _bloodCulturePositive = false;

  @override
  void initState() {
    super.initState();
    if (widget.existingPatient != null) {
      final p = widget.existingPatient!;
      _nameController.text = p.name;
      _mrnController.text = p.mrn;
      _gestationalAge = p.gestationalAgeWeeks;
      _birthDate = p.birthDateTime;
      _birthTime = TimeOfDay.fromDateTime(p.birthDateTime);
      _maternalTemp = p.maternalTemperature;
      _romHours = p.romHours;
      _gbsPositive = p.gbsPositive;
      _adequateAbx = p.adequateIntrapartumAntibiotics;
      _chorio = p.clinicalChorioamnionitis;
      _deliveryMode = p.deliveryMode;

      _respiratoryDistress = p.respiratoryDistress;
      _oxygenNeed = p.oxygenNeed;
      _apgar5Min = p.apgar5Min;
      _poorPerfusion = p.poorPerfusion;
      _neonatalTemp = p.neonatalTemperature;
      _neurologicalStatus = p.neurologicalStatus;

      if (p.wbcCount != null)
        _wbcController.text = p.wbcCount!.toStringAsFixed(0);
      if (p.itRatio != null) _itRatioController.text = p.itRatio!.toString();
      if (p.plateletCount != null)
        _plateletsController.text = p.plateletCount!.toStringAsFixed(0);
      if (p.crpLevel != null) _crpController.text = p.crpLevel!.toString();
      if (p.pctLevel != null) _pctController.text = p.pctLevel!.toString();
      _bloodCulturePositive = p.bloodCulturePositive ?? false;
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    _mrnController.dispose();
    _wbcController.dispose();
    _itRatioController.dispose();
    _plateletsController.dispose();
    _crpController.dispose();
    _pctController.dispose();
    super.dispose();
  }

  // Helper to compile current form state into PatientParameters object
  PatientParameters _buildCurrentPatientParameters() {
    final combinedBirthDateTime = DateTime(
      _birthDate.year,
      _birthDate.month,
      _birthDate.day,
      _birthTime.hour,
      _birthTime.minute,
    );

    return PatientParameters(
      id: widget.existingPatient?.id ?? const Uuid().v4(),
      name: _nameController.text.trim().isEmpty
          ? 'Draft Assessment'
          : _nameController.text.trim(),
      mrn: _mrnController.text.trim().isEmpty
          ? 'MRN-TEMP'
          : _mrnController.text.trim(),
      gestationalAgeWeeks: _gestationalAge,
      birthDateTime: combinedBirthDateTime,
      maternalTemperature: _maternalTemp,
      romHours: _romHours,
      gbsPositive: _gbsPositive,
      adequateIntrapartumAntibiotics: _adequateAbx,
      clinicalChorioamnionitis: _chorio,
      deliveryMode: _deliveryMode,
      respiratoryDistress: _respiratoryDistress,
      oxygenNeed: _oxygenNeed,
      apgar5Min: _apgar5Min,
      poorPerfusion: _poorPerfusion,
      neonatalTemperature: _neonatalTemp,
      neurologicalStatus: _neurologicalStatus,
      wbcCount: double.tryParse(_wbcController.text),
      itRatio: double.tryParse(_itRatioController.text),
      plateletCount: double.tryParse(_plateletsController.text),
      crpLevel: double.tryParse(_crpController.text),
      pctLevel: double.tryParse(_pctController.text),
      bloodCulturePositive: _bloodCulturePositive,
    );
  }

  void _submitForm() async {
    if (!_formKey.currentState!.validate()) return;

    final patient = _buildCurrentPatientParameters();
    final notifier = ref.read(patientsProvider.notifier);
    final alreadyExists = notifier.state.any((p) => p.id == patient.id);

    if (alreadyExists) {
      await notifier.updatePatient(patient);
    } else {
      await notifier.addPatient(patient);
    }

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Assessment saved successfully.')),
      );
      // Navigate to results screen directly
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (context) => RiskResultsScreen(patient: patient),
        ),
      );
    }
  }

  Future<void> _selectBirthDate(BuildContext context) async {
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: _birthDate,
      firstDate: DateTime.now().subtract(const Duration(days: 30)),
      lastDate: DateTime.now(),
    );
    if (picked != null && picked != _birthDate) {
      setState(() {
        _birthDate = picked;
      });
    }
  }

  Future<void> _selectBirthTime(BuildContext context) async {
    final TimeOfDay? picked = await showTimePicker(
      context: context,
      initialTime: _birthTime,
    );
    if (picked != null && picked != _birthTime) {
      setState(() {
        _birthTime = picked;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final livePatient = _buildCurrentPatientParameters();
    final liveResult = EoscalCalculator.calculate(livePatient);

    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.existingPatient != null
              ? 'Edit Assessment'
              : 'New Sepsis Assessment',
        ),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
      ),
      body: Form(
        key: _formKey,
        child: Column(
          children: [
            // Progress Bar / Stepper Header
            Container(
              color: WhoTheme.primaryNavy.withValues(alpha: 0.04),
              padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 24),
              child: Row(
                children: [
                  _buildStepHeaderCircle(0, 'Maternal'),
                  _buildStepHeaderLine(1),
                  _buildStepHeaderCircle(1, 'Neonatal'),
                  _buildStepHeaderLine(2),
                  _buildStepHeaderCircle(2, 'Laboratory'),
                ],
              ),
            ),

            // Active Form Step Body
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: _buildActiveStepContent(),
              ),
            ),

            // Real-time Running Score Preview Footer
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
              decoration: BoxDecoration(
                color: Colors.white,
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.08),
                    offset: const Offset(0, -3),
                    blurRadius: 6,
                  ),
                ],
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Text(
                          'LIVE SCORE PREVIEW',
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            color: WhoTheme.neutralDarkGrey,
                            letterSpacing: 1.1,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Row(
                          children: [
                            Text(
                              'Score: ${liveResult.totalScore}',
                              style: const TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.bold,
                                color: WhoTheme.primaryNavy,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: liveResult.riskCategory.color,
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                liveResult.riskCategory.displayName,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 11,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  _currentStep < 2
                      ? ElevatedButton(
                          onPressed: () {
                            if (_formKey.currentState!.validate()) {
                              setState(() {
                                _currentStep++;
                              });
                            }
                          },
                          child: const Text('Next'),
                        )
                      : ElevatedButton(
                          onPressed: _submitForm,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: WhoTheme.secondaryTeal,
                          ),
                          child: const Text('Save & Calculate'),
                        ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStepHeaderCircle(int step, String label) {
    final isActive = _currentStep == step;
    final isDone = _currentStep > step;

    return Expanded(
      child: Column(
        children: [
          CircleAvatar(
            radius: 16,
            backgroundColor: isDone
                ? WhoTheme.secondaryTeal
                : isActive
                ? WhoTheme.primaryNavy
                : Colors.grey.shade300,
            child: isDone
                ? const Icon(Icons.check, color: Colors.white, size: 16)
                : Text(
                    '${step + 1}',
                    style: TextStyle(
                      color: isActive || isDone
                          ? Colors.white
                          : Colors.grey.shade600,
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                    ),
                  ),
          ),
          const SizedBox(height: 4),
          Text(
            label,
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 11,
              fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
              color: isActive ? WhoTheme.primaryNavy : Colors.grey.shade600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStepHeaderLine(int beforeStep) {
    final isPassed = _currentStep >= beforeStep;
    return Expanded(
      child: Container(
        height: 2,
        color: isPassed ? WhoTheme.secondaryTeal : Colors.grey.shade300,
        margin: const EdgeInsets.only(bottom: 16),
      ),
    );
  }

  Widget _buildActiveStepContent() {
    switch (_currentStep) {
      case 0:
        return _buildStep1Maternal();
      case 1:
        return _buildStep2Neonatal();
      case 2:
      default:
        return _buildStep3Laboratory();
    }
  }

  // --- STEP 1: MATERNAL PARAMETERS ---
  Widget _buildStep1Maternal() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '1. Demographics & Maternal Background',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 16),

        // Patient Name
        TextFormField(
          controller: _nameController,
          decoration: const InputDecoration(
            labelText: 'Neonate Identifier / Name',
            hintText: 'e.g. Baby Girl Adams',
          ),
          validator: (val) {
            if (val == null || val.trim().isEmpty) {
              return 'Please enter patient name or code identifier';
            }
            return null;
          },
        ),
        const SizedBox(height: 16),

        // MRN
        TextFormField(
          controller: _mrnController,
          decoration: const InputDecoration(
            labelText: 'Medical Record Number (MRN)',
            hintText: 'e.g. MRN-2026-991',
          ),
          validator: (val) {
            if (val == null || val.trim().isEmpty) {
              return 'Please enter MRN';
            }
            return null;
          },
        ),
        const SizedBox(height: 20),

        // Gestational Age Slider & Warning
        Text(
          'Gestational Age: ${_gestationalAge.toStringAsFixed(1)} weeks',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        Slider(
          value: _gestationalAge,
          min: 30.0,
          max: 42.0,
          divisions: 24,
          label: '${_gestationalAge.toStringAsFixed(1)} weeks',
          onChanged: (val) {
            setState(() {
              _gestationalAge = val;
            });
          },
        ),
        if (_gestationalAge < 35.0) ...[
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.amber.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.amber.shade300),
            ),
            child: Row(
              children: [
                Icon(Icons.warning_amber_rounded, color: Colors.amber.shade800),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    'Gestational Age is <35 weeks. EOSCAL 2024 is designed for late-preterm/term infants. Interpret results with extreme caution for early preterm infants.',
                    style: TextStyle(fontSize: 12, color: Colors.black87),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ],

        // Birth DateTime Pickers
        const Text(
          'Birth Date & Time',
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => _selectBirthDate(context),
                icon: const Icon(Icons.calendar_today, size: 16),
                label: Text(DateFormat('yyyy-MM-dd').format(_birthDate)),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => _selectBirthTime(context),
                icon: const Icon(Icons.access_time, size: 16),
                label: Text(_birthTime.format(context)),
              ),
            ),
          ],
        ),
        const SizedBox(height: 20),

        const Divider(),
        const SizedBox(height: 12),
        Text(
          'Maternal Intrapartum Indicators',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 16),

        // Maternal Temperature Slider
        Text(
          'Max Maternal Temperature: ${_maternalTemp.toStringAsFixed(1)} °C',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        Slider(
          value: _maternalTemp,
          min: 36.0,
          max: 41.0,
          divisions: 50,
          label: '${_maternalTemp.toStringAsFixed(1)} °C',
          onChanged: (val) {
            setState(() {
              _maternalTemp = val;
            });
          },
        ),
        const SizedBox(height: 8),

        // ROM Hours Slider
        Text(
          'Rupture of Membranes (ROM): ${_romHours.toStringAsFixed(0)} hours',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        Slider(
          value: _romHours,
          min: 0.0,
          max: 72.0,
          divisions: 72,
          label: '${_romHours.toStringAsFixed(0)} hours',
          onChanged: (val) {
            setState(() {
              _romHours = val;
            });
          },
        ),
        const SizedBox(height: 8),

        // Delivery Mode Dropdown
        DropdownButtonFormField<String>(
          initialValue: _deliveryMode,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Delivery Mode'),
          items: const [
            DropdownMenuItem(value: 'Vaginal', child: Text('Vaginal')),
            DropdownMenuItem(value: 'Caesarean', child: Text('Caesarean')),
          ],
          onChanged: (val) {
            if (val != null) {
              setState(() {
                _deliveryMode = val;
              });
            }
          },
        ),
        const SizedBox(height: 16),

        // GBS positive Switch
        SwitchListTile(
          title: const Text('Maternal GBS Positive'),
          subtitle: const Text('Group B Streptococcus colonization confirmed'),
          value: _gbsPositive,
          onChanged: (val) {
            setState(() {
              _gbsPositive = val;
              if (!val) _adequateAbx = false; // Reset if GBS negative
            });
          },
        ),

        // Adequate Antibiotics Switch (Only active if GBS positive or Chorio positive)
        SwitchListTile(
          title: const Text('Adequate Intrapartum Antibiotics'),
          subtitle: const Text(
            'Penicillin / Ampicillin / Cefazolin >= 4 hours before birth',
          ),
          value: _adequateAbx,
          onChanged: _gbsPositive || _chorio
              ? (val) {
                  setState(() {
                    _adequateAbx = val;
                  });
                }
              : null, // Disabled if not relevant
        ),

        // Chorioamnionitis Switch
        SwitchListTile(
          title: const Text('Clinical Chorioamnionitis'),
          subtitle: const Text(
            'Maternal clinical diagnosis of uterine infection',
          ),
          value: _chorio,
          onChanged: (val) {
            setState(() {
              _chorio = val;
              if (val) _adequateAbx = true; // Auto-suggest or enable IAP
            });
          },
        ),
        const SizedBox(height: 24),
      ],
    );
  }

  // --- STEP 2: NEONATAL PARAMETERS ---
  Widget _buildStep2Neonatal() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '2. Neonatal Clinical Status Assessment',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 16),

        // Respiratory Distress Dropdown
        DropdownButtonFormField<String>(
          initialValue: _respiratoryDistress,
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'Respiratory Distress Level',
            helperText: 'Tachypnea, retractions, grunting, nasal flaring',
          ),
          items: const [
            DropdownMenuItem(value: 'None', child: Text('None / Resolved')),
            DropdownMenuItem(
              value: 'Mild',
              child: Text('Mild (transient tachypnea, brief grunting)'),
            ),
            DropdownMenuItem(
              value: 'Severe',
              child: Text('Severe (persistent retractions, grunting, flaring)'),
            ),
          ],
          onChanged: (val) {
            if (val != null) {
              setState(() {
                _respiratoryDistress = val;
              });
            }
          },
        ),
        const SizedBox(height: 16),

        // Oxygen Support Dropdown
        DropdownButtonFormField<String>(
          initialValue: _oxygenNeed,
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'Oxygen Support Required',
          ),
          items: const [
            DropdownMenuItem(value: 'None', child: Text('None / Room Air')),
            DropdownMenuItem(
              value: 'Supplemental',
              child: Text('Supplemental Oxygen (Hood / Nasal Prongs)'),
            ),
            DropdownMenuItem(
              value: 'CPAP/Ventilation',
              child: Text('CPAP / High-flow / Mechanical Ventilation'),
            ),
          ],
          onChanged: (val) {
            if (val != null) {
              setState(() {
                _oxygenNeed = val;
              });
            }
          },
        ),
        const SizedBox(height: 20),

        // Neonatal Temperature Slider
        Text(
          'Neonatal Temperature: ${_neonatalTemp.toStringAsFixed(1)} °C',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        Slider(
          value: _neonatalTemp,
          min: 35.0,
          max: 39.0,
          divisions: 40,
          label: '${_neonatalTemp.toStringAsFixed(1)} °C',
          onChanged: (val) {
            setState(() {
              _neonatalTemp = val;
            });
          },
        ),
        const SizedBox(height: 8),

        // APGAR slider
        Text(
          '5-Minute APGAR Score: $_apgar5Min',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        Slider(
          value: _apgar5Min.toDouble(),
          min: 0,
          max: 10,
          divisions: 10,
          label: '$_apgar5Min',
          onChanged: (val) {
            setState(() {
              _apgar5Min = val.toInt();
            });
          },
        ),
        const SizedBox(height: 8),

        // Neurological Dropdown
        DropdownButtonFormField<String>(
          initialValue: _neurologicalStatus,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Neurological Status'),
          items: const [
            DropdownMenuItem(
              value: 'Normal',
              child: Text('Normal tone & reactivity'),
            ),
            DropdownMenuItem(
              value: 'Irritable/Lethargic',
              child: Text('Lethargy, hypotonia, or irritability'),
            ),
            DropdownMenuItem(
              value: 'Seizures',
              child: Text('Observed neonatal seizures'),
            ),
          ],
          onChanged: (val) {
            if (val != null) {
              setState(() {
                _neurologicalStatus = val;
              });
            }
          },
        ),
        const SizedBox(height: 16),

        // Poor Perfusion Switch
        SwitchListTile(
          title: const Text('Signs of Poor Perfusion / Shock'),
          subtitle: const Text(
            'CRT > 3s, mottled skin, weak pulses, hypotension',
          ),
          value: _poorPerfusion,
          onChanged: (val) {
            setState(() {
              _poorPerfusion = val;
            });
          },
        ),
        const SizedBox(height: 24),
      ],
    );
  }

  // --- STEP 3: LABORATORY EVIDENCE (OPTIONAL) ---
  Widget _buildStep3Laboratory() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '3. Laboratory Investigations',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: Colors.grey.shade200,
                borderRadius: BorderRadius.circular(4),
              ),
              child: const Text(
                'OPTIONAL',
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  color: WhoTheme.neutralDarkGrey,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        const Text(
          'Fill in any completed laboratory results. Leave blank if results are pending or tests were not indicated.',
          style: TextStyle(fontSize: 12, color: WhoTheme.neutralDarkGrey),
        ),
        const SizedBox(height: 16),

        // WBC
        TextFormField(
          controller: _wbcController,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'White Blood Cell (WBC) Count',
            hintText: 'cells/mm³ (normal: 5,000 - 25,000)',
            suffixText: '/mm³',
          ),
        ),
        const SizedBox(height: 16),

        // I:T Ratio
        TextFormField(
          controller: _itRatioController,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(
            labelText: 'Immature to Total (I:T) Ratio',
            hintText: 'e.g. 0.15 (normal: <0.20)',
          ),
        ),
        const SizedBox(height: 16),

        // Platelets
        TextFormField(
          controller: _plateletsController,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Platelet Count',
            hintText: 'cells/mm³ (normal: >=100,000)',
            suffixText: '/mm³',
          ),
        ),
        const SizedBox(height: 16),

        // CRP
        TextFormField(
          controller: _crpController,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(
            labelText: 'C-Reactive Protein (CRP) level',
            hintText: 'mg/L (normal: <10 mg/L)',
            suffixText: 'mg/L',
          ),
        ),
        const SizedBox(height: 16),

        // Procalcitonin
        TextFormField(
          controller: _pctController,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(
            labelText: 'Procalcitonin (PCT) level',
            hintText: 'ng/mL (normal: <0.5 ng/mL)',
            suffixText: 'ng/mL',
          ),
        ),
        const SizedBox(height: 16),

        // Blood Culture positive Switch
        SwitchListTile(
          title: const Text('Blood Culture Result'),
          subtitle: const Text('Check to indicate blood culture is POSITIVE'),
          value: _bloodCulturePositive,
          activeThumbColor: WhoTheme.riskCritical,
          onChanged: (val) {
            setState(() {
              _bloodCulturePositive = val;
            });
          },
        ),
        const SizedBox(height: 32),
      ],
    );
  }
}
