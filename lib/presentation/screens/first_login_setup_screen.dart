import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/theme.dart';
import '../patient_state.dart';

class FirstLoginSetupScreen extends ConsumerStatefulWidget {
  const FirstLoginSetupScreen({super.key});

  @override
  ConsumerState<FirstLoginSetupScreen> createState() => _FirstLoginSetupScreenState();
}

class _FirstLoginSetupScreenState extends ConsumerState<FirstLoginSetupScreen> {
  int _step = 0;
  String _role = 'Registrar';
  String _guideline = 'NICE';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Account Setup'), automaticallyImplyLeading: false),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: List.generate(3, (i) {
                return Container(
                  margin: const EdgeInsets.symmetric(horizontal: 4),
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: i <= _step ? WhoTheme.secondaryTeal : Colors.grey.shade300,
                  ),
                );
              }),
            ),
            const SizedBox(height: 24),
            if (_step == 0) ...[
              const Text('Your Clinical Role', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              ...['Attending Neonatologist', 'Registrar', 'Nurse', 'Administrator'].map(
                (r) => RadioListTile(value: r, groupValue: _role, title: Text(r), onChanged: (v) => setState(() => _role = v!)),
              ),
            ] else if (_step == 1) ...[
              const Text('Your Hospital', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const ListTile(title: Text('Demo Neonatal Unit — Pakistan')),
              TextButton(onPressed: () {}, child: const Text('Request new institution')),
            ] else ...[
              const Text('Default Guideline', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'NICE', label: Text('NICE')),
                  ButtonSegment(value: 'AAP', label: Text('AAP')),
                  ButtonSegment(value: 'WHO', label: Text('WHO')),
                ],
                selected: {_guideline},
                onSelectionChanged: (s) {
                  setState(() => _guideline = s.first);
                  ref.read(activeGuidelineProvider.notifier).setGuideline(_guideline);
                },
              ),
              SwitchListTile(title: const Text('Enable push notifications'), value: true, onChanged: (_) {}),
              SwitchListTile(title: const Text('Biometric unlock'), value: false, onChanged: (_) {}),
            ],
            const Spacer(),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: () {
                  if (_step < 2) {
                    setState(() => _step++);
                  } else {
                    Navigator.pushReplacementNamed(context, '/home');
                  }
                },
                child: Text(_step < 2 ? 'Continue' : 'Get Started'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
