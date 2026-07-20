import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:uuid/uuid.dart';

/// A signup that was started offline: the person filled in name/email/phone/
/// password and agreed to terms, but we couldn't reach Supabase yet to
/// actually create the account. Kept encrypted on-device until connectivity
/// returns, at which point SplashScreen finishes real account creation and
/// clears this draft.
class PendingSignupDraft {
  final String draftId;
  final String fullName;
  final String email;
  final String? phone;
  final String password;
  final DateTime createdAt;

  const PendingSignupDraft({
    required this.draftId,
    required this.fullName,
    required this.email,
    required this.phone,
    required this.password,
    required this.createdAt,
  });

  Map<String, dynamic> toJson() => {
        'draftId': draftId,
        'fullName': fullName,
        'email': email,
        'phone': phone,
        'password': password,
        'createdAt': createdAt.toIso8601String(),
      };

  static PendingSignupDraft fromJson(Map<String, dynamic> json) => PendingSignupDraft(
        draftId: json['draftId'] as String,
        fullName: json['fullName'] as String,
        email: json['email'] as String,
        phone: json['phone'] as String?,
        password: json['password'] as String,
        createdAt: DateTime.parse(json['createdAt'] as String),
      );
}

class LocalDraftService {
  LocalDraftService._();
  static final LocalDraftService instance = LocalDraftService._();

  static const _storage = FlutterSecureStorage();
  static const _draftKey = 'pending_signup_draft_v1';

  /// Creates and persists a new draft. Only one pending draft is supported
  /// at a time per device — call hasPendingDraft() first if you need to
  /// guard against overwriting an existing one.
  Future<PendingSignupDraft> saveDraft({
    required String fullName,
    required String email,
    String? phone,
    required String password,
  }) async {
    final draft = PendingSignupDraft(
      draftId: const Uuid().v4(),
      fullName: fullName,
      email: email,
      phone: phone,
      password: password,
      createdAt: DateTime.now(),
    );
    await _storage.write(key: _draftKey, value: jsonEncode(draft.toJson()));
    return draft;
  }

  Future<PendingSignupDraft?> getDraft() async {
    final raw = await _storage.read(key: _draftKey);
    if (raw == null) return null;
    try {
      return PendingSignupDraft.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      // Corrupt draft — better to drop it than get stuck in a bad loop.
      await clearDraft();
      return null;
    }
  }

  Future<bool> hasPendingDraft() async => await getDraft() != null;

  /// Wipes the draft, including the plaintext password held in secure
  /// storage. Call this the moment the real Supabase account has been
  /// created/signed-in successfully and local data has been migrated.
  Future<void> clearDraft() async {
    await _storage.delete(key: _draftKey);
  }
}