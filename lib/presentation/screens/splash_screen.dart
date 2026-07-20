import 'dart:async';
import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/theme.dart';
import '../../data/api/api_client.dart';
import '../../data/auth_service.dart';
import '../../data/offline_guideline_cache.dart';
import '../../data/offline_pdf_cache.dart';
import '../../data/supabase_config.dart';
import '../patient_state.dart';

class SplashScreen extends ConsumerStatefulWidget {
  const SplashScreen({super.key});

  @override
  ConsumerState<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends ConsumerState<SplashScreen> {
  final _pinController = TextEditingController();
  String _status = 'Checking secure session...';
  bool _needsPin = false;
  bool _pinError = false;

  bool _verifyingDbPin = false;
  String? _dbPinHash;
  String? _dbRole;
  String? _dbGuideline;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  @override
  void dispose() {
    _pinController.dispose();
    super.dispose();
  }

  Future<void> _boot() async {
    setState(() => _status = 'Loading clinical data...');
    await Future.delayed(const Duration(milliseconds: 500));
    final online = await apiClient.isBackendAvailable();
    if (online) {
      // Fire-and-forget: refreshes (a) the offline care-plan protocol cache
      // and (b) the guideline PDF document list (metadata only — actual PDF
      // bytes download lazily on first "view source" tap, see
      // OfflinePdfCache). Neither is awaited — must never delay app boot —
      // and both log-and-swallow their own failures internally.
      unawaited(OfflineGuidelineCache.sync());
      unawaited(OfflinePdfCache.syncDocumentList());
      // Proactively caches every guideline PDF (WiFi-gated by default — see
      // OfflinePdfCache.syncAllPdfs) so offline "jump to source" works even
      // for documents the clinician has never manually opened. This also
      // calls syncDocumentList internally, so the line above is technically
      // redundant once this completes — kept anyway so the (tiny) document
      // list is fresh immediately even before a WiFi-gated bulk sync runs.
      unawaited(OfflinePdfCache.syncAllPdfs());
    }

    // Not configured or not signed in → go to login
    if (!SupabaseConfig.isConfigured || !AuthService.instance.isSignedIn) {
      if (!mounted) return;
      setState(() => _status = online ? 'Ready' : 'Offline Mode');
      await Future.delayed(const Duration(milliseconds: 400));
      if (mounted) context.go('/login');
      return;
    }

    // Signed in but email not confirmed → sign out and go to login
    final user = AuthService.instance.currentUser;
    final emailConfirmed = user?.emailConfirmedAt != null;
    if (!emailConfirmed) {
      await AuthService.instance.signOut();
      if (mounted) context.go('/login');
      return;
    }

    // ── Authoritative setup check ──────────────────────────────────────────
    // CRITICAL FIX: whether this account needs first-login setup is decided
    // by querying users.pin_hash in Supabase for THIS user id — never by
    // checking local device storage. Local storage can hold a leftover PIN
    // from a different account that previously used this device/emulator,
    // which was the exact cause of new signups jumping straight to PIN entry.
    setState(() => _status = 'Retrieving account settings...');
    Map<String, dynamic>? userRow;
    try {
      userRow = await AuthService.instance.client
          .from('users')
          .select()
          .eq('id', user!.id)
          .maybeSingle();
    } catch (e) {
      debugPrint('Error checking user record in db: $e');
    }

    final dbPinHash = userRow?['pin_hash'] as String?;

    if (dbPinHash == null || dbPinHash.isEmpty) {
      // This account has never completed setup on any device.
      if (mounted) context.go('/setup');
      return;
    }

    // This account HAS completed setup before (maybe on another device).
    // Check whether THIS device already has a cached local PIN for THIS
    // user id — that's just a fast path, not a setup-completeness check.
    final hasLocalCache = await AuthService.instance.hasLocalUnlock();

    if (!hasLocalCache) {
      // First time this account is used on this particular device: verify
      // directly against the DB hash, then cache locally on success.
      setState(() {
        _needsPin = true;
        _verifyingDbPin = true;
        _dbPinHash = dbPinHash;
        _dbRole = userRow?['role'] as String?;
        _dbGuideline = userRow?['default_guideline'] as String?;
        _status = 'First login on this device. Enter PIN to unlock.';
      });
      return;
    }

    // Has local cache → try biometrics, fall back to local PIN entry.
    setState(() => _status = online ? 'Verify device unlock' : 'Offline Mode - verify unlock');
    final biometricOk = await AuthService.instance.unlockWithBiometrics();
    if (!mounted) return;
    if (biometricOk) {
      _syncSettingsFromDb();
      context.go('/home');
    } else {
      setState(() => _needsPin = true);
    }
  }

  Future<void> _syncSettingsFromDb() async {
    try {
      final settings = await AuthService.instance.loadUserSettings();
      if (settings != null && settings['default_guideline'] != null) {
        ref.read(activeGuidelineProvider.notifier).setGuideline(settings['default_guideline'] as String);
      }
    } catch (e) {
      debugPrint('Background settings sync failed: $e');
    }
  }

  Future<void> _verifyPin() async {
    final enteredPin = _pinController.text.trim();
    if (_verifyingDbPin) {
      // Compare SHA-256 hash of entered PIN to stored hash
      final enteredHash = sha256.convert(utf8.encode(enteredPin)).toString();
      if (_dbPinHash == enteredHash) {
        setState(() => _status = 'Saving device settings...');
        // Cache PIN locally, scoped to this user id
        await AuthService.instance.saveLocalUnlock(
          pin: enteredPin,
          biometricEnabled: false,
        );
        // Load default guideline & user role into local prefs/Riverpod
        final prefs = await SharedPreferences.getInstance();
        if (_dbGuideline != null) {
          await prefs.setString('active_guideline', _dbGuideline!);
          ref.read(activeGuidelineProvider.notifier).setGuideline(_dbGuideline!);
        }
        if (_dbRole != null) {
          await prefs.setString('user_role', _dbRole!);
        }
        if (mounted) context.go('/home');
      } else {
        setState(() {
          _pinError = true;
          _pinController.clear();
        });
      }
    } else {
      final ok = await AuthService.instance.verifyPin(enteredPin);
      if (!mounted) return;
      if (ok) {
        _syncSettingsFromDb();
        context.go('/home');
      } else {
        setState(() {
          _pinError = true;
          _pinController.clear();
        });
      }
    }
  }

  Future<void> _signOutAndReturn() async {
    await AuthService.instance.signOut();
    if (mounted) context.go('/login');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: WhoTheme.primaryNavy,
      body: SafeArea(
        child: _needsPin ? _buildPinUnlock() : _buildLoading(),
      ),
    );
  }

  Widget _buildLoading() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(
                color: WhoTheme.secondaryTeal.withValues(alpha: 0.5),
                width: 3,
              ),
            ),
            child: const Icon(Icons.security, color: Colors.white, size: 64),
          ),
          const SizedBox(height: 24),
          const Text(
            'NeoGuard AI',
            style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 16),
          Text(_status, style: TextStyle(color: Colors.grey.shade400, fontSize: 14)),
          const SizedBox(height: 32),
          SizedBox(
            width: 28,
            height: 28,
            child: CircularProgressIndicator(
              strokeWidth: 2.5,
              color: WhoTheme.secondaryTeal.withValues(alpha: 0.8),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPinUnlock() {
    return Column(
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: IconButton(
            onPressed: _signOutAndReturn,
            icon: const Icon(Icons.arrow_back, color: Colors.white70),
            tooltip: 'Sign out',
          ),
        ),
        Expanded(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    padding: const EdgeInsets.all(20),
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: WhoTheme.secondaryTeal.withValues(alpha: 0.15),
                      border: Border.all(
                        color: WhoTheme.secondaryTeal.withValues(alpha: 0.5),
                        width: 2,
                      ),
                    ),
                    child: const Icon(Icons.lock_outline, color: Colors.white, size: 48),
                  ),
                  const SizedBox(height: 24),
                  const Text(
                    'NeoGuard AI',
                    style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Enter your PIN to unlock',
                    style: TextStyle(color: Colors.grey.shade400, fontSize: 14),
                  ),
                  const SizedBox(height: 40),
                  ValueListenableBuilder<TextEditingValue>(
                    valueListenable: _pinController,
                    builder: (context, value, _) {
                      return Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: List.generate(6, (i) {
                          final filled = i < value.text.length;
                          return Container(
                            margin: const EdgeInsets.symmetric(horizontal: 6),
                            width: 16,
                            height: 16,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: filled
                                  ? (_pinError ? Colors.red.shade400 : WhoTheme.secondaryTeal)
                                  : Colors.white.withValues(alpha: 0.2),
                              border: Border.all(
                                color: _pinError
                                    ? Colors.red.shade400
                                    : filled
                                        ? WhoTheme.secondaryTeal
                                        : Colors.white38,
                                width: 1.5,
                              ),
                            ),
                          );
                        }),
                      );
                    },
                  ),
                  if (_pinError) ...[
                    const SizedBox(height: 10),
                    Text(
                      'Incorrect PIN. Try again.',
                      style: TextStyle(color: Colors.red.shade400, fontSize: 13),
                    ),
                  ],
                  const SizedBox(height: 32),
                  Opacity(
                    opacity: 0,
                    child: SizedBox(
                      height: 0,
                      child: TextField(
                        controller: _pinController,
                        autofocus: true,
                        keyboardType: TextInputType.number,
                        maxLength: 6,
                        onChanged: (v) {
                          setState(() => _pinError = false);
                          if (v.length == 6) _verifyPin();
                        },
                      ),
                    ),
                  ),
                  _buildNumpad(),
                  const SizedBox(height: 24),
                  TextButton(
                    onPressed: _signOutAndReturn,
                    child: Text(
                      'Sign in with a different account',
                      style: TextStyle(color: Colors.grey.shade400, fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Text('v1.0.0', style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
        ),
      ],
    );
  }

  Widget _buildNumpad() {
    final keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'];
    return GridView.count(
      crossAxisCount: 3,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      childAspectRatio: 1.6,
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      children: keys.map((key) {
        if (key.isEmpty) return const SizedBox.shrink();
        return InkWell(
          onTap: () {
            if (key == '⌫') {
              if (_pinController.text.isNotEmpty) {
                _pinController.text = _pinController.text.substring(0, _pinController.text.length - 1);
                setState(() => _pinError = false);
              }
            } else {
              if (_pinController.text.length < 6) {
                _pinController.text += key;
                setState(() => _pinError = false);
                if (_pinController.text.length == 6) _verifyPin();
              }
            }
          },
          borderRadius: BorderRadius.circular(12),
          child: Container(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              color: Colors.white.withValues(alpha: 0.08),
              border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
            ),
            alignment: Alignment.center,
            child: key == '⌫'
                ? const Icon(Icons.backspace_outlined, color: Colors.white70, size: 20)
                : Text(
                    key,
                    style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w400),
                  ),
          ),
        );
      }).toList(),
    );
  }
}