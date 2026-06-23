import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

class ApiClient {
  static const String _defaultBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://127.0.0.1:8000',
  );

  late final Dio _dio;
  final String baseUrl;

  ApiClient({String? baseUrl})
      : baseUrl = baseUrl ?? _defaultBaseUrl {
    _dio = Dio(
      BaseOptions(
        baseUrl: this.baseUrl,
        connectTimeout: const Duration(seconds: 8),
        receiveTimeout: const Duration(seconds: 30),
        headers: {'Content-Type': 'application/json'},
      ),
    );
  }

  Dio get dio => _dio;

  Future<bool> isBackendAvailable() async {
    try {
      final response = await _dio.get('/health');
      return response.statusCode == 200;
    } catch (e) {
      debugPrint('Backend unavailable: $e');
      return false;
    }
  }
}

final apiClient = ApiClient();
