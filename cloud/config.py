"""
cloud/config.py — Application settings via pydantic-settings.
"""

from __future__ import annotations

import secrets
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./securesend.db"

    # JWT — SECRET_KEY MUST be set in production
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Security
    SECURE_COOKIES: bool = False  # Set to True in production (HTTPS)
    ALLOWED_ORIGINS: str = ""     # Comma-separated, e.g. "https://app.example.com"
    MAX_UPLOAD_SIZE_MB: int = 100  # Max total upload size in MB

    # ClamAV
    # Ohne laufenden clamd: false setzen (docker-compose setzt CLAMAV_ENABLED standardmäßig false).
    CLAMAV_ENABLED: bool = True
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    CLAMAV_FAIL_OPEN: bool = True  # Set to False in production to block on ClamAV failure

    # Public base URL (used in e-mails; override in .env for production)
    PUBLIC_BASE_URL: str = ""

    # true = bei unbehandelten 500-Fehlern echter Exception-Text in JSON (nur Dev/Staging)
    DEV_MODE: bool = False

    # App metadata
    APP_TITLE: str = "SecureSend Cloud API"
    APP_VERSION: str = "1.0.0"
    # CalVer YYYY.MM.XX für UI (Dashboard); leer = aus Git oder Datei VERSION
    APP_DISPLAY_VERSION: str = ""

    # SecureSend Hosted Storage (lokal oder S3-kompatibel)
    SECURESEND_STORAGE_ENABLED: bool = True
    SECURESEND_STORAGE_BACKEND: str = "local"  # local | s3
    SECURESEND_STORAGE_ROOT: str = "/data/hosted"
    SECURESEND_S3_ENDPOINT: str = ""
    SECURESEND_S3_BUCKET: str = ""
    SECURESEND_S3_ACCESS_KEY: str = ""
    SECURESEND_S3_SECRET_KEY: str = ""
    SECURESEND_S3_REGION: str = "us-east-1"

    # Ablauf: periodisches Löschen abgelaufener Sendungen (Hosted Storage)
    EXPIRY_CLEANUP_ENABLED: bool = True
    EXPIRY_CLEANUP_INTERVAL_SECONDS: int = 3600

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        if not v:
            # Auto-generate for dev, but warn
            import logging
            logging.getLogger("securesend").warning(
                "SECRET_KEY not set — using auto-generated key. "
                "Set SECRET_KEY in .env for production!"
            )
            return secrets.token_hex(32)
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v


settings = Settings()
