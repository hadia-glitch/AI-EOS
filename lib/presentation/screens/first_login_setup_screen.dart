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
  ConsumerState<FirstLoginSetupScreen> createState() =>
      _FirstLoginSetupScreenState();
}

class _FirstLoginSetupScreenState extends ConsumerState<FirstLoginSetupScreen> {
  static const _totalSteps =
      5; // role, hospital, guideline, pin, mfa (optional)

  int _step = 0;
  String _role = 'Registrar';
  String _guideline = 'NICE';
  final _pinController = TextEditingController();
  final _confirmPinController = TextEditingController();
  bool _biometricEnabled = false;
  bool _biometricAvailable = false;
  bool _pinObscure = true;
  bool _confirmObscure = true;

  // ── MFA (optional, final step) ───────────────────────────────────────────
  final bool _wantsMfa = false;
  bool _mfaEnrollmentStarted = false;
  String? _mfaFactorId;
  String? _mfaSecret;
  String? _mfaOtpauthUri;
  final _mfaCodeController = TextEditingController();
  bool _mfaBusy = false;
  String? _mfaError;

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
    _mfaCodeController.dispose();
    super.dispose();
  }

  void _goBack() {
    if (_step > 0) {
      setState(() => _step--);
    } else {
      AuthService.instance.signOut().then((_) {
        if (mounted) context.go('/login');
      });
    }
  }

  Future<void> _finishPinStep() async {
    final pin = _pinController.text.trim();
    final confirm = _confirmPinController.text.trim();
    if (pin.length < 4 || pin != confirm) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Enter matching PINs with at least 4 digits.'),
        ),
      );
      return;
    }

    final userId = AuthService.instance.currentUser?.id;
    if (userId != null) {
      try {
        final formattedRole = _role.toLowerCase().replaceAll(' ', '_');
        const defaultInstId = '00000000-0000-0000-0000-000000000001';
        final pinHash = sha256.convert(utf8.encode(pin)).toString();

        await AuthService.instance.client.from('users').upsert({
          'id': userId,
          'role': formattedRole,
          'institution_id': defaultInstId,
          'default_guideline': _guideline,
          'pin_hash': pinHash,
        });

        await AuthService.instance.client.auth.updateUser(
          UserAttributes(
            data: {
              'role': formattedRole,
              'institution_id': defaultInstId,
              'default_guideline': _guideline,
            },
          ),
        );
      } catch (e) {
        debugPrint('Could not update users row and auth metadata: $e');
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(
                'Warning: settings may not have saved (offline?): $e',
              ),
            ),
          );
        }
      }
    }

    await AuthService.instance.saveLocalUnlock(
      pin: pin,
      biometricEnabled: _biometricEnabled,
    );

    // Now that we're signed in for real, migrate any leftover locally-cached
    // patient data (relevant if this account went through an offline signup
    // and already entered patients before setup completed).
    try {
      await ref.read(patientsProvider.notifier).refresh();
    } catch (_) {}

    if (mounted) setState(() => _step++); // advance to optional MFA step
  }

  Future<void> _startMfaEnrollment() async {
    setState(() {
      _mfaBusy = true;
      _mfaError = null;
    });
    try {
      final result = await AuthService.instance.beginMfaEnrollment();
      setState(() {
        _mfaEnrollmentStarted = true;
        _mfaFactorId = result.factorId;
        _mfaSecret = result.secret;
        _mfaOtpauthUri = result.otpauthUri;
      });
    } catch (e) {
      setState(
        () => _mfaError = 'Could not start MFA setup (are you online?): $e',
      );
    } finally {
      if (mounted) setState(() => _mfaBusy = false);
    }
  }

  Future<void> _confirmMfaCode() async {
    if (_mfaFactorId == null || _mfaCodeController.text.trim().length != 6) {
      setState(
        () => _mfaError = 'Enter the 6-digit code from your authenticator app.',
      );
      return;
    }
    setState(() {
      _mfaBusy = true;
      _mfaError = null;
    });
    try {
      await AuthService.instance.confirmMfaEnrollment(
        factorId: _mfaFactorId!,
        code: _mfaCodeController.text.trim(),
      );
      if (mounted) context.go('/home');
    } catch (e) {
      setState(() => _mfaError = 'Incorrect code. Try again.');
    } finally {
      if (mounted) setState(() => _mfaBusy = false);
    }
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
              Wrap(
                alignment: WrapAlignment.center,
                spacing: 8,
                runSpacing: 8,
                children: List.generate(_totalSteps, (i) {
                  final isActive = i == _step;
                  final isDone = i < _step;
                  return AnimatedContainer(
                    duration: const Duration(milliseconds: 250),
                    width: isActive ? 28 : 12,
                    height: 12,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(6),
                      color: isDone || isActive
                          ? WhoTheme.secondaryTeal
                          : Colors.grey.shade300,
                    ),
                  );
                }),
              ),
              const SizedBox(height: 24),
              Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (_step == 0) ..._buildRoleStep(),
                      if (_step == 1) ..._buildHospitalStep(),
                      if (_step == 2) ..._buildGuidelineStep(),
                      if (_step == 3) ..._buildPinStep(),
                      if (_step == 4) ..._buildMfaStep(),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _primaryButtonAction(),
                  child: Text(_primaryButtonLabel()),
                ),
              ),
              if (_step == 4)
                TextButton(
                  onPressed: _mfaBusy ? null : () => context.go('/home'),
                  child: const Text('Skip for now'),
                ),
            ],
          ),
        ),
      ),
    );
  }

  String _primaryButtonLabel() {
    if (_step < 3) return 'Continue';
    if (_step == 3) return 'Get Started';
    if (!_mfaEnrollmentStarted) return 'Set up two-factor authentication';
    return 'Verify & Finish';
  }

  VoidCallback? _primaryButtonAction() {
    if (_step < 3) return () => setState(() => _step++);
    if (_step == 3) return _finishPinStep;
    if (_mfaBusy) return null;
    if (!_mfaEnrollmentStarted) return _startMfaEnrollment;
    return _confirmMfaCode;
  }

  List<Widget> _buildRoleStep() => [
    const Text(
      'Your Clinical Role',
      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
    ),
    const SizedBox(height: 8),
    const Text(
      'Select the role that best describes your position.',
      style: TextStyle(color: Colors.grey),
    ),
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
    const Text(
      'Your Hospital',
      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
    ),
    const SizedBox(height: 8),
    const Text(
      'Your institution determines available guidelines and protocols.',
      style: TextStyle(color: Colors.grey),
    ),
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
    const Text(
      'Default Guideline',
      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
    ),
    const SizedBox(height: 8),
    const Text(
      'Choose the clinical guideline used at your institution.',
      style: TextStyle(color: Colors.grey),
    ),
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
    const Text(
      'Secure App Unlock',
      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
    ),
    const SizedBox(height: 8),
    const Text(
      'Set a PIN to unlock the app without signing in each time.',
      style: TextStyle(color: Colors.grey),
    ),
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
      onChanged: _biometricAvailable
          ? (value) => setState(() => _biometricEnabled = value)
          : null,
    ),
  ];

  List<Widget> _buildMfaStep() => [
    const Text(
      'Two-Factor Authentication',
      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
    ),
    const SizedBox(height: 8),
    const Text(
      'Optional, but recommended. Adds a second verification step using an authenticator app '
      '(Google Authenticator, Authy, etc). Requires an internet connection to set up.',
      style: TextStyle(color: Colors.grey),
    ),
    const SizedBox(height: 20),
    if (!_mfaEnrollmentStarted) ...[
      const Icon(
        Icons.shield_outlined,
        size: 48,
        color: WhoTheme.secondaryTeal,
      ),
      const SizedBox(height: 12),
      const Text(
        'Tap "Set up two-factor authentication" below to begin, or skip for now.',
      ),
    ] else ...[
      const Text(
        '1. Add this to your authenticator app:',
        style: TextStyle(fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 8),
      Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Colors.grey.shade100,
          borderRadius: BorderRadius.circular(8),
        ),
        child: SelectableText(
          _mfaSecret ?? '',
          style: const TextStyle(fontFamily: 'monospace', fontSize: 14),
        ),
      ),
      const SizedBox(height: 4),
      Text(
        'Manual entry key shown above. If your authenticator app supports scanning a URI, '
        'you can also paste: ${_mfaOtpauthUri ?? ''}',
        style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
      ),
      const SizedBox(height: 20),
      const Text(
        '2. Enter the 6-digit code it generates:',
        style: TextStyle(fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 8),
      TextField(
        controller: _mfaCodeController,
        keyboardType: TextInputType.number,
        maxLength: 6,
        textAlign: TextAlign.center,
        style: const TextStyle(fontSize: 22, letterSpacing: 8),
        decoration: const InputDecoration(counterText: ''),
      ),
    ],
    if (_mfaError != null) ...[
      const SizedBox(height: 8),
      Text(_mfaError!, style: const TextStyle(color: Colors.red)),
    ],
  ];
}
