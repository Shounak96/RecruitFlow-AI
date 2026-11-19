from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = (
        "postgresql+psycopg://recruitflow:change-me-for-local-development"
        "@localhost:5432/recruitflow"
    )
    frontend_origin: AnyHttpUrl = "http://localhost:3000"
    local_owner_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    resume_storage_path: Path = Path("../data/resumes")
    ai_provider: Literal["gemini", "openrouter"] = "gemini"
    ai_fallback_enabled: bool = False
    ai_fallback_provider: Literal["gemini", "openrouter"] = "openrouter"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = 30.0
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_timeout_seconds: float = 30.0
    gmail_oauth_client_id: str | None = None
    gmail_oauth_client_secret: SecretStr | None = None
    gmail_oauth_redirect_uri: AnyHttpUrl = (
        "http://localhost:8001/api/gmail/oauth/callback"
    )
    gmail_token_encryption_key: SecretStr | None = None
    gmail_http_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

