import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../core/theme.dart';
import '../data/auth_service.dart';
import '../data/supabase_config.dart';
import '../domain/eoscal_calculator.dart';
import '../data/guidelines_data.dart';
import '../data/gemini_service.dart';
import 'patient_state.dart';
import 'screens/evidence_search_screen.dart';
import 'screens/patient_detail_screen.dart';
import 'screens/care_plan_screen.dart';

class HomeDashboard extends ConsumerStatefulWidget {
  const HomeDashboard({super.key});

  @override
  ConsumerState<HomeDashboard> createState() => _HomeDashboardState();
}

class _HomeDashboardState extends ConsumerState<HomeDashboard> {
  int _currentIndex = 0;

  @override
  void initState() {
    super.initState();
    // Refresh patients from Supabase every time the dashboard is entered so
    // a freshly-logged-in user sees their full patient list immediately.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(patientsProvider.notifier).refresh();
    });
  }

  @override
  Widget build(BuildContext context) {
    final List<Widget> tabs = [
      const DashboardTab(),
      const PatientsTab(),
      EvidenceSearchScreen(),
      const AlertsTab(),
      const SettingsTab(),
    ];

    return Scaffold(
      appBar: AppBar(
        title: FittedBox(
          fit: BoxFit.scaleDown,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.security, color: Colors.white, size: 24),
              const SizedBox(width: 8),
              Text(
                'NEOGUARD AI',
                style: Theme.of(context).appBarTheme.titleTextStyle,
              ),
            ],
          ),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Sign out',
            onPressed: () async {
              await AuthService.instance.signOut();
              if (!mounted) return;
              this.context.go('/login');
            },
          ),
        ],
      ),
      body: SafeArea(
        child: IndexedStack(index: _currentIndex, children: tabs),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => context.push('/new-patient'),
        icon: const Icon(Icons.add),
        label: const Text('New Assessment'),
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _currentIndex,
        onTap: (index) {
          setState(() {
            _currentIndex = index;
          });
        },
        type: BottomNavigationBarType.fixed,
        backgroundColor: WhoTheme.primaryNavy,
        selectedItemColor: Colors.white,
        unselectedItemColor: Colors.white60,
        selectedLabelStyle: const TextStyle(
          fontWeight: FontWeight.bold,
          fontSize: 12,
        ),
        unselectedLabelStyle: const TextStyle(fontSize: 11),
        items: const [
          BottomNavigationBarItem(
            icon: Icon(Icons.dashboard_outlined),
            activeIcon: Icon(Icons.dashboard),
            label: 'Home',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.people_alt_outlined),
            activeIcon: Icon(Icons.people_alt),
            label: 'Patients',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.menu_book_outlined),
            activeIcon: Icon(Icons.menu_book),
            label: 'Evidence',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.warning_amber_outlined),
            activeIcon: Icon(Icons.warning),
            label: 'Alerts',
          ),
          BottomNavigationBarItem(
            icon: Icon(Icons.settings_outlined),
            activeIcon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 1. DASHBOARD TAB
// ==========================================
class DashboardTab extends ConsumerWidget {
  const DashboardTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final patients = ref.watch(patientsProvider);
    final activeGuideline = ref.watch(activeGuidelineProvider);

    int criticalCount = 0;
    int highCount = 0;
    int intermediateCount = 0;
    int lowCount = 0;

    for (var p in patients) {
      final res = EoscalCalculator.calculate(p);
      switch (res.riskCategory) {
        case RiskCategory.critical:
          criticalCount++;
          break;
        case RiskCategory.high:
          highCount++;
          break;
        case RiskCategory.intermediate:
          intermediateCount++;
          break;
        case RiskCategory.low:
          lowCount++;
          break;
      }
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Ward Overview',
                style: Theme.of(context).textTheme.headlineMedium,
              ),
              const SizedBox(height: 2),
              Text(
                'Active Guidance: $activeGuideline Sepsis Engine',
                style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
              ),
            ],
          ),
          const SizedBox(height: 20),

          // Risk Summary Grid
          GridView.count(
            crossAxisCount: 2,
            crossAxisSpacing: 12,
            mainAxisSpacing: 12,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 1.6,
            children: [
              _buildStatCard(
                'Critical Sepsis',
                criticalCount,
                WhoTheme.riskCritical,
              ),
              _buildStatCard('High Risk', highCount, WhoTheme.riskHigh),
              _buildStatCard(
                'Intermediate Risk',
                intermediateCount,
                WhoTheme.riskIntermediate,
              ),
              _buildStatCard('Low Risk', lowCount, WhoTheme.riskLow),
            ],
          ),
          const SizedBox(height: 24),

          // Guidelines card
          Card(
            color: WhoTheme.lightBlueBackground,
            elevation: 0,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  const Icon(Icons.info, color: WhoTheme.primaryNavy, size: 36),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'EOSCAL 2024 Recalibration',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: WhoTheme.primaryNavy,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'NeoGuard implements the updated 2024 weights for intrapartum sepsis calculations, incorporating direct laboratory scoring modifiers.',
                          style: TextStyle(
                            fontSize: 13,
                            color: Colors.grey.shade800,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),

          Text(
            'Recent Assessments',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 10),
          patients.isEmpty
              ? Container(
                  padding: const EdgeInsets.all(32),
                  alignment: Alignment.center,
                  child: Text(
                    'No patients registered. Tap "New Assessment" to start.',
                    style: TextStyle(color: Colors.grey.shade500),
                  ),
                )
              : ListView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: patients.length > 10 ? 10 : patients.length,
                  itemBuilder: (context, index) {
                    final patient = patients[index];
                    final result = EoscalCalculator.calculate(patient);
                    return PatientListTile(patient: patient, result: result);
                  },
                ),
        ],
      ),
    );
  }

  Widget _buildStatCard(String label, int value, Color color) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border(left: BorderSide(color: color, width: 6)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.04),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Flexible(
            child: Text(
              label,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                color: WhoTheme.neutralDarkGrey,
              ),
            ),
          ),
          const SizedBox(height: 4),
          FittedBox(
            child: Text(
              '$value',
              style: TextStyle(
                fontSize: 32,
                fontWeight: FontWeight.bold,
                color: color,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 2. PATIENTS TAB
// ==========================================
class PatientsTab extends ConsumerWidget {
  const PatientsTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final searchQuery = ref.watch(searchQueryProvider);
    final patients = ref.watch(patientsProvider);

    final filteredPatients = patients.where((p) {
      final query = searchQuery.toLowerCase();
      return p.name.toLowerCase().contains(query) ||
          p.mrn.toLowerCase().contains(query);
    }).toList();

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Patient Registry',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 12),
          TextField(
            onChanged: (val) =>
                ref.read(searchQueryProvider.notifier).state = val,
            decoration: InputDecoration(
              hintText: 'Search patients by name or MRN...',
              prefixIcon: const Icon(Icons.search, color: WhoTheme.primaryNavy),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: filteredPatients.isEmpty
                ? Center(
                    child: Text(
                      searchQuery.isEmpty
                          ? 'No patients enrolled in registry.'
                          : 'No matching patients found.',
                      style: TextStyle(color: Colors.grey.shade500),
                    ),
                  )
                : ListView.builder(
                    itemCount: filteredPatients.length,
                    itemBuilder: (context, index) {
                      final patient = filteredPatients[index];
                      final result = EoscalCalculator.calculate(patient);
                      return PatientListTile(patient: patient, result: result);
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 3. EVIDENCE TAB
// ==========================================
class EvidenceTab extends StatefulWidget {
  const EvidenceTab({super.key});

  @override
  State<EvidenceTab> createState() => _EvidenceTabState();
}

class _EvidenceTabState extends State<EvidenceTab> {
  String _selectedSource = 'ALL';
  String _searchQuery = '';

  @override
  Widget build(BuildContext context) {
    final filteredChunks = GuidelinesData.chunks.where((chunk) {
      final matchesSource =
          _selectedSource == 'ALL' || chunk.source == _selectedSource;
      final matchesSearch =
          chunk.content.toLowerCase().contains(_searchQuery.toLowerCase()) ||
          chunk.section.toLowerCase().contains(_searchQuery.toLowerCase()) ||
          chunk.keywords.any(
            (k) => k.toLowerCase().contains(_searchQuery.toLowerCase()),
          );
      return matchesSource && matchesSearch;
    }).toList();

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Guidelines & Evidence',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 12),
          // Search Guideline
          TextField(
            onChanged: (val) {
              setState(() {
                _searchQuery = val;
              });
            },
            decoration: InputDecoration(
              hintText: 'Search evidence keywords (e.g. antibiotic, CRP)...',
              prefixIcon: const Icon(Icons.search, color: WhoTheme.primaryNavy),
            ),
          ),
          const SizedBox(height: 12),
          // Filter Chips
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: ['ALL', 'NICE', 'AAP', 'WHO'].map((source) {
                final isSelected = _selectedSource == source;
                return Padding(
                  padding: const EdgeInsets.only(right: 8.0),
                  child: FilterChip(
                    label: Text(source),
                    selected: isSelected,
                    selectedColor: WhoTheme.primaryNavy.withValues(alpha: 0.15),
                    checkmarkColor: WhoTheme.primaryNavy,
                    labelStyle: TextStyle(
                      color: isSelected
                          ? WhoTheme.primaryNavy
                          : WhoTheme.neutralDarkGrey,
                      fontWeight: isSelected
                          ? FontWeight.bold
                          : FontWeight.normal,
                    ),
                    onSelected: (selected) {
                      setState(() {
                        _selectedSource = source;
                      });
                    },
                  ),
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: filteredChunks.isEmpty
                ? Center(
                    child: Text(
                      'No matching guideline guidelines found.',
                      style: TextStyle(color: Colors.grey.shade500),
                    ),
                  )
                : ListView.builder(
                    itemCount: filteredChunks.length,
                    itemBuilder: (context, index) {
                      final chunk = filteredChunks[index];
                      return Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                mainAxisAlignment:
                                    MainAxisAlignment.spaceBetween,
                                children: [
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 8,
                                      vertical: 4,
                                    ),
                                    decoration: BoxDecoration(
                                      color: WhoTheme.primaryNavy.withValues(
                                        alpha: 0.1,
                                      ),
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    child: Text(
                                      chunk.source,
                                      style: const TextStyle(
                                        color: WhoTheme.primaryNavy,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 12,
                                      ),
                                    ),
                                  ),
                                  Text(
                                    chunk.section,
                                    style: TextStyle(
                                      color: Colors.grey.shade600,
                                      fontSize: 12,
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 10),
                              Text(
                                chunk.content,
                                style: const TextStyle(
                                  fontSize: 14,
                                  color: WhoTheme.neutralDarkGrey,
                                  height: 1.4,
                                ),
                              ),
                              const SizedBox(height: 8),
                              Wrap(
                                spacing: 6,
                                runSpacing: 4,
                                children: chunk.keywords.map((k) {
                                  return Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 6,
                                      vertical: 2,
                                    ),
                                    decoration: BoxDecoration(
                                      color: Colors.grey.shade100,
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    child: Text(
                                      '#$k',
                                      style: TextStyle(
                                        color: Colors.grey.shade600,
                                        fontSize: 11,
                                      ),
                                    ),
                                  );
                                }).toList(),
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 4. ALERTS TAB
// ==========================================
class AlertsTab extends ConsumerStatefulWidget {
  const AlertsTab({super.key});

  @override
  ConsumerState<AlertsTab> createState() => _AlertsTabState();
}

class _AlertsTabState extends ConsumerState<AlertsTab> {
  @override
  Widget build(BuildContext context) {
    final patients = ref.watch(patientsProvider);

    final List<Map<String, dynamic>> activeAlerts = [];

    for (var p in patients) {
      final res = EoscalCalculator.calculate(p);
      // High or critical risks trigger clinical warning items
      if (res.riskCategory == RiskCategory.critical ||
          res.riskCategory == RiskCategory.high) {
        activeAlerts.add({
          'patient': p,
          'result': res,
          'type': 'risk',
          'severity': res.riskCategory,
          'message':
              'Patient has a high EOSCAL score of ${res.totalScore}. Initiate clinical protocols.',
        });
      }
      // Premature warning
      if (p.gestationalAgeWeeks < 35.0) {
        activeAlerts.add({
          'patient': p,
          'result': res,
          'type': 'preterm',
          'severity': RiskCategory.intermediate,
          'message':
              'Premature gestation (${p.gestationalAgeWeeks} weeks). Interpret calculations with caution.',
        });
      }
      // Blood culture alerts
      if (p.bloodCulturePositive == true) {
        activeAlerts.add({
          'patient': p,
          'result': res,
          'type': 'culture',
          'severity': RiskCategory.critical,
          'message': 'CONFIRMED BACTEREMIA. Blood culture returned positive.',
        });
      }
    }

    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Clinical Warnings & Audits',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 4),
          Text(
            'Real-time alerts active across current ward roster.',
            style: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          ),
          const SizedBox(height: 16),
          Expanded(
            child: activeAlerts.isEmpty
                ? Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          Icons.check_circle_outline,
                          size: 64,
                          color: WhoTheme.riskLow,
                        ),
                        const SizedBox(height: 12),
                        const Text(
                          'No critical alerts active.',
                          style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 16,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'All patients are low risk or stable.',
                          style: TextStyle(color: Colors.grey.shade500),
                        ),
                      ],
                    ),
                  )
                : ListView.builder(
                    itemCount: activeAlerts.length,
                    itemBuilder: (context, index) {
                      final alert = activeAlerts[index];
                      final PatientParameters patient = alert['patient'];
                      final EoscalResult result = alert['result'];
                      final RiskCategory severity = alert['severity'];

                      return Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8),
                          side: BorderSide(
                            color: severity.color.withValues(alpha: 0.4),
                            width: 1,
                          ),
                        ),
                        child: ListTile(
                          leading: CircleAvatar(
                            backgroundColor: severity.color.withValues(
                              alpha: 0.15,
                            ),
                            child: Icon(
                              Icons.warning,
                              color: severity.color,
                              size: 20,
                            ),
                          ),
                          title: Text(
                            patient.name,
                            style: const TextStyle(fontWeight: FontWeight.bold),
                          ),
                          subtitle: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const SizedBox(height: 2),
                              Text(alert['message']),
                              const SizedBox(height: 4),
                              Row(
                                children: [
                                  Text(
                                    'Score: ${result.totalScore} | MRN: ${patient.mrn}',
                                    style: TextStyle(
                                      fontSize: 12,
                                      color: Colors.grey.shade600,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                          isThreeLine: true,
                          trailing: const Icon(
                            Icons.arrow_forward_ios,
                            size: 14,
                          ),
                          onTap: () {
                            final guideline = ref.read(activeGuidelineProvider);
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (context) => PatientDetailScreen(
                                  patient: patient,
                                  activeGuideline: guideline,
                                ),
                              ),
                            );
                          },
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// 5. SETTINGS TAB
// ==========================================
class SettingsTab extends ConsumerStatefulWidget {
  const SettingsTab({super.key});

  @override
  ConsumerState<SettingsTab> createState() => _SettingsTabState();
}

class _SettingsTabState extends ConsumerState<SettingsTab> {
  final TextEditingController _apiKeyController = TextEditingController();
  bool _isEditing = false;
  String _maskedKey = '';

  @override
  void initState() {
    super.initState();
    _loadKey();
  }

  Future<void> _loadKey() async {
    final key = await GeminiService.getApiKey();
    if (key != null && key.isNotEmpty) {
      setState(() {
        _apiKeyController.text = key;
        _maskedKey =
            '••••••••••••••••${key.substring(key.length > 6 ? key.length - 6 : 0)}';
      });
    } else {
      setState(() {
        _apiKeyController.text = '';
        _maskedKey = 'Not Configured (Running in Offline Simulation Mode)';
      });
    }
  }

  @override
  void dispose() {
    _apiKeyController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final activeGuideline = ref.watch(activeGuidelineProvider);
    final notifier = ref.read(activeGuidelineProvider.notifier);

    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Settings & Configuration',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
          const SizedBox(height: 20),

          // Clinical Guideline Configuration
          Text(
            'Default Guideline Engine',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 4),
          const Text(
            'Select which clinical guidelines to prioritize for calculation, RAG queries, and immediate care suggestions.',
            style: TextStyle(fontSize: 13, color: WhoTheme.neutralDarkGrey),
          ),
          const SizedBox(height: 12),
          Card(
            child: Column(
              children: [
                RadioListTile<String>(
                  title: const Text('NICE (UK - NG195) Sepsis Guideline'),
                  subtitle: const Text(
                    'Strict timeline-based treatment with 1-hour limits',
                  ),
                  value: 'NICE',
                  groupValue: activeGuideline,
                  onChanged: (val) {
                    if (val != null) notifier.setGuideline(val);
                  },
                ),
                const Divider(height: 1),
                RadioListTile<String>(
                  title: const Text('AAP (US - 2023) Management Guideline'),
                  subtitle: const Text(
                    'Appearance-based and risk factor enhanced monitoring',
                  ),
                  value: 'AAP',
                  groupValue: activeGuideline,
                  onChanged: (val) {
                    if (val != null) notifier.setGuideline(val);
                  },
                ),
                const Divider(height: 1),
                RadioListTile<String>(
                  title: const Text('WHO (Global) Sepsis Guideline'),
                  subtitle: const Text(
                    'Clinical danger signs and resource-optimized therapy',
                  ),
                  value: 'WHO',
                  groupValue: activeGuideline,
                  onChanged: (val) {
                    if (val != null) notifier.setGuideline(val);
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // Gemini API key settings
          Text(
            'Gemini AI Explanation Settings',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 4),
          const Text(
            'Provide a Gemini API Key to enable real-time generative clinical explanation analysis. If empty, the app runs in Offline Simulation mode.',
            style: TextStyle(fontSize: 13, color: WhoTheme.neutralDarkGrey),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Gemini API Status:',
                    style: TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    _maskedKey,
                    style: TextStyle(
                      color: _apiKeyController.text.isEmpty
                          ? WhoTheme.riskIntermediate
                          : WhoTheme.riskLow,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 12),
                  if (_isEditing) ...[
                    TextField(
                      controller: _apiKeyController,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'Enter Gemini API Key',
                        hintText: 'AIzaSy...',
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        ElevatedButton(
                          onPressed: () async {
                            final key = _apiKeyController.text.trim();
                            if (key.isNotEmpty) {
                              await GeminiService.saveApiKey(key);
                            } else {
                              await GeminiService.deleteApiKey();
                            }
                            if (!mounted) return;
                            setState(() {
                              _isEditing = false;
                            });
                            await _loadKey();
                            if (!mounted) return;
                            ScaffoldMessenger.of(this.context).showSnackBar(
                              const SnackBar(
                                content: Text('API Key configuration updated.'),
                              ),
                            );
                          },
                          child: const Text('Save'),
                        ),
                        const SizedBox(width: 8),
                        TextButton(
                          onPressed: () {
                            setState(() {
                              _isEditing = false;
                            });
                            _loadKey();
                          },
                          child: const Text('Cancel'),
                        ),
                      ],
                    ),
                  ] else ...[
                    ElevatedButton(
                      onPressed: () {
                        setState(() {
                          _isEditing = true;
                        });
                      },
                      child: Text(
                        _apiKeyController.text.isEmpty
                            ? 'Setup API Key'
                            : 'Change API Key',
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          Text('Administration', style: Theme.of(context).textTheme.titleLarge),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.medical_services_outlined),
                  title: const Text('Stewardship Dashboard'),
                  onTap: () => context.push('/admin/stewardship'),
                ),
                // User management is intentionally parked for now.
                // ListTile(
                //   leading: const Icon(Icons.people_outline),
                //   title: const Text('User Management'),
                //   onTap: () => context.push('/admin/users'),
                // ),
                ListTile(
                  leading: const Icon(Icons.library_books_outlined),
                  title: const Text('Guideline Configuration'),
                  onTap: () => context.push('/admin/guidelines'),
                ),
                ListTile(
                  leading: const Icon(Icons.history),
                  title: const Text('Audit Log'),
                  onTap: () => context.push('/admin/audit'),
                ),
                // Institution settings is intentionally parked for now.
                // ListTile(
                //   leading: const Icon(Icons.business),
                //   title: const Text('Institution Settings'),
                //   onTap: () => context.push('/admin/settings'),
                // ),
              ],
            ),
          ),
          const SizedBox(height: 32),
          Center(
            child: Text(
              'NeoGuard Clinical App v1.0.0 (WHO Aesthetic)',
              style: TextStyle(color: Colors.grey.shade500, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

// ==========================================
// SHARED REUSABLE COMPONENTS
// ==========================================
class PatientListTile extends ConsumerWidget {
  final PatientParameters patient;
  final EoscalResult result;

  const PatientListTile({
    super.key,
    required this.patient,
    required this.result,
  });

  void _openExplanation(BuildContext context, WidgetRef ref) {
    final guideline = ref.read(activeGuidelineProvider);
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CarePlanScreen(
          patient: patient,
          result: result,
          activeGuideline: guideline,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ageHours = DateTime.now().difference(patient.birthDateTime).inHours;
    final riskColor = result.riskCategory.color;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () {
          final guideline = ref.read(activeGuidelineProvider);
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) => PatientDetailScreen(
                patient: patient,
                activeGuideline: guideline,
              ),
            ),
          );
        },
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              // Risk score badge
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: riskColor.withValues(alpha: 0.12),
                  shape: BoxShape.circle,
                  border: Border.all(color: riskColor, width: 2),
                ),
                alignment: Alignment.center,
                child: Text(
                  '${result.totalScore}',
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                    color: riskColor,
                  ),
                ),
              ),
              const SizedBox(width: 16),

              // Patient core details
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      patient.name,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                        color: WhoTheme.primaryNavy,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'MRN: ${patient.mrn}  •  GA: ${patient.gestationalAgeWeeks}w  •  Age: ${ageHours}h',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        color: Colors.grey.shade600,
                      ),
                    ),
                    if (patient.gestationalAgeWeeks < 35.0) ...[
                      const SizedBox(height: 4),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.warning,
                            color: WhoTheme.riskIntermediate,
                            size: 14,
                          ),
                          const SizedBox(width: 4),
                          Expanded(
                            child: const Text(
                              'Premature infant (<35 weeks)',
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 11,
                                color: WhoTheme.riskIntermediate,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),

              // Risk badge + AI star + chevron
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: riskColor,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(
                      result.riskCategory.displayName.split(' ')[0],
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 11,
                      ),
                    ),
                  ),
                  const SizedBox(height: 4),
                  // AI explanation star button — tap to open explanation directly
                  Tooltip(
                    message: 'AI Care Plan',
                    child: InkWell(
                      onTap: () => _openExplanation(context, ref),
                      borderRadius: BorderRadius.circular(16),
                      child: Padding(
                        padding: const EdgeInsets.all(4),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(
                              Icons.auto_awesome,
                              size: 16,
                              color: riskColor,
                            ),
                            const SizedBox(width: 2),
                            Text(
                              'AI',
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                                color: riskColor,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 2),
                  const Icon(
                    Icons.arrow_forward_ios,
                    size: 14,
                    color: Colors.grey,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
