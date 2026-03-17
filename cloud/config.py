"""
cloud/config.py — Application settings via pydantic-settings.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./securesend.db"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Public base URL (used in e-mails; override in .env for production)
    PUBLIC_BASE_URL: str = ""

    # App metadata
    APP_TITLE: str = "SecureSend Cloud API"
    APP_VERSION: str = "1.0.0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
