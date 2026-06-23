from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""
    gemini_api_key: str = ""
    guidelines_dir: str = "./guidelines"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    gemini_model: str = "gemini-1.5-flash"
    chunk_size: int = 512
    chunk_overlap: int = 50
    rrf_k: int = 60
    retrieval_top_k: int = 10
    rerank_top_k: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()