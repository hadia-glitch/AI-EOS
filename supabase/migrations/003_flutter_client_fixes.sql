-- 003_flutter_client_fixes.sql
-- Apply after 001 and 002.
-- Fixes needed for Flutter client to write to all tables correctly.

-- 1. alerts.id: change from UUID to TEXT so we can use deterministic
--    composite keys like '<encounter_uuid>_risk' from the Flutter client.
ALTER TABLE alerts ALTER COLUMN id TYPE TEXT USING id::TEXT;

-- 2. patient_encounters: birth_weight_g is nullable (we don't collect it in the
--    Flutter form yet, so inserts would fail if NOT NULL were ever added).
--    Column already nullable by default — no change needed.

-- 3. RLS for alerts: allow authenticated users to insert/upsert their own alerts.
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'alerts' AND policyname = 'alerts_owner_all'
  ) THEN
    CREATE POLICY alerts_owner_all ON alerts
      FOR ALL
      USING (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      )
      WITH CHECK (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      );
  END IF;
END $$;

-- 4. RLS for clinical_assessments and risk_results (scoped via encounter ownership).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'clinical_assessments' AND policyname = 'clinical_assessments_owner_all'
  ) THEN
    CREATE POLICY clinical_assessments_owner_all ON clinical_assessments
      FOR ALL
      USING (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      )
      WITH CHECK (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'risk_results' AND policyname = 'risk_results_owner_all'
  ) THEN
    CREATE POLICY risk_results_owner_all ON risk_results
      FOR ALL
      USING (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      )
      WITH CHECK (
        encounter_id IN (
          SELECT id FROM patient_encounters WHERE owner_user_id = auth.uid()
        )
      );
  END IF;
END $$;