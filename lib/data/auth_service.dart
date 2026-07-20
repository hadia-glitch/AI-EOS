import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class AuthService {
  AuthService._();

  static final AuthService instance = AuthService._();
  static const _storage = FlutterSecureStorage();

  SupabaseClient get client => Supabase.instance.client;
  User? get currentUser => client.auth.currentUser;
  bool get isSignedIn => currentUser != null;

  // ── User-scoped local storage keys ──────────────────────────────────────
  // Every key here is scoped by user id so that a PIN cached on this device
  // for one account can never leak into a different account that later
  // signs in on the same device/emulator.
  String _pinHashKey(String userId) => 'local_pin_hash_$userId';
  String _biometricKey(String userId) => 'biometric_unlock_enabled_$userId';

  String _hashPin(String pin) => sha256.convert(utf8.encode(pin)).toString();

  Future<Map<String, dynamic>?> loadUserSettings() async {
    if (!isSignedIn) return null;
    try {
      final res = await client
          .from('users')
          .select()
          .eq('id', currentUser!.id)
          .maybeSingle();
      if (res != null) {
        final prefs = await SharedPreferences.getInstance();
        if (res['default_guideline'] != null) {
          await prefs.setString('active_guideline', res['default_guideline'] as String);
        }
        if (res['role'] != null) {
          await prefs.setString('user_role', res['role'] as String);
        }
        return res;
      }
    } catch (e) {
      debugPrint('Failed to load user settings: $e');
    }
    return null;
  }

  /// Authoritative check for "has THIS user completed first-login setup
  /// anywhere, ever?" Always checked against Supabase (users.pin_hash).
  Future<bool> hasCompletedSetup() async {
    if (!isSignedIn) return false;
    try {
      final res = await client
          .from('users')
          .select('pin_hash')
          .eq('id', currentUser!.id)
          .maybeSingle();
      final hash = res?['pin_hash'] as String?;
      return hash != null && hash.isNotEmpty;
    } catch (e) {
      debugPrint('hasCompletedSetup check failed: $e');
      return false;
    }
  }

  Future<AuthResponse> signIn({
    required String email,
    required String password,
  }) async {
    final response = await client.auth.signInWithPassword(
      email: email,
      password: password,
    );
    final userId = response.user?.id;
    if (userId != null) {
      try {
        await client.from('users').upsert({
          'id': userId,
          'last_login': DateTime.now().toUtc().toIso8601String(),
        });
      } catch (e) {
        debugPrint('Could not update last_login: $e');
      }
    }
    return response;
  }

  /// Signs up and immediately inserts a row into the public users table.
  /// fullName/phone are optional profile fields (see migration
  /// 007_users_profile_fields.sql) used by the new signup flow.
  /// Deliberately does NOT set pin_hash here — an absent pin_hash is what
  /// marks the account as "setup not completed" for routing purposes.
  Future<AuthResponse> signUp({
    required String email,
    required String password,
    String? fullName,
    String? phone,
  }) async {
    final response = await client.auth.signUp(
      email: email,
      password: password,
      data: {
        if (fullName != null && fullName.isNotEmpty) 'full_name': fullName,
        if (phone != null && phone.isNotEmpty) 'phone': phone,
      },
    );
    final userId = response.user?.id;
    if (userId != null) {
      try {
        await client.from('users').upsert({
          'id': userId,
          'role': 'nurse',
          if (fullName != null && fullName.isNotEmpty) 'full_name': fullName,
          if (phone != null && phone.isNotEmpty) 'phone': phone,
        });
      } catch (e) {
        // Non-fatal in the sense that we don't block the UI, but this is
        // exactly the kind of failure that silently breaks first-login
        // setup later (see 006_users_rls_policies.sql). Surfacing it
        // clearly here helps catch RLS/FK problems immediately on signup.
        debugPrint('Could not insert users row: $e');
      }
    }
    return response;
  }

  Future<void> signOut() async {
    await client.auth.signOut();
  }

  /// Resend the confirmation email for an unconfirmed account.
  Future<void> resendConfirmation({required String email}) async {
    await client.auth.resend(
      type: OtpType.signup,
      email: email,
    );
  }

  // ── PIN unlock ───────────────────────────────────────────────────────────

  Future<void> saveLocalUnlock({
    required String pin,
    required bool biometricEnabled,
  }) async {
    final userId = currentUser?.id;
    if (userId == null) {
      throw StateError('Cannot save PIN: no signed-in user');
    }
    await _storage.write(key: _pinHashKey(userId), value: _hashPin(pin));
    await _storage.write(
      key: _biometricKey(userId),
      value: biometricEnabled ? 'true' : 'false',
    );
  }

  Future<bool> hasLocalUnlock() async {
    final userId = currentUser?.id;
    if (userId == null) return false;
    final hash = await _storage.read(key: _pinHashKey(userId));
    return hash != null && hash.isNotEmpty;
  }

  Future<bool> verifyPin(String pin) async {
    final userId = currentUser?.id;
    if (userId == null) return false;
    final saved = await _storage.read(key: _pinHashKey(userId));
    return saved != null && saved == _hashPin(pin);
  }

  Future<bool> isBiometricEnabled() async {
    final userId = currentUser?.id;
    if (userId == null) return false;
    return await _storage.read(key: _biometricKey(userId)) == 'true';
  }

  Future<bool> canUseBiometrics() async {
    if (kIsWeb) return false;
    try {
      final auth = LocalAuthentication();
      return await auth.isDeviceSupported() && await auth.canCheckBiometrics;
    } catch (_) {
      return false;
    }
  }

  Future<bool> unlockWithBiometrics() async {
    if (!await isBiometricEnabled() || !await canUseBiometrics()) return false;
    try {
      return LocalAuthentication().authenticate(
        localizedReason: 'Unlock NeoGuard AI',
        options: const AuthenticationOptions(
          biometricOnly: false,
          stickyAuth: true,
        ),
      );
    } catch (_) {
      return false;
    }
  }

  // ── Multi-factor authentication (TOTP, optional) ────────────────────────
  // Uses Supabase's built-in MFA API — no custom backend needed. Enrollment
  // and verification both require connectivity, so this is only ever
  // offered once a user is fully online and past first-login setup.

  /// Starts TOTP enrollment. Returns the factor id and an otpauth:// URI the
  /// user can add to any authenticator app (Google Authenticator, Authy,
  /// etc). Call confirmMfaEnrollment() with a code from that app to finish.
  Future<({String factorId, String otpauthUri, String secret})> beginMfaEnrollment() async {
    final res = await client.auth.mfa.enroll(factorType: FactorType.totp);
    final totp = res.totp;
    if (totp == null) {
      throw StateError('Supabase did not return TOTP enrollment details.');
    }
    return (
      factorId: res.id,
      otpauthUri: totp.uri,
      secret: totp.secret,
    );
  }

  /// Verifies the 6-digit code from the authenticator app and activates
  /// the factor.
  Future<void> confirmMfaEnrollment({
    required String factorId,
    required String code,
  }) async {
    final challenge = await client.auth.mfa.challenge(factorId: factorId);
    await client.auth.mfa.verify(
      factorId: factorId,
      challengeId: challenge.id,
      code: code,
    );
  }

  Future<bool> isMfaEnabled() async {
    try {
      final factors = await client.auth.mfa.listFactors();
      return factors.totp.any((f) => f.status == FactorStatus.verified);
    } catch (e) {
      debugPrint('Could not list MFA factors: $e');
      return false;
    }
  }

  Future<String?> primaryVerifiedTotpFactorId() async {
    try {
      final factors = await client.auth.mfa.listFactors();
      final verified = factors.totp.where((f) => f.status == FactorStatus.verified);
      return verified.isEmpty ? null : verified.first.id;
    } catch (_) {
      return null;
    }
  }

  /// After signInWithPassword succeeds, call this to check whether the
  /// session still needs a second-factor challenge before it's fully
  /// trusted (aal2). Returns false if the account has no MFA factor.
  Future<bool> needsMfaChallenge() async {
    try {
      final level = client.auth.mfa.getAuthenticatorAssuranceLevel();
      return level.currentLevel == AuthenticatorAssuranceLevels.aal1 &&
          level.nextLevel == AuthenticatorAssuranceLevels.aal2;
    } catch (e) {
      debugPrint('Could not read MFA assurance level: $e');
      return false;
    }
  }

  Future<void> verifyMfaChallenge({
    required String factorId,
    required String code,
  }) async {
    final challenge = await client.auth.mfa.challenge(factorId: factorId);
    await client.auth.mfa.verify(
      factorId: factorId,
      challengeId: challenge.id,
      code: code,
    );
  }
}