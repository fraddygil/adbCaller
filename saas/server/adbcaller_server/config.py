from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    public_url: str = "http://127.0.0.1:8000"
    database_url: str = (
        "postgresql+asyncpg://adbcaller_app:adbcaller_app@127.0.0.1:54329/adbcaller"
    )
    migration_database_url: str = (
        "postgresql+asyncpg://adbcaller:adbcaller@127.0.0.1:54329/adbcaller"
    )
    session_secret: str = "development-only-session-secret"
    token_pepper: str = "development-only-token-pepper"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    session_ttl_seconds: int = Field(default=28_800, ge=300, le=604_800)
    pairing_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    conference_call_timeout_seconds: int = Field(default=45, ge=10, le=300)
    conference_merge_attempts: int = Field(default=5, ge=1, le=10)
    max_conference_calls: int = Field(default=4, ge=2, le=20)

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.app_env == "production":
            if not self.public_url.startswith("https://"):
                raise ValueError("PUBLIC_URL debe usar HTTPS en producción")
            if self.session_secret.startswith("development-"):
                raise ValueError("SESSION_SECRET de desarrollo no puede usarse en producción")
            if self.token_pepper.startswith("development-"):
                raise ValueError("TOKEN_PEPPER de desarrollo no puede usarse en producción")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
