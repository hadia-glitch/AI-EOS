import 'package:connectivity_plus/connectivity_plus.dart';

/// Thin wrapper around connectivity_plus. Only tells you whether the device
/// currently has *some* network interface up (WiFi/mobile) — it does not
/// guarantee Supabase itself is reachable (use ApiClient.isBackendAvailable()
/// or a lightweight Supabase call for that). Good enough to decide whether
/// to attempt a live signup/sync or fall back to local draft mode.
class ConnectivityService {
  ConnectivityService._();
  static final ConnectivityService instance = ConnectivityService._();

  Future<bool> isOnline() async {
    try {
      final results = await Connectivity().checkConnectivity();
      return results.any((r) => r != ConnectivityResult.none);
    } catch (_) {
      // If the plugin itself fails for any reason, don't block the user —
      // assume offline and let the app degrade gracefully rather than crash.
      return false;
    }
  }

  /// True only when on WiFi specifically — used to gate the bulk guideline
  /// PDF pre-download (see OfflinePdfCache.syncAllPdfs) so a clinician on
  /// mobile data doesn't unexpectedly burn several MB in the background.
  /// Mobile-data downloads still happen lazily, one PDF at a time, whenever
  /// the clinician actually opens a source (see OfflinePdfCache.getLocalPath).
  Future<bool> isOnWifi() async {
    try {
      final results = await Connectivity().checkConnectivity();
      return results.contains(ConnectivityResult.wifi);
    } catch (_) {
      return false;
    }
  }
}