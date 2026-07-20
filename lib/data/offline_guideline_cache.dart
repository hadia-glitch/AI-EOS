import 'dart:convert';
import 'dart:developer' as developer;
import 'package:shared_preferences/shared_preferences.dart';
import 'api/api_client.dart';

/// Local mirror of backend's guideline_knowledge_cache table
/// (rag/guideline_cache.py) — a small (num_guidelines x 4 categories) set
/// of pre-synthesized, evidence-based protocol templates. Synced whenever
/// the backend is reachable; read from whenever it isn't.
///
/// This is deliberately NOT the same thing as caching individual AI
/// explanations — those are per-patient and effectively never repeat, so
/// they can't be usefully synced ahead of time. This table is small and
/// bounded, so a full local copy is always available offline, and it picks
/// up newly-uploaded local/institutional guidelines the next time the app
/// is online after an admin uploads one (see admin_screens.dart
/// GuidelineConfigScreen and backend rag/guideline_cache.py rebuild hook).
class OfflineGuidelineCache {
  static const _dataKey = 'guideline_cache_v1';
  static const _lastSyncKey = 'guideline_cache_last_sync_v1';

  /// Pulls any new/updated rows from the backend and merges them into local
  /// storage. Safe to call often (e.g. on every successful backend health
  /// check) — it's a small incremental GET when already synced. Never
  /// throws; sync failures are logged and simply leave the existing local
  /// cache untouched.
  static Future<void> sync({ApiClient? client}) async {
    final c = client ?? apiClient;
    try {
      final available = await c.isBackendAvailable();
      if (!available) return;

      final prefs = await SharedPreferences.getInstance();
      final lastSync = prefs.getString(_lastSyncKey);

      final response = await c.dio.get(
        '/api/v1/guideline-cache/sync',
        queryParameters: lastSync != null ? {'since': lastSync} : null,
      );
      final data = response.data as Map<String, dynamic>;
      final rows = (data['rows'] as List<dynamic>? ?? []);

      if (rows.isNotEmpty) {
        final existing = await _loadRaw(prefs);
        for (final row in rows) {
          final r = row as Map<String, dynamic>;
          final source = (r['guideline_source'] as String? ?? '').toUpperCase();
          final category = (r['risk_category'] as String? ?? '').toUpperCase();
          if (source.isEmpty || category.isEmpty) continue;
          existing['${source}_$category'] = r;
        }
        await prefs.setString(_dataKey, jsonEncode(existing));
      }

      final syncedAt = data['synced_at'] as String? ?? DateTime.now().toUtc().toIso8601String();
      await prefs.setString(_lastSyncKey, syncedAt);
      developer.log(
        '[OfflineGuidelineCache] Synced ${rows.length} updated row(s), total cached=${(await _loadRaw(prefs)).length}',
        name: 'OfflineGuidelineCache',
      );
    } catch (e, st) {
      developer.log('[OfflineGuidelineCache] Sync failed (non-fatal): $e',
          name: 'OfflineGuidelineCache', error: e, stackTrace: st);
    }
  }

  static Future<Map<String, dynamic>> _loadRaw(SharedPreferences prefs) async {
    final s = prefs.getString(_dataKey);
    if (s == null || s.isEmpty) return {};
    try {
      return jsonDecode(s) as Map<String, dynamic>;
    } catch (_) {
      return {};
    }
  }

  /// Returns the cached `synthesized_plan` JSON for (guideline, category),
  /// or null if that combination has never been synced (e.g. first app
  /// launch with no connectivity yet, or a local guideline that hasn't
  /// finished its first synthesis on the backend).
  static Future<Map<String, dynamic>?> getProtocol(String guideline, String category) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = await _loadRaw(prefs);
    final key = '${guideline.toUpperCase()}_${category.toUpperCase()}';
    final row = raw[key] as Map<String, dynamic>?;
    if (row == null) return null;
    return row['synthesized_plan'] as Map<String, dynamic>?;
  }

  static Future<DateTime?> lastSynced() async {
    final prefs = await SharedPreferences.getInstance();
    final s = prefs.getString(_lastSyncKey);
    return s != null ? DateTime.tryParse(s) : null;
  }

  static Future<bool> hasAnyCache() async {
    final prefs = await SharedPreferences.getInstance();
    return (await _loadRaw(prefs)).isNotEmpty;
  }

  /// Which (guideline, category) pairs are currently cached — useful for a
  /// settings-screen diagnostic ("12/12 protocols cached for offline use").
  static Future<List<String>> cachedKeys() async {
    final prefs = await SharedPreferences.getInstance();
    return (await _loadRaw(prefs)).keys.toList()..sort();
  }
}