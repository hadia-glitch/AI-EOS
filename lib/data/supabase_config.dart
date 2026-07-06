import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

class SupabaseRuntimeConfig {
  const SupabaseRuntimeConfig({
    required this.url,
    required this.anonKey,
  });

  final String url;
  final String anonKey;

  bool get isConfigured => url.isNotEmpty && anonKey.isNotEmpty;
}

class SupabaseConfig {
  static SupabaseRuntimeConfig? current;

  /// Resolution order:
  ///   1. Values from the local .env file (SUPABASE_URL / SUPABASE_ANON_KEY)
  ///      — this is now the primary source, replacing --dart-define.
  ///   2. Fallback: ask the backend's /api/v1/mobile/config endpoint
  ///      (kept for setups that provision config from the backend instead
  ///      of shipping it in the mobile .env).
  static Future<SupabaseRuntimeConfig?> load() async {
    final envUrl = dotenv.env['SUPABASE_URL']?.trim() ?? '';
    final envAnonKey = dotenv.env['SUPABASE_ANON_KEY']?.trim() ?? '';

    if (envUrl.isNotEmpty && envAnonKey.isNotEmpty) {
      current = SupabaseRuntimeConfig(url: envUrl, anonKey: envAnonKey);
      return current;
    }

    debugPrint('.env is missing SUPABASE_URL/SUPABASE_ANON_KEY — falling back to backend config endpoint');

    final apiBaseUrl = dotenv.env['API_BASE_URL']?.trim().isNotEmpty == true
        ? dotenv.env['API_BASE_URL']!.trim()
        : 'http://127.0.0.1:8000';

    try {
      final dio = Dio(
        BaseOptions(
          baseUrl: apiBaseUrl,
          connectTimeout: const Duration(seconds: 5),
          receiveTimeout: const Duration(seconds: 10),
        ),
      );
      final response = await dio.get('/api/v1/mobile/config');
      final data = response.data as Map<String, dynamic>;
      current = SupabaseRuntimeConfig(
        url: data['supabase_url'] as String? ?? '',
        anonKey: data['supabase_anon_key'] as String? ?? '',
      );
      return current;
    } catch (e) {
      debugPrint('Unable to load Supabase config from .env or backend: $e');
      current = null;
      return null;
    }
  }

  static bool get isConfigured => current?.isConfigured ?? false;
}