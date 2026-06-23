"""Audit logging with chain hash."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from db import get_supabase


def _last_chain_hash(supabase) -> str:
    try:
        resp = (
            supabase.table("audit_logs")
            .select("chain_hash")
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data and resp.data[0].get("chain_hash"):
            return resp.data[0]["chain_hash"]
    except Exception:
        pass
    return "genesis"


def log_audit_event(
    action_type: str,
    resource_id: str | None = None,
    user_id: str | None = None,
    change_summary: dict | None = None,
    rag_chunks_used: list | None = None,
    gemini_model_version: str | None = None,
    outcome: str = "SUCCESS",
) -> dict | None:
    try:
        supabase = get_supabase()
        prev_hash = _last_chain_hash(supabase)
        timestamp = datetime.now(timezone.utc).isoformat()

        payload = {
            "timestamp": timestamp,
            "action_type": action_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "change_summary": change_summary or {},
            "rag_chunks_used": rag_chunks_used,
            "gemini_model_version": gemini_model_version,
            "outcome": outcome,
        }
        chain_input = prev_hash + json.dumps(payload, sort_keys=True, default=str)
        chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()
        payload["chain_hash"] = chain_hash

        resp = supabase.table("audit_logs").insert(payload).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None
