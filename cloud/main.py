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
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from sqlalchemy import text

from config import settings
from database import Base, engine
from routers.auth import router as auth_router
from routers.contacts import router as contacts_router
from routers.send import router as send_router
from routers.templates import router as templates_router
from routers.admin_org import router as admin_org_router
from routers.admin_reseller import router as admin_reseller_router
from routers.register import router as register_router
from routers.ui import router as ui_router
from routers.requests_router import router as requests_router
from routers.public import router as public_router
from routers.tracking import router as tracking_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("securesend")


# ── Security Headers Middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS only if PUBLIC_BASE_URL uses HTTPS
        if settings.PUBLIC_BASE_URL.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── Lifespan ──────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)


async def _run_migrations(conn) -> None:
    """Lightweight inline migrations for new columns (idempotent)."""
    migrations = [
        "ALTER TABLE users ADD COLUMN first_name VARCHAR(100)",
        "ALTER TABLE users ADD COLUMN last_name VARCHAR(100)",
        "ALTER TABLE history ADD COLUMN tracking_token VARCHAR(32)",
        "ALTER TABLE history ADD COLUMN opened_at TIMESTAMP",
        "ALTER TABLE history ADD COLUMN link_clicked_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)",
        "ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT FALSE",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
            log.info("Migration OK: %s", sql)
        except Exception:
            pass  # Spalte existiert bereits → ignorieren


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all tables on startup + run inline column migrations."""
    async with engine.begin() as conn:
        # Import all models so Base.metadata is populated
        import models  # noqa: F401 — triggers __init__.py imports
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)
    log.info("SecureSend Cloud API ready.")
    if settings.SECRET_KEY == "change-me-in-production":
        log.critical("SECURITY: SECRET_KEY is default value! Change immediately.")
    if not settings.SECURE_COOKIES and settings.PUBLIC_BASE_URL.startswith("https://"):
        log.warning("SECURITY: SECURE_COOKIES=False but PUBLIC_BASE_URL is HTTPS. Set SECURE_COOKIES=True.")
    if not settings.ALLOWED_ORIGINS:
        log.warning("SECURITY: ALLOWED_ORIGINS not set — using localhost fallback.")
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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — only allow configured origins (never wildcard + credentials)
_allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if not _allowed_origins:
    # Dev fallback: allow localhost only
    _allowed_origins = ["http://localhost:8001", "http://127.0.0.1:8001"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)
app.add_middleware(SecurityHeadersMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(send_router)
app.include_router(templates_router)
app.include_router(contacts_router)
app.include_router(admin_org_router)
app.include_router(admin_reseller_router)
app.include_router(register_router)
app.include_router(ui_router)
app.include_router(requests_router)
app.include_router(public_router)
app.include_router(tracking_router)


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirect browser root to the UI login page."""
    return RedirectResponse(url="/ui/login", status_code=302)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": settings.APP_VERSION}
