import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:intl/intl.dart';
import '../../core/theme.dart';
import '../../domain/eoscal_calculator.dart';
import '../../data/auth_service.dart';
import '../../data/supabase_config.dart';

/// Screen 16 — Patient Timeline showing historical clinical assessments
class PatientTimelineScreen extends StatefulWidget {
  final PatientParameters patient;
  final EoscalResult result;

  const PatientTimelineScreen({super.key, required this.patient, required this.result});

  @override
  State<PatientTimelineScreen> createState() => _PatientTimelineScreenState();
}

class _PatientTimelineScreenState extends State<PatientTimelineScreen> {
  List<Map<String, dynamic>> _assessments = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchTimeline();
  }

  Color _getColorForCategory(String category) {
    switch (category.toUpperCase()) {
      case 'CRITICAL':
        return WhoTheme.riskCritical;
      case 'HIGH':
        return WhoTheme.riskHigh;
      case 'INTERMEDIATE':
        return WhoTheme.riskIntermediate;
      case 'LOW':
      default:
        return WhoTheme.riskLow;
    }
  }

  Future<void> _fetchTimeline() async {
    if (!SupabaseConfig.isConfigured || !AuthService.instance.isSignedIn) {
      // Fallback to local parameters
      _loadLocalFallback();
      return;
    }

    try {
      final client = AuthService.instance.client;
      // Fetch assessments
      final assessmentsRows = await client
          .from('clinical_assessments')
          .select('id, created_at, event_type, maternal_data, neonatal_data, lab_data')
          .eq('encounter_id', widget.patient.id)
          .order('created_at', ascending: true);

      // Fetch risk results
      final riskRows = await client
          .from('risk_results')
          .select('assessment_id, combined_score, category')
          .eq('encounter_id', widget.patient.id);

      final Map<String, Map<String, dynamic>> riskMap = {};
      for (final r in riskRows) {
        final aId = r['assessment_id'] as String?;
        if (aId != null) {
          riskMap[aId] = r;
        }
      }

      final List<Map<String, dynamic>> items = [];
      for (final row in assessmentsRows) {
        final aId = row['id'] as String;
        final risk = riskMap[aId];
        items.add({
          'created_at': row['created_at'],
          'maternal_data': row['maternal_data'] ?? {},
          'neonatal_data': row['neonatal_data'] ?? {},
          'lab_data': row['lab_data'] ?? {},
          'combined_score': risk != null ? (risk['combined_score'] as num).toInt() : 0,
          'category': risk != null ? risk['category'] as String : 'LOW',
        });
      }

      if (items.isEmpty) {
        _loadLocalFallback();
      } else {
        setState(() {
          _assessments = items;
          _loading = false;
        });
      }
    } catch (e) {
      debugPrint('Error fetching timeline: $e');
      _loadLocalFallback();
    }
  }

  void _loadLocalFallback() {
    setState(() {
      _assessments = [
        {
          'created_at': DateTime.now().toUtc().toIso8601String(),
          'maternal_data': {
            'maternal_temperature': widget.patient.maternalTemperature,
            'rom_hours': widget.patient.romHours,
            'gbs_positive': widget.patient.gbsPositive,
            'adequate_intrapartum_antibiotics': widget.patient.adequateIntrapartumAntibiotics,
            'clinical_chorioamnionitis': widget.patient.clinicalChorioamnionitis,
            'delivery_mode': widget.patient.deliveryMode,
          },
          'neonatal_data': {
            'respiratory_distress': widget.patient.respiratoryDistress,
            'oxygen_need': widget.patient.oxygenNeed,
            'apgar_5_min': widget.patient.apgar5Min,
            'poor_perfusion': widget.patient.poorPerfusion,
            'neonatal_temperature': widget.patient.neonatalTemperature,
            'neurological_status': widget.patient.neurologicalStatus,
            'gestational_age_weeks': widget.patient.gestationalAgeWeeks,
          },
          'lab_data': {
            'wbc_count': widget.patient.wbcCount,
            'it_ratio': widget.patient.itRatio,
            'platelet_count': widget.patient.plateletCount,
            'crp_level': widget.patient.crpLevel,
            'pct_level': widget.patient.pctLevel,
            'blood_culture_positive': widget.patient.bloodCulturePositive,
          },
          'combined_score': widget.result.totalScore,
          'category': widget.result.riskCategory.name.toUpperCase(),
        }
      ];
      _loading = false;
    });
  }

  void _showAssessmentDetail(Map<String, dynamic> assessment, int ageHours) {
    final maternal = assessment['maternal_data'] as Map<String, dynamic>? ?? {};
    final neonatal = assessment['neonatal_data'] as Map<String, dynamic>? ?? {};
    final lab = assessment['lab_data'] as Map<String, dynamic>? ?? {};
    final date = DateTime.parse(assessment['created_at']);

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Assessment Details', style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(
              'Recorded at age ${ageHours}h (${DateFormat('yyyy-MM-dd HH:mm').format(date.toLocal())})',
              style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            ),
          ],
        ),
        content: SizedBox(
          width: double.maxFinite,
          child: ListView(
            shrinkWrap: true,
            children: [
              _buildDetailSection('Maternal Indicators', [
                _buildDetailRow('Temperature', '${maternal['maternal_temperature'] ?? 'N/A'} °C'),
                _buildDetailRow('ROM Hours', '${maternal['rom_hours'] ?? 'N/A'} hours'),
                _buildDetailRow('GBS Positive', '${maternal['gbs_positive'] ?? 'N/A'}'),
                _buildDetailRow('Adequate IAP', '${maternal['adequate_intrapartum_antibiotics'] ?? 'N/A'}'),
                _buildDetailRow('Clinical Chorioamnionitis', '${maternal['clinical_chorioamnionitis'] ?? 'N/A'}'),
                _buildDetailRow('Delivery Mode', '${maternal['delivery_mode'] ?? 'N/A'}'),
              ]),
              const Divider(),
              _buildDetailSection('Neonatal Status', [
                _buildDetailRow('Gestational Age', '${neonatal['gestational_age_weeks'] ?? 'N/A'} weeks'),
                _buildDetailRow('Temperature', '${neonatal['neonatal_temperature'] ?? 'N/A'} °C'),
                _buildDetailRow('Respiratory Distress', '${neonatal['respiratory_distress'] ?? 'N/A'}'),
                _buildDetailRow('Oxygen Need', '${neonatal['oxygen_need'] ?? 'N/A'}'),
                _buildDetailRow('APGAR (5-Min)', '${neonatal['apgar_5_min'] ?? 'N/A'}'),
                _buildDetailRow('Poor Perfusion', '${neonatal['poor_perfusion'] ?? 'N/A'}'),
                _buildDetailRow('Neurological Status', '${neonatal['neurological_status'] ?? 'N/A'}'),
              ]),
              const Divider(),
              _buildDetailSection('Laboratory Results', [
                _buildDetailRow('WBC Count', '${lab['wbc_count'] ?? 'N/A'} /mm³'),
                _buildDetailRow('I:T Ratio', '${lab['it_ratio'] ?? 'N/A'}'),
                _buildDetailRow('Platelets', '${lab['platelet_count'] ?? 'N/A'} /mm³'),
                _buildDetailRow('CRP level', '${lab['crp_level'] ?? 'N/A'} mg/L'),
                _buildDetailRow('PCT level', '${lab['pct_level'] ?? 'N/A'} ng/mL'),
                _buildDetailRow(
                  'Blood Culture',
                  lab['blood_culture_positive'] == true
                      ? 'Positive'
                      : lab['blood_culture_positive'] == false
                          ? 'Negative'
                          : 'Pending/Not Done',
                ),
              ]),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Widget _buildDetailSection(String title, List<Widget> children) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Text(
            title,
            style: const TextStyle(fontWeight: FontWeight.bold, color: WhoTheme.primaryNavy, fontSize: 14),
          ),
        ),
        ...children,
      ],
    );
  }

  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: TextStyle(fontSize: 13, color: Colors.grey.shade700)),
          Text(value, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text('Error loading history: $_error', style: const TextStyle(color: Colors.red)),
        ),
      );
    }

    // Map spots for LineChart
    final List<FlSpot> spots = [];
    double minX = 0;
    double maxX = 72;

    for (int i = 0; i < _assessments.length; i++) {
      final item = _assessments[i];
      final date = DateTime.parse(item['created_at']);
      final ageHours = date.difference(widget.patient.birthDateTime).inHours.toDouble().clamp(0.0, 500.0);
      final score = (item['combined_score'] as num).toDouble();
      spots.add(FlSpot(ageHours, score));
    }

    // Ensure we sort spots by age
    spots.sort((a, b) => a.x.compareTo(b.x));

    // Calculate chart bounds based on points
    if (spots.isNotEmpty) {
      minX = 0; // always start at age 0
      maxX = spots.last.x > 72.0 ? spots.last.x + 12.0 : 72.0;
    }

    // If only one spot exists, create a line from age 0 to the current age
    final List<FlSpot> chartSpots = List.from(spots);
    if (chartSpots.length == 1) {
      final single = chartSpots.first;
      if (single.x > 0) {
        chartSpots.insert(0, FlSpot(0, single.y));
      } else {
        chartSpots.add(FlSpot(72, single.y));
      }
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Title block
        Text(
          'Assessment Progress Trend',
          style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 12),
        // Chart container
        Container(
          height: 220,
          padding: const EdgeInsets.only(right: 20, top: 12, bottom: 8),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.04),
                blurRadius: 6,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: LineChart(
            LineChartData(
              minY: 0,
              maxY: 32,
              minX: minX,
              maxX: maxX,
              lineTouchData: LineTouchData(
                touchTooltipData: LineTouchTooltipData(
                  getTooltipColor: (_) => WhoTheme.primaryNavy.withOpacity(0.9),
                  getTooltipItems: (touchedSpots) {
                    return touchedSpots.map((spot) {
                      return LineTooltipItem(
                        'Score: ${spot.y.toInt()}\nAge: ${spot.x.toInt()}h',
                        const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12),
                      );
                    }).toList();
                  },
                ),
              ),
              lineBarsData: [
                LineChartBarData(
                  spots: chartSpots,
                  isCurved: chartSpots.length > 2,
                  color: widget.result.riskCategory.color,
                  barWidth: 3,
                  isStrokeCapRound: true,
                  dotData: const FlDotData(show: true),
                  belowBarData: BarAreaData(
                    show: true,
                    color: widget.result.riskCategory.color.withOpacity(0.1),
                  ),
                ),
              ],
              titlesData: FlTitlesData(
                show: true,
                leftTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    reservedSize: 32,
                    interval: 8,
                    getTitlesWidget: (value, _) => Text(
                      '${value.toInt()}',
                      style: TextStyle(color: Colors.grey.shade600, fontSize: 10),
                    ),
                  ),
                ),
                bottomTitles: AxisTitles(
                  axisNameWidget: Text('Neonate Age (Hours)', style: TextStyle(color: Colors.grey.shade600, fontSize: 11)),
                  sideTitles: SideTitles(
                    showTitles: true,
                    reservedSize: 22,
                    interval: maxX > 72 ? (maxX / 5).roundToDouble() : 24,
                    getTitlesWidget: (value, _) => Text(
                      '${value.toInt()}h',
                      style: TextStyle(color: Colors.grey.shade600, fontSize: 10),
                    ),
                  ),
                ),
                rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
              ),
              gridData: FlGridData(
                show: true,
                drawVerticalLine: true,
                getDrawingHorizontalLine: (value) => FlLine(color: Colors.grey.shade100, strokeWidth: 1),
                getDrawingVerticalLine: (value) => FlLine(color: Colors.grey.shade100, strokeWidth: 1),
              ),
              borderData: FlBorderData(
                show: true,
                border: Border(
                  left: BorderSide(color: Colors.grey.shade300, width: 1),
                  bottom: BorderSide(color: Colors.grey.shade300, width: 1),
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 24),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Historical Assessments',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
            ),
            IconButton(
              icon: const Icon(Icons.refresh, size: 20),
              onPressed: () {
                setState(() => _loading = true);
                _fetchTimeline();
              },
            ),
          ],
        ),
        const SizedBox(height: 10),
        // Render in reverse chronological order for timeline list
        ListView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: _assessments.length,
          itemBuilder: (context, idx) {
            // Reverse the index
            final assessment = _assessments[_assessments.length - 1 - idx];
            final date = DateTime.parse(assessment['created_at']);
            final ageHours = date.difference(widget.patient.birthDateTime).inHours;
            final score = assessment['combined_score'] as int;
            final category = assessment['category'] as String;
            final color = _getColorForCategory(category);

            return Card(
              margin: const EdgeInsets.only(bottom: 12),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
                side: BorderSide(color: color.withOpacity(0.15), width: 1),
              ),
              child: ListTile(
                leading: CircleAvatar(
                  backgroundColor: color.withOpacity(0.12),
                  child: Text(
                    '$score',
                    style: TextStyle(fontWeight: FontWeight.bold, color: color),
                  ),
                ),
                title: Text(
                  'Assessment at age ${ageHours}h',
                  style: const TextStyle(fontWeight: FontWeight.bold),
                ),
                subtitle: Text(
                  '${DateFormat('yyyy-MM-dd HH:mm').format(date.toLocal())}  •  Risk: ${category.toUpperCase()}',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                ),
                trailing: const Icon(Icons.chevron_right, size: 18),
                onTap: () => _showAssessmentDetail(assessment, ageHours),
              ),
            );
          },
        ),
      ],
    );
  }
}
