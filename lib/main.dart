import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'core/theme.dart';
import 'presentation/router.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    const ProviderScope(
      child: NeoGuardApp(),
    ),
  );
}

class NeoGuardApp extends StatelessWidget {
  const NeoGuardApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'NeoGuard AI CDSS',
      debugShowCheckedModeBanner: false,
      theme: WhoTheme.lightTheme,
      routerConfig: appRouter,
    );
  }
}
