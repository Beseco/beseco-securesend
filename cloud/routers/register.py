"""
cloud/routers/register.py — Self-registration endpoints (public).

GET  /register/{org_slug}/info  → org name + allowed domains (for the form)
POST /register                   → create inactive user + send verification email
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from config import settings
from database import get_db

logger = logging.getLogger(__name__)
from dependencies import hash_password
from models.organization import Organization
from models.reseller import Reseller
from models.shared import EmailVerification
from models.user import User, UserRole

router = APIRouter(prefix="/register", tags=["register"])

_TOKEN_TTL_HOURS = 24


class RegisterRequest(BaseModel):
    org_slug: str
    email: str
    password: str


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _load_org(org_slug: str, db: AsyncSession) -> Organization:
    result = await db.execute(select(Organization).where(Organization.slug == org_slug))
    org = result.scalar_one_or_none()
    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organisation nicht gefunden")
    settings = org.settings_json or {}
    if not settings.get("allow_registration"):
        raise HTTPException(
            status_code=403,
            detail="Registrierung für diese Organisation ist nicht aktiviert",
        )
    return org


async def _smtp_cfg(org: Organization, db: AsyncSession) -> dict | None:
    """Return SMTP config: org first, reseller as fallback."""
    cfg = (org.settings_json or {}).get("smtp")
    if cfg:
        return cfg
    res = await db.execute(select(Reseller).where(Reseller.id == org.reseller_id))
    reseller = res.scalar_one_or_none()
    if reseller and reseller.settings_json:
        return reseller.settings_json.get("smtp")
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/{org_slug}/info")
async def org_registration_info(
    org_slug: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Public: return registration settings for the given org slug."""
    org = await _load_org(org_slug, db)
    settings = org.settings_json or {}
    return {
        "org_name": org.name,
        "allowed_domains": settings.get("allowed_domains", []),
    }


@router.post("", status_code=202)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create an inactive user account and send a verification email.
    Returns 202 Accepted — the client should tell the user to check their inbox.
    """
    org = await _load_org(body.org_slug, db)
    settings = org.settings_json or {}

    # ── Domain check ──────────────────────────────────────────────────────
    allowed_domains: list[str] = settings.get("allowed_domains", [])
    if allowed_domains:
        if "@" not in body.email:
            raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
        email_domain = body.email.split("@")[-1].lower()
        clean_allowed = [d.lower().lstrip("@") for d in allowed_domains]
        if email_domain not in clean_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"E-Mail-Domain nicht erlaubt. Erlaubte Domains: "
                f"{', '.join('@' + d for d in clean_allowed)}",
            )

    # ── Password length ───────────────────────────────────────────────────
    if len(body.password) < 12:
        raise HTTPException(
            status_code=400, detail="Passwort muss mindestens 12 Zeichen haben"
        )

    # ── Duplicate check ───────────────────────────────────────────────────
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        # Return 202 anyway to avoid user enumeration
        return {
            "detail": "Bitte prüfen Sie Ihre E-Mail und bestätigen Sie Ihre Adresse."
        }

    # ── Create inactive user ──────────────────────────────────────────────
    user = User(
        id=str(uuid.uuid4()),
        org_id=org.id,
        email=body.email,
        password_hash=hash_password(body.password),
        role=UserRole.org_user,
        is_active=False,
    )
    db.add(user)

    # ── Create verification token ─────────────────────────────────────────
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=_TOKEN_TTL_HOURS)
    verification = EmailVerification(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(verification)
    await db.commit()

    # ── Send verification email ───────────────────────────────────────────
    smtp_cfg = await _smtp_cfg(org, db)
    if smtp_cfg:
        try:
            import asyncio
            from core.email import send_email  # type: ignore[import]

            base = settings.PUBLIC_BASE_URL.rstrip("/") or str(request.base_url).rstrip(
                "/"
            )
            verify_url = f"{base}/ui/verify-email?token={token}"
            html_body = (
                f"<p>Hallo,</p>"
                f"<p>bitte bestätigen Sie Ihre E-Mail-Adresse für <strong>{org.name}</strong> – SecureSend:</p>"
                f"<p><a href='{verify_url}' style='color:#1a56db;font-weight:bold;'>"
                f"E-Mail-Adresse bestätigen</a></p>"
                f"<p style='color:#64748b;font-size:0.85em;'>"
                f"Dieser Link ist {_TOKEN_TTL_HOURS} Stunden gültig. "
                f"Falls Sie sich nicht registriert haben, können Sie diese E-Mail ignorieren.</p>"
            )
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: send_email(
                    smtp_cfg,
                    body.email,
                    f"E-Mail bestätigen – {org.name} SecureSend",
                    html_body,
                ),
            )
        except Exception as exc:
            logger.warning("Verification email failed for %s: %s", body.email, exc)

    return {"detail": "Bitte prüfen Sie Ihre E-Mail und bestätigen Sie Ihre Adresse."}
