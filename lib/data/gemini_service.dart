import 'package:google_generative_ai/google_generative_ai.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../domain/eoscal_calculator.dart';
import 'guidelines_data.dart';

class GeminiService {
  static const String apiKeyKey = 'gemini_api_key';

  static Future<String?> getApiKey() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(apiKeyKey);
  }

  static Future<void> saveApiKey(String key) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(apiKeyKey, key);
  }

  static Future<void> deleteApiKey() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(apiKeyKey);
  }

  static Future<String> generateExplanation({
    required PatientParameters patient,
    required EoscalResult result,
    required String activeGuideline,
    required List<GuidelineChunk> retrievedChunks,
  }) async {
    final apiKey = await getApiKey();

    if (apiKey == null || apiKey.trim().isEmpty) {
      // Return simulated high-fidelity medical response for offline demo mode
      await Future.delayed(const Duration(seconds: 2)); // Simulate network latency
      return _generateSimulatedExplanation(patient, result, activeGuideline, retrievedChunks);
    }

    try {
      final model = GenerativeModel(
        model: 'gemini-1.5-flash',
        apiKey: apiKey,
      );

      final ageHours = DateTime.now().difference(patient.birthDateTime).inHours;

      final prompt = '''
You are NeoGuard AI, an expert Neonatal Clinical Decision Support System.
You have analyzed a neonate using the 3-layer EOSCAL 2024 risk engine.

PATIENT CLINICAL SUMMARY:
- Name: ${patient.name} (MRN: ${patient.mrn})
- Gestational Age: ${patient.gestationalAgeWeeks} weeks
- Age at Evaluation: $ageHours hours
- Delivery Mode: ${patient.deliveryMode}

EOSCAL 2024 SCORE: ${result.totalScore} / 16 (Risk Level: ${result.riskCategory.displayName})

LAYER 1: Maternal Risk Drivers:
${result.layer1Drivers.isEmpty ? '- None' : result.layer1Drivers.map((d) => '  * ${d.name} (${d.points} pts): ${d.reason}').join('\n')}

LAYER 2: Neonatal Clinical Drivers:
${result.layer2Drivers.isEmpty ? '- None' : result.layer2Drivers.map((d) => '  * ${d.name} (${d.points} pts): ${d.reason}').join('\n')}

LAYER 3: Laboratory Evidence Drivers:
${result.layer3Drivers.isEmpty ? '- None' : result.layer3Drivers.map((d) => '  * ${d.name} (${d.points} pts): ${d.reason}').join('\n')}

ACTIVE GUIDELINE: $activeGuideline

RETRIEVED GUIDELINE KNOWLEDGE CHUNKS (RAG EVIDENCE):
${retrievedChunks.map((c) => 'Source: ${c.source} | Section: ${c.section}\nContent: ${c.content}').join('\n\n')}

TASK:
Provide a comprehensive, clear, structured clinical explanation of this patient's EOS Sepsis Risk in Markdown.

Format the output strictly as follows:

# Clinical Risk Assessment
[Write a 2-3 sentence overview of the patient's condition, summarizing the severity of their early-onset sepsis risk and explaining the medical significance of their total score of ${result.totalScore} under the $activeGuideline framework.]

## Driver Analysis
[Perform a breakdown of the key factors driving this risk score. Explain the physiological importance of:
- Any maternal risk factor (e.g. why ROM duration, fever, or GBS positive without adequate antibiotic coverage contributes to infection risk).
- Neonatal clinical signs (respiratory, cardiovascular, temperature, or neuro signs).
- Laboratory markers (explain why findings like WBC counts, I:T ratios, CRP, or cultures confirm or lower suspicion of sepsis).]

## Recommended Clinical Action Plan
[Provide a structured, step-by-step action plan in accordance with the $activeGuideline guideline:
1. First-line treatments (e.g. specific antibiotic dosages or combinations).
2. Monitoring frequencies (vital signs, clinical symptoms).
3. Laboratory follow-ups (next CRP draw, blood culture review).
4. NICU transfer or referral criteria.]

## Guideline Evidence & Citations
[Detail exactly which parts of the retrieved knowledge base chunks support this plan, citing specific sections of the $activeGuideline guideline. List the specific sources at the bottom (e.g., NICE NG195, AAP 2023, or WHO Newborn Guidelines).]

Ensure the tone is highly professional, objective, and clinical. Avoid vague statements. Maintain confidentiality (do not output MRN/Name in a way that compromises data guidelines, keep details clinical).
''';

      final content = [Content.text(prompt)];
      final response = await model.generateContent(content);
      return response.text ?? 'Error: No explanation returned from Gemini API.';
    } catch (e) {
      return 'Error generating explanation: $e\n\nFallback to offline explanation:\n\n${_generateSimulatedExplanation(patient, result, activeGuideline, retrievedChunks)}';
    }
  }

  static String _generateSimulatedExplanation(
    PatientParameters patient,
    EoscalResult result,
    String activeGuideline,
    List<GuidelineChunk> retrievedChunks,
  ) {
    final ageHours = DateTime.now().difference(patient.birthDateTime).inHours;
    final riskStr = result.riskCategory.displayName;

    String recommendation = EoscalCalculator.getRecommendation(
      guideline: activeGuideline,
      riskCategory: result.riskCategory,
    );

    return '''
# Clinical Risk Assessment (Simulated)

The patient, a ${patient.gestationalAgeWeeks}-week-old neonate evaluated at $ageHours hours of life, presents with a total EOSCAL 2024 score of **${result.totalScore}**, classifying them under the **$riskStr** category. Under the **$activeGuideline** guideline framework, this score represents a ${result.totalScore >= 7 ? 'critical' : result.totalScore >= 4 ? 'moderate' : 'low'} probability of early-onset sepsis. immediate clinical surveillance and ${result.totalScore >= 4 ? 'pharmacological intervention' : 'routine monitoring'} are advised.

## Driver Analysis

- **Maternal Factors:** The maternal risk profile shows ${result.layer1Drivers.isEmpty ? 'no significant risk factors.' : 'key contributors: ' + result.layer1Drivers.map((d) => d.name).join(', ') + '.'} These factors introduce a baseline bacterial exposure risk, particularly due to ${patient.romHours >= 18 ? 'prolonged rupture of membranes (${patient.romHours} hours) allowing ascending infection' : ''} ${patient.gbsPositive && !patient.adequateIntrapartumAntibiotics ? 'and lack of adequate GBS intrapartum antibiotic prophylaxis.' : ''}
- **Neonatal Status:** Clinical signs are ${result.layer2Drivers.isEmpty ? 'entirely stable.' : 'notable for: ' + result.layer2Drivers.map((d) => d.name).join(', ') + '.'} The presence of ${patient.respiratoryDistress != 'None' ? 'respiratory compromise' : ''} ${patient.oxygenNeed != 'None' ? 'requiring support' : ''} is a hallmark sign of systemic infection and demands close physiological monitoring.
- **Laboratory Indicators:** Laboratory values are ${result.layer3Drivers.isEmpty ? 'unremarkable or pending.' : 'significant for: ' + result.layer3Drivers.map((d) => d.name).join(', ') + '.'} ${patient.crpLevel != null && patient.crpLevel! >= 10 ? 'An elevated CRP of ${patient.crpLevel} mg/L indicates active systemic inflammation.' : ''} ${patient.bloodCulturePositive == true ? 'The positive blood culture represents confirmed bacteremia, the strongest diagnostic indicator of neonatal sepsis.' : ''}

## Recommended Clinical Action Plan

Based on the **$activeGuideline** framework, the recommended management plan is:
1. **Immediate Therapy:** $recommendation
2. **Physiological Monitoring:** Perform clinical assessments (heart rate, respiratory rate, temperature, work of breathing, and perfusion) every ${result.totalScore >= 7 ? '1-2 hours' : result.totalScore >= 4 ? '4 hours' : '12 hours'}.
3. **Repeat Labs:** ${result.totalScore >= 4 ? 'Draw follow-up CRP in 18-24 hours to monitor trend. Review blood culture status at 24 and 48 hours.' : 'No immediate laboratory repeat needed unless clinical signs emerge.'}
4. **NICU / Referral:** ${result.totalScore >= 7 || patient.gestationalAgeWeeks < 35.0 ? 'Transfer patient immediately to the Neonatal Intensive Care Unit (NICU) for continuous monitoring and IV access support.' : 'Maintain newborn on mother-baby ward with close nurse surveillance.'}

## Guideline Evidence & Citations

- **Evidence from Guidelines:**
  ${retrievedChunks.isNotEmpty ? retrievedChunks.map((c) => '* **${c.source} (${c.section}):** ${c.content.substring(0, c.content.length > 150 ? 150 : c.content.length)}...').join('\n  ') : '* No specific RAG chunks retrieved.'}
- **Primary Sources:**
  - NICE Guideline NG195: Neonatal Infection (Early-onset): Antibiotics for Prevention and Treatment.
  - American Academy of Pediatrics (AAP) 2023 Clinical Report: Management of Infants at Risk for Early-Onset Sepsis.
  - World Health Organization (WHO) Pocket Book of Hospital Care for Children (Sepsis Section).
''';
  }
}
