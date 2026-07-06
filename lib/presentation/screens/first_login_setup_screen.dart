import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../core/theme.dart';
import '../../data/auth_service.dart';
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
  final _pinController = TextEditingController();
  final _confirmPinController = TextEditingController();
  bool _biometricEnabled = false;
  bool _biometricAvailable = false;
  bool _pinObscure = true;
  bool _confirmObscure = true;

  @override
  void initState() {
    super.initState();
    AuthService.instance.canUseBiometrics().then((available) {
      if (mounted) setState(() => _biometricAvailable = available);
    });
  }

  @override
  void dispose() {
    _pinController.dispose();
    _confirmPinController.dispose();
    super.dispose();
  }

  void _goBack() {
    if (_step > 0) {
      setState(() => _step--);
    } else {
      // Step 0: go back to login and sign out
      AuthService.instance.signOut().then((_) {
        if (mounted) context.go('/login');
      });
    }
  }

  Future<void> _finishSetup() async {
    final pin = _pinController.text.trim();
    final confirm = _confirmPinController.text.trim();
    if (pin.length < 4 || pin != confirm) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Enter matching PINs with at least 4 digits.')),
      );
      return;
    }

    // Update users table with chosen role, institution, guideline, and pin_hash
    final userId = AuthService.instance.currentUser?.id;
    if (userId != null) {
      try {
        final formattedRole = _role.toLowerCase().replaceAll(' ', '_');
        const defaultInstId = '00000000-0000-0000-0000-000000000001';
        // compute a SHA-256 hash of the PIN and store that in the DB
        final pinHash = sha256.convert(utf8.encode(pin)).toString();

        // 1. Update public.users with pin_hash (avoid storing plaintext PIN)
        await AuthService.instance.client.from('users').upsert({
          'id': userId,
          'role': formattedRole,
          'institution_id': defaultInstId,
          'default_guideline': _guideline,
          'pin_hash': pinHash,
        });

        // 2. Update auth.users metadata (mirror pin_hash in user metadata)
        await AuthService.instance.client.auth.updateUser(
          UserAttributes(
            data: {
              'role': formattedRole,
              'institution_id': defaultInstId,
              'default_guideline': _guideline,
              'pin_hash': pinHash,
            },
          ),
        );
      } catch (e) {
        debugPrint('Could not update users row and auth metadata: $e');
      }
    }

    await AuthService.instance.saveLocalUnlock(
      pin: pin,
      biometricEnabled: _biometricEnabled,
    );
    if (mounted) context.go('/home');
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _goBack();
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Account Setup'),
          leading: IconButton(
            icon: const Icon(Icons.arrow_back),
            onPressed: _goBack,
            tooltip: _step == 0 ? 'Sign out' : 'Back',
          ),
        ),
        body: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Step indicator
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: List.generate(4, (i) {
                  final isActive = i == _step;
                  final isDone = i < _step;
                  return AnimatedContainer(
                    duration: const Duration(milliseconds: 250),
                    margin: const EdgeInsets.symmetric(horizontal: 4),
                    width: isActive ? 28 : 12,
                    height: 12,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(6),
                      color: isDone || isActive ? WhoTheme.secondaryTeal : Colors.grey.shade300,
                    ),
                  );
                }),
              ),
              const SizedBox(height: 24),
              if (_step == 0) ..._buildRoleStep(),
              if (_step == 1) ..._buildHospitalStep(),
              if (_step == 2) ..._buildGuidelineStep(),
              if (_step == 3) ..._buildPinStep(),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () {
                    if (_step < 3) {
                      setState(() => _step++);
                    } else {
                      _finishSetup();
                    }
                  },
                  child: Text(_step < 3 ? 'Continue' : 'Get Started'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _buildRoleStep() => [
        const Text('Your Clinical Role', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('Select the role that best describes your position.', style: TextStyle(color: Colors.grey)),
        const SizedBox(height: 12),
        ...['Attending Neonatologist', 'Registrar', 'Nurse', 'Administrator'].map(
          (r) => RadioListTile<String>(
            value: r,
            groupValue: _role,
            title: Text(r),
            onChanged: (v) => setState(() => _role = v!),
          ),
        ),
      ];

  List<Widget> _buildHospitalStep() => [
        const Text('Your Hospital', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('Your institution determines available guidelines and protocols.',
            style: TextStyle(color: Colors.grey)),
        const SizedBox(height: 16),
        Card(
          child: const ListTile(
            leading: Icon(Icons.local_hospital_outlined),
            title: Text('Demo Neonatal Unit'),
            subtitle: Text('Pakistan'),
          ),
        ),
        const SizedBox(height: 8),
        TextButton.icon(
          onPressed: () {},
          icon: const Icon(Icons.add),
          label: const Text('Request new institution'),
        ),
      ];

  List<Widget> _buildGuidelineStep() => [
        const Text('Default Guideline', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('Choose the clinical guideline used at your institution.',
            style: TextStyle(color: Colors.grey)),
        const SizedBox(height: 16),
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
        const SizedBox(height: 16),
        SwitchListTile(
          title: const Text('Enable push notifications'),
          value: true,
          onChanged: (_) {},
        ),
      ];

  List<Widget> _buildPinStep() => [
        const Text('Secure App Unlock', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('Set a PIN to unlock the app without signing in each time.',
            style: TextStyle(color: Colors.grey)),
        const SizedBox(height: 20),
        TextField(
          controller: _pinController,
          obscureText: _pinObscure,
          keyboardType: TextInputType.number,
          maxLength: 6,
          decoration: InputDecoration(
            labelText: 'Create PIN (4–6 digits)',
            suffixIcon: IconButton(
              icon: Icon(_pinObscure ? Icons.visibility : Icons.visibility_off),
              onPressed: () => setState(() => _pinObscure = !_pinObscure),
            ),
          ),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _confirmPinController,
          obscureText: _confirmObscure,
          keyboardType: TextInputType.number,
          maxLength: 6,
          decoration: InputDecoration(
            labelText: 'Confirm PIN',
            suffixIcon: IconButton(
              icon: Icon(_confirmObscure ? Icons.visibility : Icons.visibility_off),
              onPressed: () => setState(() => _confirmObscure = !_confirmObscure),
            ),
          ),
        ),
        const SizedBox(height: 4),
        SwitchListTile(
          title: const Text('Biometric unlock'),
          subtitle: Text(
            _biometricAvailable
                ? 'Use fingerprint or device biometrics when available'
                : 'No biometric capability detected on this device',
          ),
          value: _biometricAvailable && _biometricEnabled,
          onChanged: _biometricAvailable ? (value) => setState(() => _biometricEnabled = value) : null,
        ),
      ];
}