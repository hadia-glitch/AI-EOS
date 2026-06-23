-- NeoGuard AI — Initial Schema (Phase 1)
-- Run in Supabase SQL Editor after enabling pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;

-- Guideline document registry
CREATE TABLE IF NOT EXISTS guideline_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  source TEXT NOT NULL,
  version TEXT,
  region_tag TEXT NOT NULL,
  ingested_at TIMESTAMPTZ,
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RAG evidence chunks with pgvector embeddings (384-dim for all-MiniLM-L6-v2)
CREATE TABLE IF NOT EXISTS evidence_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID REFERENCES guideline_documents(id) ON DELETE CASCADE,
  chunk_text TEXT NOT NULL,
  embedding vector(384),
  section TEXT,
  page_number INT,
  chunk_index INT,
  source_name TEXT,
  version TEXT,
  region_tag TEXT,
  ingestion_date TIMESTAMPTZ DEFAULT now(),
  metadata JSONB DEFAULT '{}'::jsonb
);

-- IVFFlat index for approximate cosine search (build after ingesting chunks)
CREATE INDEX IF NOT EXISTS evidence_chunks_embedding_idx
  ON evidence_chunks USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

CREATE INDEX IF NOT EXISTS evidence_chunks_source_idx ON evidence_chunks(source_name);
CREATE INDEX IF NOT EXISTS evidence_chunks_region_idx ON evidence_chunks(region_tag);

-- Institutions
CREATE TABLE IF NOT EXISTS institutions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  region TEXT,
  country TEXT,
  guideline_preference TEXT DEFAULT 'NICE',
  local_protocol_id UUID,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Users (extends Supabase auth.users via id reference)
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY,
  institution_id UUID REFERENCES institutions(id),
  role TEXT NOT NULL DEFAULT 'nurse',
  email_hash TEXT,
  mfa_enabled BOOLEAN DEFAULT false,
  last_login TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Patient encounters (de-identified)
CREATE TABLE IF NOT EXISTS patient_encounters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id UUID REFERENCES institutions(id),
  encounter_ref TEXT NOT NULL,
  gestational_age_days INT,
  birth_weight_g INT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Clinical assessments per event
CREATE TABLE IF NOT EXISTS clinical_assessments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id UUID REFERENCES patient_encounters(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL DEFAULT 'initial',
  maternal_data JSONB DEFAULT '{}'::jsonb,
  neonatal_data JSONB DEFAULT '{}'::jsonb,
  lab_data JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Risk calculation results
CREATE TABLE IF NOT EXISTS risk_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id UUID REFERENCES patient_encounters(id) ON DELETE CASCADE,
  assessment_id UUID REFERENCES clinical_assessments(id),
  layer1_score INT DEFAULT 0,
  layer2_score INT DEFAULT 0,
  layer3_score INT DEFAULT 0,
  combined_score INT DEFAULT 0,
  probability_per_1000 NUMERIC(10,4),
  category TEXT NOT NULL,
  drivers JSONB DEFAULT '[]'::jsonb,
  guideline_used TEXT DEFAULT 'NICE',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- LLM explanations with full audit trail
CREATE TABLE IF NOT EXISTS llm_explanations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  risk_result_id UUID REFERENCES risk_results(id) ON DELETE CASCADE,
  gemini_model_version TEXT NOT NULL DEFAULT 'gemini-2.0-flash',
  rag_chunks_used JSONB DEFAULT '[]'::jsonb,
  response_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Antibiotic stewardship records
CREATE TABLE IF NOT EXISTS antibiotic_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id UUID REFERENCES patient_encounters(id) ON DELETE CASCADE,
  drug TEXT NOT NULL,
  dose TEXT,
  route TEXT,
  start_time TIMESTAMPTZ,
  stop_time TIMESTAMPTZ,
  indication TEXT,
  stop_reason TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Clinical alerts
CREATE TABLE IF NOT EXISTS alerts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id UUID REFERENCES patient_encounters(id) ON DELETE CASCADE,
  alert_type TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'MEDIUM',
  created_at TIMESTAMPTZ DEFAULT now(),
  acknowledged_at TIMESTAMPTZ,
  acknowledged_by UUID,
  action_taken TEXT
);

-- Immutable audit log with chain hash
CREATE TABLE IF NOT EXISTS audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id UUID DEFAULT gen_random_uuid(),
  timestamp TIMESTAMPTZ DEFAULT now(),
  user_id UUID,
  action_type TEXT NOT NULL,
  resource_id TEXT,
  change_summary JSONB DEFAULT '{}'::jsonb,
  rag_chunks_used JSONB,
  gemini_model_version TEXT,
  chain_hash TEXT,
  outcome TEXT DEFAULT 'SUCCESS'
);

CREATE INDEX IF NOT EXISTS audit_logs_timestamp_idx ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS audit_logs_action_idx ON audit_logs(action_type);

-- RLS policies (basic — enable row isolation per institution)
ALTER TABLE patient_encounters ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_chunks ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS; anon/authenticated read evidence for search
CREATE POLICY evidence_chunks_read ON evidence_chunks
  FOR SELECT USING (true);
