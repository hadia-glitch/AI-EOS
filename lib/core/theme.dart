import 'package:flutter/material.dart';

class WhoTheme {
  // WHO Color Palette
  static const Color primaryNavy = Color(0xFF1B3A5C);
  static const Color secondaryTeal = Color(0xFF0D9488);
  static const Color neutralLightGrey = Color(0xFFF3F4F6);
  static const Color neutralDarkGrey = Color(0xFF374151);
  static const Color pureWhite = Color(0xFFFFFFFF);
  static const Color lightBlueBackground = Color(0xFFE0F2FE);

  // Risk Classification Colors
  static const Color riskLow = Color(0xFF10B981);       // Green
  static const Color riskIntermediate = Color(0xFFF59E0B); // Amber
  static const Color riskHigh = Color(0xFFF97316);         // Orange
  static const Color riskCritical = Color(0xFFEF4444);     // Red

  static ThemeData get lightTheme {
    return ThemeData(
      useMaterial3: true,
      primaryColor: primaryNavy,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primaryNavy,
        primary: primaryNavy,
        secondary: secondaryTeal,
        background: neutralLightGrey,
        surface: pureWhite,
      ),
      scaffoldBackgroundColor: neutralLightGrey,
      cardTheme: CardThemeData(
        color: pureWhite,
        elevation: 2,
        shadowColor: Colors.black.withOpacity(0.05),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: primaryNavy,
        foregroundColor: pureWhite,
        elevation: 0,
        centerTitle: true,
        titleTextStyle: TextStyle(
          fontSize: 20,
          fontWeight: FontWeight.bold,
          color: pureWhite,
        ),
      ),
      textTheme: const TextTheme(
        headlineLarge: TextStyle(
          fontSize: 28,
          fontWeight: FontWeight.bold,
          color: primaryNavy,
        ),
        headlineMedium: TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.bold,
          color: primaryNavy,
        ),
        titleLarge: TextStyle(
          fontSize: 18,
          fontWeight: FontWeight.bold,
          color: primaryNavy,
        ),
        titleMedium: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w600,
          color: neutralDarkGrey,
        ),
        bodyLarge: TextStyle(
          fontSize: 16,
          color: neutralDarkGrey,
        ),
        bodyMedium: TextStyle(
          fontSize: 14,
          color: neutralDarkGrey,
        ),
        labelLarge: TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.bold,
          color: primaryNavy,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: primaryNavy,
          foregroundColor: pureWhite,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.bold,
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: pureWhite,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: Colors.grey.shade300),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: Colors.grey.shade200),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: primaryNavy, width: 2),
        ),
        labelStyle: TextStyle(color: Colors.grey.shade600),
      ),
    );
  }
}
