"""Rebuild IVFFlat vector index after bulk ingestion."""

import argparse

from db import get_supabase


def rebuild_index(lists: int = 100):
    """Recreate IVFFlat index on evidence_chunks.embedding."""
    supabase = get_supabase()

    # Execute via Supabase SQL — requires service role
    sql = f"""
    DROP INDEX IF EXISTS evidence_chunks_embedding_idx;
    CREATE INDEX evidence_chunks_embedding_idx
      ON evidence_chunks USING ivfflat (embedding vector_cosine_ops)
      WITH (lists = {lists});
    """
    try:
        supabase.rpc("exec_sql", {"query": sql}).execute()
    except Exception:
        # Fallback: direct postgrest won't run DDL; document manual step
        return {
            "status": "manual_required",
            "message": (
                "Run in Supabase SQL Editor:\n"
                + sql
            ),
        }

    return {"status": "success", "lists": lists}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lists", type=int, default=100)
    args = parser.parse_args()
    print(rebuild_index(args.lists))


if __name__ == "__main__":
    main()
