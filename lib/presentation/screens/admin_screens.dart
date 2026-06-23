import 'package:flutter/material.dart';
import '../../core/theme.dart';

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

class GuidelineConfigScreen extends StatelessWidget {
  const GuidelineConfigScreen({super.key});

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
          OutlinedButton(onPressed: () {}, child: const Text('Rebuild vector index')),
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
