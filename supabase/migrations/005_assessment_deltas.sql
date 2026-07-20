-- Phase 1.1: field-level deltas between consecutive clinical assessments.
-- View (not table) — computed on read from clinical_assessments + risk_results.

CREATE OR REPLACE VIEW assessment_deltas AS
WITH scored AS (
  SELECT
    ca.id AS assessment_id,
    ca.encounter_id,
    ca.created_at,
    ca.lab_data,
    ca.neonatal_data,
    rr.combined_score
  FROM clinical_assessments ca
  LEFT JOIN risk_results rr ON rr.assessment_id = ca.id
),
with_lag AS (
  SELECT
    assessment_id,
    encounter_id,
    created_at,
    combined_score,
    (lab_data->>'crp_level')::numeric AS crp_level,
    (neonatal_data->>'neonatal_temperature')::numeric AS neonatal_temperature,
    LAG(created_at) OVER (
      PARTITION BY encounter_id ORDER BY created_at
    ) AS prev_created_at,
    LAG((lab_data->>'crp_level')::numeric) OVER (
      PARTITION BY encounter_id ORDER BY created_at
    ) AS prev_crp,
    LAG((neonatal_data->>'neonatal_temperature')::numeric) OVER (
      PARTITION BY encounter_id ORDER BY created_at
    ) AS prev_temp,
    LAG(combined_score) OVER (
      PARTITION BY encounter_id ORDER BY created_at
    ) AS previous_combined_score
  FROM scored
)
SELECT
  assessment_id,
  encounter_id,
  created_at,
  CASE
    WHEN crp_level IS NOT NULL AND prev_crp IS NOT NULL
    THEN crp_level - prev_crp
    ELSE NULL
  END AS crp_delta,
  CASE
    WHEN neonatal_temperature IS NOT NULL AND prev_temp IS NOT NULL
    THEN neonatal_temperature - prev_temp
    ELSE NULL
  END AS temp_delta,
  CASE
    WHEN prev_created_at IS NOT NULL
    THEN EXTRACT(EPOCH FROM (created_at - prev_created_at)) / 3600
    ELSE NULL
  END AS hours_since_last,
  previous_combined_score,
  CASE
    WHEN combined_score IS NOT NULL AND previous_combined_score IS NOT NULL
    THEN combined_score - previous_combined_score
    ELSE NULL
  END AS score_delta
FROM with_lag;
