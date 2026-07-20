import 'dart:developer' as developer;
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

class ApiClient {
  /// URL resolution order:
  ///   1. API_BASE_URL set in the .env file at the project root — set this
  ///      to your LAN IP for a physical device, e.g.
  ///      API_BASE_URL=http://192.168.x.x:8000
  ///   2. Android (emulator): http://10.0.2.2:8000
  ///   3. iOS simulator / desktop: http://127.0.0.1:8000
  static String get _resolvedBaseUrl {
    final defined = dotenv.env['API_BASE_URL']?.trim() ?? '';
    if (defined.isNotEmpty) return defined;
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000'; // emulator host alias only
    }
    return 'http://127.0.0.1:8000';
  }

  late final Dio _dio;
  final String baseUrl;

  ApiClient({String? baseUrl}) : baseUrl = baseUrl ?? _resolvedBaseUrl {
    _dio = Dio(
      BaseOptions(
        baseUrl: this.baseUrl,
        connectTimeout: const Duration(seconds: 10),
        // 1300s (~21.7 min), not 90s: matches the backend's
        // local_llm_timeout_seconds (config.py), currently 600s (10 min)
        // for TESTING purposes — 300s wasn't enough for a real care-plan
        // prompt on this CPU-only local model. Worst case for HIGH/CRITICAL
        // risk is two full local generations back-to-back in one request
        // (main generation + the fact-check judge, see rag/fact_check.py),
        // so this must cover ~20 min plus margin, not just one call.
        // THIS IS A DIAGNOSTIC VALUE, NOT A PRODUCTION ONE — once you have
        // a real measured generation time from a successful run, dial both
        // this and the backend value down to something reasonable (or move
        // to a GPU-backed local instance, or accept cloud fallback for
        // interactive use and reserve strict Local-only for batch/offline
        // paths where a 10+ minute wait is acceptable).
        // NOTE: if you're on the Android emulator (10.0.2.2), the
        // emulator's own virtual NAT can silently drop long-idle
        // connections well before this timeout ever fires — see
        // api_client.dart's baseUrl docs. Use `adb reverse tcp:8000
        // tcp:8000` + API_BASE_URL=http://127.0.0.1:8000, or test on a
        // physical device, or this timeout value won't matter at all.
        receiveTimeout: const Duration(seconds: 1300),
        headers: {'Content-Type': 'application/json'},
      ),
    );
    if (kDebugMode) {
      _dio.interceptors.add(
        LogInterceptor(
          requestBody: false,
          responseBody: false,
          logPrint: (o) => developer.log('$o', name: 'Dio'),
        ),
      );
    }
  }

  Dio get dio => _dio;

  Future<bool> isBackendAvailable() async {
    try {
      final response = await _dio.get(
        '/health',
        options: Options(
          sendTimeout: const Duration(seconds: 5),
          receiveTimeout: const Duration(seconds: 5),
        ),
      );
      final ok = response.statusCode == 200;
      developer.log('[ApiClient] health=$ok baseUrl=$baseUrl', name: 'ApiClient');
      return ok;
    } catch (e) {
      developer.log('[ApiClient] unreachable at $baseUrl — $e', name: 'ApiClient');
      return false;
    }
  }
}

final apiClient = ApiClient();