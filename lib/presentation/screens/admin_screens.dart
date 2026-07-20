import 'package:flutter/material.dart';
import '../../core/theme.dart';
import '../../data/api/api_client.dart';
import '../../data/offline_guideline_cache.dart';
import '../../data/offline_pdf_cache.dart';

class StewardshipDashboardScreen extends StatelessWidget {
  const StewardshipDashboardScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Stewardship Dashboard')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Container(
            padding: const EdgeInsets.all(12),
            color: WhoTheme.riskIntermediate.withValues(alpha: 0.2),
            child: const Text('Visible to Attending Physicians and Administrators only'),
          ),
          const SizedBox(height: 16),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _kpi('Antibiotic Starts', '42%', '↓ 8%'),
                _kpi('De-escalated 36-48h', '31%', '↑ 5%'),
                _kpi('Mean Duration', '4.2d', ''),
                _kpi('Encounters', '128', ''),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _kpi(String title, String value, String trend) {
    return Card(
      margin: const EdgeInsets.only(right: 12),
      child: SizedBox(
        width: 140,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: const TextStyle(fontSize: 11)),
              Text(value, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
              if (trend.isNotEmpty) Text(trend, style: TextStyle(color: Colors.green.shade700, fontSize: 12)),
            ],
          ),
        ),
      ),
    );
  }
}

class UserManagementScreen extends StatelessWidget {
  const UserManagementScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('User Management'),
        actions: [IconButton(icon: const Icon(Icons.person_add), onPressed: () {})],
      ),
      body: ListView(
        children: const [
          ListTile(leading: CircleAvatar(child: Text('KM')), title: Text('Dr. Komal Mushtaq'), subtitle: Text('Attending · Active'), trailing: Icon(Icons.more_vert)),
          ListTile(leading: CircleAvatar(child: Text('RN')), title: Text('Registrar Demo'), subtitle: Text('Registrar · Active'), trailing: Icon(Icons.more_vert)),
        ],
      ),
    );
  }
}

class GuidelineConfigScreen extends StatefulWidget {
  const GuidelineConfigScreen({super.key});

  @override
  State<GuidelineConfigScreen> createState() => _GuidelineConfigScreenState();
}

class _GuidelineConfigScreenState extends State<GuidelineConfigScreen> {
  bool _rebuilding = false;
  bool _syncing = false;
  bool _downloadingPdfs = false;
  String? _rebuildStatus;
  DateTime? _lastSynced;
  int _cachedProtocolCount = 0;
  int _pdfDownloadCompleted = 0;
  int _pdfDownloadTotal = 0;

  @override
  void initState() {
    super.initState();
    _loadOfflineStatus();
  }

  Future<void> _loadOfflineStatus() async {
    final synced = await OfflineGuidelineCache.lastSynced();
    final keys = await OfflineGuidelineCache.cachedKeys();
    if (mounted) {
      setState(() {
        _lastSynced = synced;
        _cachedProtocolCount = keys.length;
      });
    }
  }

  Future<void> _rebuildVectorIndex() async {
    setState(() {
      _rebuilding = true;
      _rebuildStatus = null;
    });
    try {
      final response = await apiClient.dio.post('/api/v1/admin/guidelines/ingest');
      final data = response.data as Map<String, dynamic>;
      final inserted = data['chunks_inserted'] ?? 0;
      setState(() => _rebuildStatus = 'Rebuilt — $inserted chunks indexed. '
          'Offline care plans will refresh next time the app syncs.');
    } catch (e) {
      setState(() => _rebuildStatus = 'Rebuild failed: $e');
    } finally {
      if (mounted) setState(() => _rebuilding = false);
    }
  }

  Future<void> _syncOfflineCache() async {
    setState(() => _syncing = true);
    await OfflineGuidelineCache.sync();
    await _loadOfflineStatus();
    if (mounted) setState(() => _syncing = false);
  }

  Future<void> _downloadAllPdfs() async {
    setState(() {
      _downloadingPdfs = true;
      _pdfDownloadCompleted = 0;
      _pdfDownloadTotal = 0;
    });
    // wifiOnly: false — this is an explicit tap from Settings, not a
    // background boot sync, so the clinician has opted in to the data cost.
    await OfflinePdfCache.syncAllPdfs(
      wifiOnly: false,
      onProgress: (completed, total) {
        if (mounted) {
          setState(() {
            _pdfDownloadCompleted = completed;
            _pdfDownloadTotal = total;
          });
        }
      },
    );
    if (mounted) setState(() => _downloadingPdfs = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Guideline Configuration')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: ListTile(
              title: const Text('NICE NG195'),
              subtitle: const Text('Active · 847 chunks · all-MiniLM-L6-v2'),
              trailing: ElevatedButton(onPressed: () {}, child: const Text('Switch')),
            ),
          ),
          const SizedBox(height: 16),
          const Text('Upload local protocol', style: TextStyle(fontWeight: FontWeight.bold)),
          Container(
            height: 120,
            margin: const EdgeInsets.symmetric(vertical: 12),
            decoration: BoxDecoration(border: Border.all(color: Colors.grey.shade400), borderRadius: BorderRadius.circular(8)),
            child: const Center(child: Text('Drag PDF or Browse')),
          ),
          const ListTile(
            leading: Icon(Icons.check_circle, color: WhoTheme.riskLow),
            title: Text('Vector index: Healthy'),
            subtitle: Text('Last rebuilt: 2 hours ago · Total chunks: 4,847'),
          ),
          OutlinedButton(
            onPressed: _rebuilding ? null : _rebuildVectorIndex,
            child: _rebuilding
                ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Text('Rebuild vector index'),
          ),
          if (_rebuildStatus != null) ...[
            const SizedBox(height: 8),
            Text(_rebuildStatus!, style: const TextStyle(fontSize: 12, color: Colors.grey)),
          ],
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 8),
          const Text('Offline care plan cache', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text(
            'Every guideline (including uploaded local protocols) is pre-synthesized into '
            '4 risk-level protocols and synced to this device so care plans still work '
            'with zero connectivity. Rebuilding the vector index above also refreshes this — '
            'sync afterward to pull it to this device.',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          ),
          const SizedBox(height: 12),
          Card(
            child: ListTile(
              leading: Icon(
                _cachedProtocolCount > 0 ? Icons.offline_pin : Icons.warning_amber_rounded,
                color: _cachedProtocolCount > 0 ? WhoTheme.riskLow : WhoTheme.riskIntermediate,
              ),
              title: Text('$_cachedProtocolCount protocol${_cachedProtocolCount == 1 ? '' : 's'} cached on this device'),
              subtitle: Text(_lastSynced != null
                  ? 'Last synced: ${_lastSynced!.toLocal().toString().substring(0, 16)}'
                  : 'Never synced — connect to the backend at least once'),
              trailing: TextButton(
                onPressed: _syncing ? null : _syncOfflineCache,
                child: _syncing
                    ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Sync now'),
              ),
            ),
          ),
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 8),
          const Text('Offline guideline PDFs', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text(
            'Downloads every guideline PDF to this device so "view in source PDF" works with '
            'zero connectivity, without needing to have opened each document online first. '
            'Runs automatically over WiFi at app launch — tap below to force it now, including '
            'over mobile data.',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          ),
          const SizedBox(height: 12),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: Icon(Icons.picture_as_pdf_outlined, color: WhoTheme.secondaryTeal),
                  title: const Text('Download all guideline PDFs'),
                  subtitle: _downloadingPdfs && _pdfDownloadTotal > 0
                      ? Text('Downloading $_pdfDownloadCompleted / $_pdfDownloadTotal…')
                      : const Text('For fully offline "view in source PDF"'),
                  trailing: TextButton(
                    onPressed: _downloadingPdfs ? null : _downloadAllPdfs,
                    child: _downloadingPdfs
                        ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('Download all'),
                  ),
                ),
                if (_downloadingPdfs && _pdfDownloadTotal > 0)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                    child: LinearProgressIndicator(
                      value: _pdfDownloadCompleted / _pdfDownloadTotal,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class AuditLogScreen extends StatelessWidget {
  const AuditLogScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Audit Log'),
        actions: [IconButton(icon: const Icon(Icons.filter_list), onPressed: () {}), IconButton(icon: const Icon(Icons.download), onPressed: () {})],
      ),
      body: Column(
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            color: WhoTheme.riskLow.withValues(alpha: 0.2),
            child: const Text('✅ Chain integrity verified — no tampering detected'),
          ),
          Expanded(
            child: ListView(
              children: const [
                ListTile(title: Text('LLM_CALL'), subtitle: Text('RAG retrieval + Gemini explanation'), trailing: Text('14:32')),
                ListTile(title: Text('RISK_CALC'), subtitle: Text('EOS assessment completed'), trailing: Text('14:30')),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class InstitutionSettingsScreen extends StatelessWidget {
  const InstitutionSettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Institution Settings'),
        actions: [TextButton(onPressed: () {}, child: const Text('Save Changes'))],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(decoration: const InputDecoration(labelText: 'Hospital name'), controller: TextEditingController(text: 'Demo Neonatal Unit')),
          SwitchListTile(title: const Text('CRITICAL alerts push'), value: true, onChanged: (_) {}),
          SwitchListTile(title: const Text('HL7 FHIR R4'), value: false, onChanged: (_) {}),
          const SizedBox(height: 24),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(border: Border.all(color: WhoTheme.riskCritical), borderRadius: BorderRadius.circular(8)),
            child: const Text('Danger zone: Delete all patient data requires dual admin approval'),
          ),
        ],
      ),
    );
  }
}