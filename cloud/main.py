"""
cloud/main.py — SecureSend Cloud API entry point.

Run locally:
    cd cloud
    uvicorn main:app --reload --port 8000

sys.path is patched so that `from core.storage import ...` works whether
the service is started from the cloud/ directory or from the project root.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# ── Path patch: make core.* importable ───────────────────────────────────────
# Insert the project root (parent of this file's directory) at position 0
# so that `import core.storage` resolves to <root>/core/storage.py.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Imports ───────────────────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import Base, engine
from routers.auth import router as auth_router
from routers.contacts import router as contacts_router
from routers.send import router as send_router
from routers.admin_org import router as admin_org_router
from routers.admin_reseller import router as admin_reseller_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("securesend")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all tables on startup (use Alembic for production migrations)."""
    async with engine.begin() as conn:
        # Import all models so Base.metadata is populated
        import models  # noqa: F401 — triggers __init__.py imports
        await conn.run_sync(Base.metadata.create_all)
    log.info("SecureSend Cloud API ready.")
    yield
    await engine.dispose()
    log.info("SecureSend Cloud API shut down.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=(
        "Multi-tenant API for SecureSend — secure file & message delivery "
        "via cloud storage (Nextcloud / OneDrive) with JWT authentication."
    ),
    lifespan=lifespan,
)

# Allow all origins in development; tighten for production via env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(send_router)
app.include_router(contacts_router)
app.include_router(admin_org_router)
app.include_router(admin_reseller_router)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": settings.APP_VERSION}
