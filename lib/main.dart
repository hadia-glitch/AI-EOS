import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'core/theme.dart';
import 'data/supabase_config.dart';
import 'presentation/router.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Load environment variables from .env at the project root. This
  // replaces passing SUPABASE_URL / SUPABASE_ANON_KEY / API_BASE_URL via
  // --dart-define on every `flutter run`. See .env.example for the
  // required keys — copy it to `.env` and fill in your real values.
  await dotenv.load(fileName: '.env');

  final supabaseConfig = await SupabaseConfig.load();
  if (supabaseConfig != null && supabaseConfig.isConfigured) {
    await Supabase.initialize(
      url: supabaseConfig.url,
      publishableKey: supabaseConfig.anonKey,
    );
  }
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