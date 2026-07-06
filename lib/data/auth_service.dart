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
  // CRITICAL FIX: these keys previously were global constants
  // ('local_unlock_pin', 'biometric_unlock_enabled') shared across every
  // account that ever signed in on the device. That meant a brand-new
  // account would inherit whatever PIN the *previous* test account had set
  // on the same emulator, and skip straight to PIN entry instead of setup.
  // Scoping every key by the Supabase user id fixes this at the root.
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
        return res as Map<String, dynamic>;
      }
    } catch (e) {
      debugPrint('Failed to load user settings: $e');
    }
    return null;
  }

  /// Authoritative check for "has THIS user completed first-login setup
  /// anywhere, ever?" Always checked against Supabase (users.pin_hash), never
  /// against local device storage — local storage can be stale or can belong
  /// to a different account that previously used this device.
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
    // Update last_login timestamp — non-fatal if it fails
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
  /// Deliberately does NOT set pin_hash here — an absent pin_hash is what
  /// marks the account as "setup not completed" for routing purposes.
  Future<AuthResponse> signUp({
    required String email,
    required String password,
  }) async {
    final response = await client.auth.signUp(email: email, password: password);
    final userId = response.user?.id;
    if (userId != null) {
      try {
        await client.from('users').upsert({
          'id': userId,
          'role': 'nurse',
        });
      } catch (e) {
        // Non-fatal: users row can be created later in setup
        debugPrint('Could not insert users row: $e');
      }
    }
    return response;
  }

  Future<void> signOut() async {
    await client.auth.signOut();
    // NOTE: we intentionally do NOT wipe secure storage here. Every key is
    // scoped by user id (see _pinHashKey/_biometricKey above), so leftover
    // entries from this account never leak into a different account that
    // later signs in on the same device.
  }

  /// Resend the confirmation email for an unconfirmed account.
  Future<void> resendConfirmation({required String email}) async {
    await client.auth.resend(
      type: OtpType.signup,
      email: email,
    );
  }

  /// Caches a hash of the PIN locally, scoped to the current user, for fast
  /// offline unlock. Writing pin_hash to Supabase itself is done by the
  /// caller (first-login setup screen) as part of its single combined
  /// upsert with role/institution/guideline — this method only maintains
  /// the local per-device cache.
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

  /// Whether THIS user has a locally-cached PIN on THIS device.
  /// Used only to decide "fast local unlock" vs "first time on this device,
  /// verify against the DB hash instead" — never used to decide whether
  /// setup itself is complete (see hasCompletedSetup).
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
}