import 'dart:io';
import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import '../../data/offline_pdf_cache.dart';

/// Screen 08 — Guideline PDF Viewer.
///
/// Opens a guideline PDF at a specific page and, when [searchText] is
/// provided (the "exact excerpt" from a RAG chunk), highlights every match
/// of that text on the page — this is what makes "click the excerpt, land
/// on the exact paragraph" work, without needing per-character offsets
/// from ingestion: Syncfusion's built-in text search does the highlighting
/// against the real PDF content at render time.
///
/// Works fully offline once the PDF has been opened at least once online
/// (see lib/data/offline_pdf_cache.dart — downloads once, caches locally).
/// If it has never been cached and there's no connectivity, shows a clear
/// "connect once to enable offline viewing" message instead of a blank
/// screen or crash.
class GuidelinePdfViewerScreen extends StatefulWidget {
  /// The PDF's file name as stored in guideline_documents.name /
  /// evidence_chunks.metadata.file_name — used as the cache key and to
  /// look up the Storage URL.
  final String fileName;

  /// Human-readable title for the app bar (e.g. "NICE NG195").
  final String documentName;

  /// 1-indexed page to jump to on open. Falls back to page 1 if null/0.
  final int? pageNumber;

  /// The exact chunk text to search for and highlight on that page.
  final String? searchText;

  const GuidelinePdfViewerScreen({
    super.key,
    required this.fileName,
    required this.documentName,
    this.pageNumber,
    this.searchText,
  });

  @override
  State<GuidelinePdfViewerScreen> createState() => _GuidelinePdfViewerScreenState();
}

class _GuidelinePdfViewerScreenState extends State<GuidelinePdfViewerScreen> {
  final PdfViewerController _controller = PdfViewerController();
  PdfTextSearchResult? _searchResult;

  String? _localPath;
  bool _loading = true;
  double _downloadProgress = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _downloadProgress = 0;
    });

    final alreadyCached = await OfflinePdfCache.isCached(widget.fileName);

    final path = await OfflinePdfCache.getLocalPath(
      widget.fileName,
      onProgress: (p) {
        if (mounted) setState(() => _downloadProgress = p);
      },
    );

    if (!mounted) return;

    if (path == null) {
      setState(() {
        _loading = false;
        _error = alreadyCached
            ? 'Could not open this document.'
            : 'This guideline hasn\'t been downloaded to this device yet, and there\'s no '
                'connection right now. Open it once while online to enable offline viewing.';
      });
      return;
    }

    setState(() {
      _localPath = path;
      _loading = false;
    });
  }

  void _onDocumentLoaded(PdfDocumentLoadedDetails details) {
    final targetPage = (widget.pageNumber ?? 1).clamp(1, details.document.pages.count);
    // Jump first, then search — searching before navigation can otherwise
    // scroll to the FIRST match in the document rather than the match on
    // the page we already know is correct from the chunk's page_number.
    _controller.jumpToPage(targetPage);

    final query = widget.searchText;
    if (query != null && query.trim().isNotEmpty) {
      // Search on a short, distinctive slice — full excerpts can be long
      // enough that exact whole-string matching fails on PDF text-extraction
      // quirks (hyphenation, spacing). First ~80 chars is usually enough to
      // be unique on a single page while staying robust to that.
      final trimmed = query.trim();
      final snippet = trimmed.length > 80 ? trimmed.substring(0, 80) : trimmed;
      Future.delayed(const Duration(milliseconds: 300), () {
        if (!mounted) return;
        final result = _controller.searchText(snippet);
        setState(() => _searchResult = result);
      });
    }
  }

  @override
  void dispose() {
    _searchResult?.clear();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.documentName, overflow: TextOverflow.ellipsis),
        actions: [
          if (_localPath != null) ...[
            IconButton(
              icon: const Icon(Icons.chevron_left),
              tooltip: 'Previous match',
              onPressed: () => _searchResult?.previousInstance(),
            ),
            IconButton(
              icon: const Icon(Icons.chevron_right),
              tooltip: 'Next match',
              onPressed: () => _searchResult?.nextInstance(),
            ),
          ],
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (_downloadProgress > 0 && _downloadProgress < 1) ...[
              SizedBox(
                width: 120,
                child: LinearProgressIndicator(value: _downloadProgress),
              ),
              const SizedBox(height: 12),
              Text('Downloading for offline viewing… ${(_downloadProgress * 100).round()}%',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
            ] else
              const CircularProgressIndicator(),
          ],
        ),
      );
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.cloud_off, size: 48, color: Colors.grey.shade400),
              const SizedBox(height: 16),
              Text(_error!, textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.grey.shade700)),
              const SizedBox(height: 20),
              OutlinedButton.icon(
                onPressed: _load,
                icon: const Icon(Icons.refresh),
                label: const Text('Try again'),
              ),
            ],
          ),
        ),
      );
    }

    return SfPdfViewer.file(
      File(_localPath!),
      controller: _controller,
      onDocumentLoaded: _onDocumentLoaded,
      canShowScrollHead: true,
      canShowScrollStatus: true,
    );
  }
}