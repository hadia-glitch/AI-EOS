import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../core/theme.dart';
import '../../data/api/api_client.dart';

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  String _status = 'Checking connectivity…';

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    setState(() => _status = 'Loading clinical data…');
    await Future.delayed(const Duration(milliseconds: 800));
    final online = await apiClient.isBackendAvailable();
    setState(() => _status = online ? 'Ready' : 'Offline Mode');
    await Future.delayed(const Duration(milliseconds: 1200));
    if (mounted) {
      context.go('/home');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: WhoTheme.primaryNavy,
      body: SafeArea(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Spacer(flex: 2),
            Container(
              padding: const EdgeInsets.all(24),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: WhoTheme.secondaryTeal.withValues(alpha: 0.5), width: 3),
              ),
              child: const Icon(Icons.security, color: Colors.white, size: 64),
            ),
            const SizedBox(height: 24),
            const Text('NeoGuard AI', style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold)),
            const SizedBox(height: 32),
            Text(_status, style: TextStyle(color: Colors.grey.shade400, fontSize: 14)),
            const Spacer(flex: 2),
            Text('v1.0.0', style: TextStyle(color: Colors.grey.shade500, fontSize: 12)),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }
}
