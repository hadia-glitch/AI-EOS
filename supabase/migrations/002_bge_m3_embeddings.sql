-- OPTIONAL migration: upgrade evidence_chunks.embedding from all-MiniLM-L6-v2
-- (384-dim) to BAAI/bge-m3 (1024-dim).
--
-- Do NOT run this until you have also:
--   1. Set EMBEDDING_MODEL=BAAI/bge-m3 and EMBEDDING_DIM=1024 in backend/.env
--   2. Installed the model locally (first ingest run will download it —
--      it is ~2.2GB, larger than MiniLM's ~90MB, budget disk/RAM accordingly)
--
-- This migration DROPS all existing embeddings (they are the wrong
-- dimension and wrong vector space to mix with new ones) and requires a
-- full re-ingest immediately after. Run in this order:
--
--   1. psql -f 002_bge_m3_embeddings.sql   (this file)
--   2. python -m rag.ingest --clear         (re-embeds every guideline PDF)
--   3. python -m rag.rebuild_index          (rebuilds the ivfflat index)

BEGIN;

-- Drop the ivfflat index first -- it is bound to the old vector dimension
-- and cannot be altered in place.
DROP INDEX IF EXISTS evidence_chunks_embedding_idx;

-- Clear existing (now-orphaned) chunks and their parent document rows.
-- Old 384-dim embeddings are not comparable to new 1024-dim ones -- leaving
-- them in place would silently corrupt cosine similarity for any query
-- that happens to hit both dimensionalities.
DELETE FROM evidence_chunks;
DELETE FROM guideline_documents;

-- Change the column type. Postgres can't cast vector(384) -> vector(1024)
-- meaningfully (they're different vector spaces, not just different sizes),
-- so this is a hard type change on an already-emptied column.
ALTER TABLE evidence_chunks
  ALTER COLUMN embedding TYPE vector(1024);

COMMIT;

-- After this completes, re-run ingestion (step 2/3 above) before serving
-- any traffic -- the chunk store will otherwise report 0 chunks and the
-- app will silently fall back to the small seed_chunks.py dataset.