import 'package:flutter/material.dart';
import '../theme.dart';

class SourceBadge extends StatelessWidget {
  final String source;

  const SourceBadge({super.key, required this.source});

  Color get _color {
    switch (source.toUpperCase()) {
      case 'NICE':
        return WhoTheme.primaryNavy;
      case 'AAP':
        return const Color(0xFF1D4ED8);
      case 'WHO':
        return WhoTheme.secondaryTeal;
      case 'EOSCAL':
        return WhoTheme.neutralDarkGrey;
      default:
        return WhoTheme.neutralDarkGrey;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(color: _color, borderRadius: BorderRadius.circular(6)),
      child: Text(source, style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.bold)),
    );
  }
}
