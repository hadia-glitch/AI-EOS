import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:eos/presentation/home_dashboard.dart';

void main() {
  testWidgets('Home dashboard smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: HomeDashboard()),
      ),
    );

    expect(find.text('NEOGUARD AI'), findsOneWidget);
    expect(find.text('Home'), findsOneWidget);
    expect(find.text('Evidence'), findsOneWidget);
  });
}
