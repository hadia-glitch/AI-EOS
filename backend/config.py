from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).with_name(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""

    # ── LLM keys ─────────────────────────────────────────────────────────────
    # Primary: Gemini (Google AI Studio — free tier, AQ. or AIza keys both work)
    gemini_api_key: str = ""
    # Fallback: Groq (free tier, fast, Llama 3.3 70B)
    # Get key at: https://console.groq.com/keys
    groq_api_key: str = ""

    # ── Model names ──────────────────────────────────────────────────────────
    # Gemini: gemini-1.5-flash or gemini-2.0-flash
    gemini_model: str = "gemini-1.5-flash"
    # Groq fallback model — Llama 3.3 70B is best quality on Groq free tier
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Other settings ────────────────────────────────────────────────────────
    guidelines_dir: str = "./guidelines"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_size: int = 512
    chunk_overlap: int = 50
    rrf_k: int = 60
    retrieval_top_k: int = 10
    rerank_top_k: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()