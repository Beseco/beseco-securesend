"""
cloud/database.py — Async SQLAlchemy engine + session factory.

Supports:
  - SQLite (dev/test):  sqlite+aiosqlite:///./securesend.db
  - PostgreSQL (prod):  postgresql+asyncpg://user:pass@host/db
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

connect_args: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Base declarative class (all models inherit from this)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Dependency helper for FastAPI routes
# ---------------------------------------------------------------------------

async def get_db() -> AsyncSession:  # type: ignore[return]
    """Yield an async DB session; close it after the request."""
    async with async_session() as session:
        yield session
