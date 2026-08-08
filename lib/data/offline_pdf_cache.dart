import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'api/api_client.dart';
import 'connectivity_service.dart';

/// Downloads guideline PDFs from Supabase Storage and caches them in the
/// app's local documents directory, so the "jump to source page" feature
/// (see guideline_pdf_viewer_screen.dart) works fully offline.
///
/// Two paths, both feeding the same local cache:
///   - [syncAllPdfs]: proactive bulk pre-download of every guideline PDF,
///     called at app boot (see splash_screen.dart). WiFi-gated by default —
///     a clinician should never be surprised by mobile data usage from a
///     background sync they didn't ask for. This is what makes offline PDF
///     viewing work WITHOUT ever having opened a document online first.
///   - [getLocalPath]: lazy per-document fallback, used directly by the
///     viewer. If [syncAllPdfs] already cached the file this is instant; if
///     bulk sync hasn't run yet (e.g. first launch was on mobile data) or
///     missed a newly-added document, this still downloads it on demand.
class OfflinePdfCache {
  static const _documentListKey = 'guideline_documents_v1';
  static const _cacheDirName = 'guideline_pdfs';

  // ── Document list (metadata only — cheap, synced eagerly) ────────────────

  static Future<void> syncDocumentList({ApiClient? client}) async {
    final c = client ?? apiClient;
    try {
      final available = await c.isBackendAvailable();
      if (!available) return;

      final response = await c.dio.get('/api/v1/guidelines/documents');
      final data = response.data as Map<String, dynamic>;
      final docs = data['documents'] as List<dynamic>? ?? [];

      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_documentListKey, jsonEncode(docs));
      developer.log('[OfflinePdfCache] Synced document list: ${docs.length} guideline(s)',
          name: 'OfflinePdfCache');
    } catch (e) {
      developer.log('[OfflinePdfCache] Document list sync failed (non-fatal): $e',
          name: 'OfflinePdfCache');
    }
  }

  static Future<Map<String, dynamic>?> _findDocument(
    String fileName, {
    ApiClient? client,
    bool allowSync = true,
  }) async {
    Map<String, dynamic>? searchRaw(String? raw) {
      if (raw == null) return null;
      try {
        final docs = jsonDecode(raw) as List<dynamic>;
        for (final d in docs) {
          final doc = d as Map<String, dynamic>;
          if (doc['name'] == fileName) return doc;
        }
      } catch (_) {}
      return null;
    }

    final prefs = await SharedPreferences.getInstance();
    final found = searchRaw(prefs.getString(_documentListKey));
    if (found != null || !allowSync) return found;

    // The document list (fileName -> storage_url) is metadata only — a few
    // KB of JSON, not PDF bytes — but it was previously ONLY ever synced
    // from inside syncAllPdfs(), AFTER that method's WiFi gate. A device
    // that has only ever been online on mobile data (or where
    // ConnectivityService.isOnWifi() misdetects — this happens on some
    // Android versions without location permission) would therefore never
    // learn ANY document's storage_url, and getLocalPath below would
    // always return null and the viewer would misreport "no connection"
    // even while genuinely online. Sync just the metadata here, on demand,
    // regardless of connection type, and retry once — this is the "lazy
    // per-document fallback" this class's docstring already promises.
    developer.log(
        '[OfflinePdfCache] "$fileName" not in local document list — syncing metadata on demand',
        name: 'OfflinePdfCache');
    await syncDocumentList(client: client);
    final refreshedPrefs = await SharedPreferences.getInstance();
    return searchRaw(refreshedPrefs.getString(_documentListKey));
  }

  // ── Bulk pre-download (proactive — makes offline viewing "just work") ────

  /// Downloads every guideline PDF not already cached, so offline viewing
  /// works without the clinician ever having opened a document online
  /// first. Safe to call often (e.g. every app boot) — already-cached files
  /// are skipped instantly via [isCached], so a repeat call after the first
  /// successful sync is nearly free.
  ///
  /// [wifiOnly] defaults to true: guideline corpora can be tens of MB total
  /// (this app's is ~9 PDFs, one over 300 pages), and silently spending a
  /// clinician's mobile data in the background is the kind of thing that
  /// erodes trust in an app fast. Set false to force a sync regardless of
  /// connection type (e.g. from an explicit "Download for offline use"
  /// button in Settings, where the user has opted in).
  static Future<void> syncAllPdfs({
    ApiClient? client,
    bool wifiOnly = true,
    void Function(int completed, int total)? onProgress,
  }) async {
    final c = client ?? apiClient;
    try {
      final available = await c.isBackendAvailable();
      if (!available) return;

      // Document-list metadata (fileName -> storage_url, a few KB of JSON)
      // is cheap enough to sync regardless of connection type -- only the
      // actual PDF bytes below are WiFi-gated. This also means
      // getLocalPath's on-demand fallback (see _findDocument) almost never
      // needs its own extra round trip, since the list is normally already
      // fresh by the time anyone opens a document.
      await syncDocumentList(client: c);

      if (wifiOnly && !await ConnectivityService.instance.isOnWifi()) {
        developer.log('[OfflinePdfCache] Skipping bulk PDF prefetch — not on WiFi '
            '(will still cache lazily per-document on any connection)',
            name: 'OfflinePdfCache');
        return;
      }

      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_documentListKey);
      if (raw == null) return;
      final docs = (jsonDecode(raw) as List<dynamic>).cast<Map<String, dynamic>>();

      int completed = 0;
      for (final doc in docs) {
        final fileName = doc['name'] as String? ?? '';
        if (fileName.isEmpty) {
          completed++;
          continue;
        }
        // Sequential, not parallel: keeps memory bounded (some of these PDFs
        // are 300+ pages) and gives predictable, readable progress.
        if (!await isCached(fileName)) {
          await getLocalPath(fileName, client: c);
        }
        completed++;
        onProgress?.call(completed, docs.length);
      }
      developer.log(
          '[OfflinePdfCache] Bulk prefetch complete: $completed/${docs.length} guideline PDF(s) cached',
          name: 'OfflinePdfCache');
    } catch (e) {
      developer.log('[OfflinePdfCache] Bulk prefetch failed (non-fatal): $e',
          name: 'OfflinePdfCache');
    }
  }

  // ── PDF bytes (per-document — used by the bulk sync above and directly ───
  // ── by the viewer as a lazy fallback) ─────────────────────────────────────

  /// Returns a local file path for [fileName], downloading it from Supabase
  /// Storage first if not already cached. Returns null if:
  ///  - the document is genuinely unknown even after an on-demand metadata
  ///    sync (e.g. never uploaded to Storage, or a fileName typo/mismatch
  ///    against guideline_documents.name), or
  ///  - the backend/storage itself is unreachable right now.
  /// [onProgress] receives 0.0-1.0 during download (ignored if already cached).
  static Future<String?> getLocalPath(
    String fileName, {
    void Function(double progress)? onProgress,
    ApiClient? client,
  }) async {
    if (fileName.isEmpty) return null;
    final c = client ?? apiClient;

    final dir = await getApplicationDocumentsDirectory();
    final cacheDir = Directory('${dir.path}/$_cacheDirName');
    if (!await cacheDir.exists()) await cacheDir.create(recursive: true);
    final localFile = File('${cacheDir.path}/$fileName');

    if (await localFile.exists() && await localFile.length() > 0) {
      return localFile.path;
    }

    final doc = await _findDocument(fileName, client: c);
    final storageUrl = doc?['storage_url'] as String?;
    if (storageUrl == null || storageUrl.isEmpty) {
      developer.log('[OfflinePdfCache] No storage_url for "$fileName" even after an '
          'on-demand metadata sync — either the backend is unreachable right now, or '
          'this file genuinely has no matching entry in guideline_documents (check for '
          'a fileName mismatch, or that it was actually uploaded to Storage).',
          name: 'OfflinePdfCache');
      return null;
    }

    try {
      final response = await c.dio.get<List<int>>(
        storageUrl,
        options: Options(responseType: ResponseType.bytes),
        onReceiveProgress: (received, total) {
          if (total > 0 && onProgress != null) onProgress(received / total);
        },
      );
      final bytes = response.data;
      if (bytes == null || bytes.isEmpty) return null;
      await localFile.writeAsBytes(bytes);
      developer.log('[OfflinePdfCache] Downloaded and cached $fileName (${bytes.length} bytes)',
          name: 'OfflinePdfCache');
      return localFile.path;
    } catch (e) {
      developer.log('[OfflinePdfCache] Download failed for $fileName: $e',
          name: 'OfflinePdfCache');
      return null;
    }
  }

  static Future<bool> isCached(String fileName) async {
    final dir = await getApplicationDocumentsDirectory();
    final f = File('${dir.path}/$_cacheDirName/$fileName');
    return f.existsSync() && f.lengthSync() > 0;
  }
}