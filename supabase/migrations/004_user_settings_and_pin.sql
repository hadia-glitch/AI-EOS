-- Migration 004: User Settings and PIN, plus Patient Encounters metadata
-- Run this in your Supabase SQL Editor

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS default_guideline TEXT DEFAULT 'NICE';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS pin TEXT;

ALTER TABLE public.patient_encounters ADD COLUMN IF NOT EXISTS patient_name TEXT;
ALTER TABLE public.patient_encounters ADD COLUMN IF NOT EXISTS birth_date_time TIMESTAMPTZ;

-- Ensure the default Demo Neonatal Unit exists
INSERT INTO public.institutions (id, name, region, country, guideline_preference)
VALUES ('00000000-0000-0000-0000-000000000001', 'Demo Neonatal Unit', 'Sindh', 'Pakistan', 'NICE')
ON CONFLICT (id) DO NOTHING;
