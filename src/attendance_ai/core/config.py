"""Application settings, read from environment variables (and the project's .env file in development)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All runtime configuration. Connection URLs and keys are SecretStr so they never reach logs."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    service_module: str = "attendance"

    # PostgreSQL: one URL per database role (docs/design.md, section 11). Passwords come from the
    # environment only; the defaults deliberately carry none.
    database_url: SecretStr = SecretStr("postgresql+psycopg://ai_app@localhost:5432/attendance_ai")
    database_query_url: SecretStr = SecretStr("postgresql+psycopg://ai_query@localhost:5432/attendance_ai")
    database_owner_url: SecretStr = SecretStr("postgresql+psycopg://ai_owner@localhost:5432/attendance_ai")
    database_admin_url: SecretStr | None = None
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_pool_size: int = Field(default=5, ge=1, le=50)

    # Redis: job queue (Dramatiq), answer cache and LLM provider health.
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    worker_threads: int = Field(default=2, ge=1, le=16)

    # Ingestion: original uploads, size limit, OCR.
    storage_dir: Path = PROJECT_ROOT / "var" / "storage"
    upload_max_mb: int = Field(default=20, ge=1, le=200)
    tesseract_cmd: str | None = None
    # "queue": the worker processes each upload from the Redis queue. "inline": the upload request
    # processes it, for hosts without a background worker (the Vercel deployment).
    ingestion_mode: Literal["queue", "inline"] = "queue"

    # Qdrant (vector store) and the local embedding models (FastEmbed).
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "attendance_chunks"
    model_cache_dir: Path = PROJECT_ROOT / "var" / "models"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "Qdrant/bm25"
    rerank_model: str | None = "Xenova/ms-marco-MiniLM-L-6-v2"
    search_candidates: int = Field(default=20, ge=1, le=100)
    search_top_k: int = Field(default=5, ge=1, le=20)
    rerank_min_score: float = -4.0

    # LLM provider chain: primary model, then fallback model, then a controlled error.
    llm_provider: Literal["openrouter", "openai"] = "openrouter"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_fallback_model: str | None = "openai/gpt-4.1-nano"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_fallback_model: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=700, ge=64, le=4000)

    # Question answering.
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    query_row_cap: int = Field(default=200, ge=1, le=5000)
    evidence_row_cap: int = Field(default=2000, ge=1, le=20000)

    # Approved feedback examples: how close a new question must be to an example's question (cosine
    # similarity of the local embeddings) for the example to be applied.
    feedback_min_similarity: float = Field(default=0.85, ge=0.5, le=1.0)

    # Exports are built synchronously, up to this many records.
    export_row_cap: int = Field(default=10_000, ge=1, le=50_000)

    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "attendance-ai"
    jwt_audience: str = "attendance-ai-api"
    jwt_ttl_minutes: int = Field(default=60, ge=5, le=24 * 60)
    auth_dev_login_enabled: bool = False
    # When set, the development sign-in also asks for this code, so a public demo deployment can be
    # shared with the people who have the code and nobody else.
    demo_access_code: SecretStr | None = None

    seed_file: Path = PROJECT_ROOT / "sample_data" / "seed" / "tenants.json"
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    @model_validator(mode="after")
    def _check_security(self) -> Settings:
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be set to at least 32 characters.")
        if self.app_env == "prod" and self.auth_dev_login_enabled:
            raise ValueError("AUTH_DEV_LOGIN_ENABLED must be false when APP_ENV=prod.")
        code = self.demo_access_code.get_secret_value() if self.demo_access_code else ""
        if code and len(code) < 8:
            # Sign-in attempts are not rate limited, so a short code could be guessed.
            raise ValueError("DEMO_ACCESS_CODE must be at least 8 characters.")
        return self

    @property
    def llm_api_key(self) -> SecretStr | None:
        return self.openrouter_api_key if self.llm_provider == "openrouter" else self.openai_api_key

    @property
    def llm_base_url(self) -> str:
        return self.openrouter_base_url if self.llm_provider == "openrouter" else self.openai_base_url

    @property
    def llm_models(self) -> tuple[str, ...]:
        """Models to try, in order."""
        if self.llm_provider == "openrouter":
            chain = (self.openrouter_model, self.openrouter_fallback_model)
        else:
            chain = (self.openai_model, self.openai_fallback_model)
        return tuple(model for model in chain if model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
