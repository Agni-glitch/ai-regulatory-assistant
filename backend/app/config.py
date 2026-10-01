"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Azure OpenAI ---
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4.1"
    azure_openai_embedding_deployment: str = "text-embedding-ada-002"

    # --- Vector store ---
    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    chroma_collection: str = "ai_regulations"
    # Used only when chroma_host is empty (local dev without a server).
    chroma_persist_dir: str = "data/chroma"

    # --- Retrieval / agent ---
    retrieval_top_k: int = 6
    agent_max_steps: int = 6

    # --- Celery / Redis (async ingestion jobs) ---
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    # Where the worker finds the corpus and ingestion code (set in compose).
    docs_dir: str = "document"
    ingestion_dir: str = "ingestion"

    # --- Server ---
    backend_port: int = 8080
    cors_origins: str = "http://localhost,http://localhost:5173"

    # --- Auth / RBAC ---
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480
    admin_password: str = "admin123"
    privs_password: str = "privs123"
    user_password: str = "user123"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def azure_configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
