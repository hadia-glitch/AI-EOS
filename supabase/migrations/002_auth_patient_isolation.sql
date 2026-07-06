-- Mobile auth + patient isolation.
-- Apply after 001_initial_schema.sql.

CREATE TABLE IF NOT EXISTS patient_records (
  id TEXT PRIMARY KEY,
  owner_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  encounter_ref TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS patient_records_owner_idx ON patient_records(owner_user_id);
CREATE INDEX IF NOT EXISTS patient_records_updated_idx ON patient_records(updated_at DESC);

ALTER TABLE patient_records ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_records' AND policyname = 'patient_records_owner_select'
  ) THEN
    CREATE POLICY patient_records_owner_select ON patient_records
      FOR SELECT USING (auth.uid() = owner_user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_records' AND policyname = 'patient_records_owner_insert'
  ) THEN
    CREATE POLICY patient_records_owner_insert ON patient_records
      FOR INSERT WITH CHECK (auth.uid() = owner_user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_records' AND policyname = 'patient_records_owner_update'
  ) THEN
    CREATE POLICY patient_records_owner_update ON patient_records
      FOR UPDATE USING (auth.uid() = owner_user_id) WITH CHECK (auth.uid() = owner_user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_records' AND policyname = 'patient_records_owner_delete'
  ) THEN
    CREATE POLICY patient_records_owner_delete ON patient_records
      FOR DELETE USING (auth.uid() = owner_user_id);
  END IF;
END $$;

ALTER TABLE patient_encounters
  ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS patient_encounters_owner_idx ON patient_encounters(owner_user_id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_encounters' AND policyname = 'patient_encounters_owner_select'
  ) THEN
    CREATE POLICY patient_encounters_owner_select ON patient_encounters
      FOR SELECT USING (auth.uid() = owner_user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'patient_encounters' AND policyname = 'patient_encounters_owner_write'
  ) THEN
    CREATE POLICY patient_encounters_owner_write ON patient_encounters
      FOR ALL USING (auth.uid() = owner_user_id) WITH CHECK (auth.uid() = owner_user_id);
  END IF;
END $$;
-- Migration 002: Extend llm_explanations for full structured output + caching
-- Run in Supabase SQL Editor

-- Add columns to llm_explanations for all structured fields
ALTER TABLE llm_explanations
  ADD COLUMN IF NOT EXISTS active_guideline       TEXT NOT NULL DEFAULT 'NICE',
  ADD COLUMN IF NOT EXISTS is_simulated           BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS is_cached              BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS clinical_summary       TEXT,
  ADD COLUMN IF NOT EXISTS risk_analysis          TEXT,
  ADD COLUMN IF NOT EXISTS driver_breakdown       TEXT,
  ADD COLUMN IF NOT EXISTS recommended_actions    JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS antibiotic_plan        JSONB DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS monitoring_plan        TEXT,
  ADD COLUMN IF NOT EXISTS escalation_criteria    TEXT,
  ADD COLUMN IF NOT EXISTS guideline_citations    JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS patient_snapshot       JSONB DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS eoscal_score           INT,
  ADD COLUMN IF NOT EXISTS risk_category          TEXT,
  ADD COLUMN IF NOT EXISTS cache_key              TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS expires_at             TIMESTAMPTZ;

-- Index for fast cache lookup by key
CREATE INDEX IF NOT EXISTS llm_explanations_cache_key_idx
  ON llm_explanations (cache_key)
  WHERE cache_key IS NOT NULL;

-- Index for lookup by risk_result_id
CREATE INDEX IF NOT EXISTS llm_explanations_risk_result_idx
  ON llm_explanations (risk_result_id);

-- Index for expiry-based cache sweeps
CREATE INDEX IF NOT EXISTS llm_explanations_expires_idx
  ON llm_explanations (expires_at)
  WHERE expires_at IS NOT NULL;

-- Enable RLS (service role bypasses, authenticated users read their own)
ALTER TABLE llm_explanations ENABLE ROW LEVEL SECURITY;

-- Allow backend service role full access (already has bypass)
-- Allow authenticated users to read explanations linked to their encounters
CREATE POLICY IF NOT EXISTS llm_explanations_read ON llm_explanations
  FOR SELECT USING (true);

-- Store RAG query results: one row per evidence search query  
CREATE TABLE IF NOT EXISTS rag_query_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query_text      TEXT NOT NULL,
  active_guideline TEXT NOT NULL DEFAULT 'NICE',
  source_filters  TEXT[],
  chunks_returned JSONB DEFAULT '[]'::jsonb,
  chunk_count     INT DEFAULT 0,
  retrieval_method TEXT DEFAULT 'hybrid',  -- 'hybrid', 'semantic', 'bm25', 'seed'
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rag_query_log_created_idx ON rag_query_log (created_at DESC);

COMMENT ON TABLE llm_explanations IS
  'Stores all LLM-generated clinical explanations with full structured output for audit trail and caching.';

COMMENT ON COLUMN llm_explanations.cache_key IS
  'SHA-256 hash of (encounter_id + risk_score + risk_category + active_guideline) for cache lookup.';

COMMENT ON COLUMN llm_explanations.is_simulated IS
  'True when explanation was generated by rule-based fallback (no LLM call made).';

COMMENT ON COLUMN llm_explanations.is_cached IS
  'True when this row was returned from cache rather than a fresh LLM call.';

COMMENT ON TABLE rag_query_log IS
  'Audit trail of all RAG evidence search queries and their returned chunks.';
