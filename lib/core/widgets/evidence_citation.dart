import 'package:flutter/material.dart';
import '../../data/models/rag_chunk.dart';
import '../../presentation/screens/guideline_pdf_viewer_screen.dart';

/// Matches bracketed evidence labels like `[E1]`, `[E12]` inside prose.
final RegExp _evidenceLabelPattern = RegExp(r'\[(E\d+)\]');

/// Renders [text] as normal prose, except every `[E#]` token becomes a
/// small tappable/hoverable citation chip instead of plain bracketed text.
///
/// - On desktop/web (mouse present): hovering the chip pops open a small
///   scrollable card showing the source name, section, and the actual
///   retrieved excerpt for that label. The popup stays open for as long as
///   the pointer is over either the chip or the popup itself, so a longer
///   excerpt can be scrolled without it vanishing.
/// - On touch devices (no hover event ever fires): tapping the chip opens
///   the same content in a bottom sheet instead.
/// - Either way, when the underlying chunk has a known source PDF (see
///   [EvidenceLabelRef.canOpenInApp]), clicking anywhere on the popup/sheet
///   (or its explicit "Open in PDF" link) jumps straight to that chunk's
///   page in GuidelinePdfViewerScreen — the same in-app viewer + jump-to-
///   page/search behaviour already used by the "Guideline Sources" citation
///   list, just reachable from an inline [E#] citation too.
///
/// This replaces the old failure mode of the backend printing a raw chunk
/// UUID directly into generated prose (e.g. "chunk ID: 0e782494-2471-...")
/// — see gemini_service.py's evidence-labels section. A label with no
/// matching entry in [labels] (e.g. a response generated before this field
/// existed) renders as plain, unstyled text rather than a broken chip.
class EvidenceCitationText extends StatelessWidget {
  final String text;
  final Map<String, EvidenceLabelRef> labels;
  final TextStyle? style;
  final TextAlign? textAlign;

  const EvidenceCitationText({
    super.key,
    required this.text,
    required this.labels,
    this.style,
    this.textAlign,
  });

  @override
  Widget build(BuildContext context) {
    if (text.isEmpty) return const SizedBox.shrink();
    final baseStyle = style ?? DefaultTextStyle.of(context).style;

    if (labels.isEmpty || !_evidenceLabelPattern.hasMatch(text)) {
      // Fast path: nothing to link — avoids building a RichText tree with
      // a single unstyled span for the (very common) case of no citations.
      return Text(text, style: baseStyle, textAlign: textAlign);
    }

    final spans = <InlineSpan>[];
    int last = 0;
    for (final match in _evidenceLabelPattern.allMatches(text)) {
      if (match.start > last) {
        spans.add(TextSpan(text: text.substring(last, match.start)));
      }
      final label = match.group(1)!;
      final ref = labels[label];
      if (ref != null) {
        spans.add(WidgetSpan(
          alignment: PlaceholderAlignment.middle,
          child: _EvidenceChip(label: label, ref: ref, baseStyle: baseStyle),
        ));
      } else {
        spans.add(TextSpan(text: match.group(0)));
      }
      last = match.end;
    }
    if (last < text.length) {
      spans.add(TextSpan(text: text.substring(last)));
    }

    return Text.rich(
      TextSpan(style: baseStyle, children: spans),
      textAlign: textAlign,
    );
  }
}

class _EvidenceChip extends StatefulWidget {
  final String label;
  final EvidenceLabelRef ref;
  final TextStyle baseStyle;

  const _EvidenceChip({
    required this.label,
    required this.ref,
    required this.baseStyle,
  });

  @override
  State<_EvidenceChip> createState() => _EvidenceChipState();
}

class _EvidenceChipState extends State<_EvidenceChip> {
  final LayerLink _link = LayerLink();
  OverlayEntry? _overlayEntry;

  void _showPopup() {
    if (_overlayEntry != null) return;
    final overlay = Overlay.maybeOf(context);
    if (overlay == null) return;
    _overlayEntry = OverlayEntry(
      builder: (ctx) => _EvidencePopup(
        link: _link,
        ref: widget.ref,
        onClose: _hidePopup,
        onOpenPdf: widget.ref.canOpenInApp
            ? () {
                // Close the hover popup before navigating away, so it
                // isn't left dangling as an overlay entry on the new route.
                _hidePopup();
                _openPdfViewer(context, widget.ref);
              }
            : null,
      ),
    );
    overlay.insert(_overlayEntry!);
  }

  void _hidePopup() {
    _overlayEntry?.remove();
    _overlayEntry = null;
  }

  @override
  void dispose() {
    _hidePopup();
    super.dispose();
  }

  void _openSheet() {
    // Touch devices: no hover ever fires, so tapping shows the same
    // content in a bottom sheet instead of the pointer-positioned overlay.
    // Captured here (not inside the builder) so it still points at this
    // still-mounted chip after the sheet's own context is popped/disposed.
    final chipContext = context;
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (sheetContext) => _EvidenceSheet(
        ref: widget.ref,
        onOpenPdf: widget.ref.canOpenInApp
            ? () {
                Navigator.of(sheetContext).pop(); // close the sheet first
                _openPdfViewer(chipContext, widget.ref);
              }
            : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final chipFontSize = (widget.baseStyle.fontSize ?? 13) - 2;
    return CompositedTransformTarget(
      link: _link,
      child: MouseRegion(
        onEnter: (_) => _showPopup(),
        onExit: (_) => _hidePopup(),
        cursor: SystemMouseCursors.click,
        child: GestureDetector(
          onTap: _openSheet,
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 1),
            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
            decoration: BoxDecoration(
              color: const Color(0xFF1A56DB).withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: const Color(0xFF1A56DB).withValues(alpha: 0.35)),
            ),
            child: Text(
              widget.label,
              style: widget.baseStyle.copyWith(
                fontSize: chipFontSize < 9 ? 9 : chipFontSize,
                fontWeight: FontWeight.bold,
                color: const Color(0xFF1A56DB),
                height: 1.0,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Pushes GuidelinePdfViewerScreen straight to this chunk's page — same
/// constructor shape the existing "Guideline Sources" citation list already
/// uses (see care_plan_screen.dart's _CitationRow), just fed from an
/// EvidenceLabelRef instead of a CitationItem.
void _openPdfViewer(BuildContext context, EvidenceLabelRef ref) {
  Navigator.push(
    context,
    MaterialPageRoute(
      builder: (_) => GuidelinePdfViewerScreen(
        fileName: ref.fileName,
        documentName: ref.sourceName.isNotEmpty ? ref.sourceName : ref.fileName,
        pageNumber: ref.pageNumber,
        searchText: ref.section,
      ),
    ),
  );
}

/// Hover popup — positioned just below the chip via [CompositedTransformFollower].
/// Wrapped in its own [MouseRegion] so moving the pointer from the chip onto
/// the popup (to scroll a long excerpt) doesn't close it; it only closes
/// once the pointer leaves the popup itself.
class _EvidencePopup extends StatelessWidget {
  final LayerLink link;
  final EvidenceLabelRef ref;
  final VoidCallback onClose;
  final VoidCallback? onOpenPdf;

  const _EvidencePopup({
    required this.link,
    required this.ref,
    required this.onClose,
    this.onOpenPdf,
  });

  @override
  Widget build(BuildContext context) {
    return Positioned(
      width: 320,
      child: CompositedTransformFollower(
        link: link,
        showWhenUnlinked: false,
        offset: const Offset(-8, 22),
        child: Align(
          alignment: Alignment.topLeft,
          child: MouseRegion(
            onExit: (_) => onClose(),
            child: Material(
              elevation: 8,
              borderRadius: BorderRadius.circular(10),
              color: Theme.of(context).cardColor,
              clipBehavior: Clip.antiAlias,
              child: Container(
                constraints: const BoxConstraints(maxHeight: 280),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.grey.shade300),
                ),
                child: SingleChildScrollView(
                  child: _EvidenceCardContent(ref: ref, onOpenPdf: onOpenPdf),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Bottom-sheet content shown when a citation chip is tapped (touch devices).
class _EvidenceSheet extends StatelessWidget {
  final EvidenceLabelRef ref;
  final VoidCallback? onOpenPdf;
  const _EvidenceSheet({required this.ref, this.onOpenPdf});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.6),
        child: SingleChildScrollView(
          child: _EvidenceCardContent(
            ref: ref,
            onOpenPdf: onOpenPdf,
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
          ),
        ),
      ),
    );
  }
}

/// The actual popup/sheet content: label chip, source + section, page
/// number, excerpt, and — when [onOpenPdf] is provided (i.e. this chunk
/// has a known source PDF, see [EvidenceLabelRef.canOpenInApp]) — an
/// "Open in PDF" link plus a whole-card tap target, so clicking anywhere on
/// the popup (not just the link) jumps to that chunk's page.
class _EvidenceCardContent extends StatelessWidget {
  final EvidenceLabelRef ref;
  final EdgeInsets padding;
  final VoidCallback? onOpenPdf;

  const _EvidenceCardContent({
    required this.ref,
    this.padding = const EdgeInsets.all(14),
    this.onOpenPdf,
  });

  @override
  Widget build(BuildContext context) {
    final titleLine = ref.section.isNotEmpty ? '${ref.sourceName} — ${ref.section}' : ref.sourceName;
    final content = Padding(
      padding: padding,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                margin: const EdgeInsets.only(top: 1),
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: const Color(0xFF1A56DB),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  ref.label,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  titleLine.isNotEmpty ? titleLine : 'Guideline source',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                ),
              ),
            ],
          ),
          if (ref.pageNumber != null) ...[
            const SizedBox(height: 4),
            Text('Page ${ref.pageNumber}', style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
          ],
          const SizedBox(height: 8),
          Text(
            ref.snippet.isNotEmpty ? ref.snippet : '(no excerpt available for this citation)',
            style: const TextStyle(fontSize: 12.5, height: 1.5),
          ),
          if (onOpenPdf != null) ...[
            const SizedBox(height: 10),
            const Divider(height: 1),
            const SizedBox(height: 8),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  ref.pageNumber != null ? 'Open in PDF · p.${ref.pageNumber}' : 'Open in PDF',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF1A56DB),
                  ),
                ),
                const SizedBox(width: 4),
                const Icon(Icons.picture_as_pdf_outlined, size: 13, color: Color(0xFF1A56DB)),
              ],
            ),
          ],
        ],
      ),
    );

    if (onOpenPdf == null) return content;

    // Whole card is tappable, not just the link row — "click the popup"
    // should work anywhere on it, with the link row underneath as an
    // explicit, discoverable affordance for the same action.
    return Material(
      color: Colors.transparent,
      child: InkWell(onTap: onOpenPdf, child: content),
    );
  }
}