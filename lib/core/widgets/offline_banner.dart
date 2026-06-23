import 'package:flutter/material.dart';
import '../theme.dart';

class OfflineBanner extends StatelessWidget {
  final String message;

  const OfflineBanner({super.key, this.message = 'Offline — showing cached data'});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: WhoTheme.riskIntermediate,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: [
          const Icon(Icons.cloud_off, color: Colors.white, size: 18),
          const SizedBox(width: 8),
          Expanded(child: Text(message, style: const TextStyle(color: Colors.white, fontSize: 12))),
        ],
      ),
    );
  }
}
