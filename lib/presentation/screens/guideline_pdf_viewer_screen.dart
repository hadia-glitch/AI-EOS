import 'package:flutter/material.dart';
import '../../core/theme.dart';

/// Screen 08 — Guideline PDF Viewer (placeholder for bundled PDFs)
class GuidelinePdfViewerScreen extends StatelessWidget {
  final String documentName;

  const GuidelinePdfViewerScreen({super.key, required this.documentName});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(documentName, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(icon: const Icon(Icons.search), onPressed: () {}),
          IconButton(icon: const Icon(Icons.bookmark_border), onPressed: () {}),
        ],
      ),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.picture_as_pdf, size: 64, color: WhoTheme.primaryNavy),
            const SizedBox(height: 16),
            Text('PDF: $documentName'),
            const SizedBox(height: 8),
            Text('Open from Supabase Storage when configured', style: TextStyle(color: Colors.grey.shade600)),
          ],
        ),
      ),
    );
  }
}
