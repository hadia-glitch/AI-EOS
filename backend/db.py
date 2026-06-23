"""Supabase client factory."""

from functools import lru_cache

from supabase import Client, create_client

from config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    print(f"[DEBUG DB] Initializing Supabase client with URL: '{settings.supabase_url}'")
    if not settings.supabase_url or not settings.supabase_service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in backend/.env"
        )
    return create_client(settings.supabase_url, settings.supabase_service_key)
