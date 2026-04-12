"""
cloud/routers/requests_router.py — Authenticated endpoints for creating phone and
upload requests.

POST /requests/phone   — create PhoneRequest, email contact
POST /requests/upload  — create UploadRequest, email recipient
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from dependencies import get_current_user, org_user_required
from models.organization import Organization
from models.reseller import Reseller
from models.shared import Contact, PhoneRequest, UploadRequest
from models.user import User

log = logging.getLogger("securesend")

router = APIRouter(prefix="/requests", tags=["requests"])


# ── GET /requests/upload ──────────────────────────────────────────────────────

@router.get("/upload")
async def list_upload_requests(
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return all UploadRequests created by the current user, newest first."""
    result = await db.execute(
        select(UploadRequest)
        .where(UploadRequest.sender_id == current_user.id)
        .order_by(UploadRequest.created_at.desc())
    )
    rows = result.scalars().all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = []
    for r in rows:
        effective_status = r.status
        if effective_status == "pending" and r.expires_at < now:
            effective_status = "expired"
        row: dict = {
            "id": r.id,
            "recipient_email": r.recipient_email,
            "recipient_name": r.recipient_name or "",
            "status": effective_status,
            "result_url": r.result_url or "",
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
        }
        if effective_status == "pending":
            row["token"] = r.token
        out.append(row)
    return out


def _base_url(request: Request) -> str:
    """Return PUBLIC_BASE_URL from settings, or derive from the incoming request."""
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


# ── Pydantic bodies ───────────────────────────────────────────────────────────

class PhoneRequestBody(BaseModel):
    contact_id: str


class UploadRequestBody(BaseModel):
    recipient_email: str
    recipient_name: Optional[str] = None
    message: Optional[str] = None
    days: int = 14


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_smtp_cfg(sender: User, db: AsyncSession) -> Optional[dict]:
    """Return SMTP config: org first, then reseller fallback. None if not found."""
    # Load org with settings_json
    org_result = await db.execute(
        select(Organization).where(Organization.id == sender.org_id)
    )
    org = org_result.scalar_one_or_none()
    if org and org.settings_json and org.settings_json.get("smtp"):
        return org.settings_json["smtp"]

    # Fallback: reseller
    if org:
        res_result = await db.execute(
            select(Reseller).where(Reseller.id == org.reseller_id)
        )
        reseller = res_result.scalar_one_or_none()
        if reseller and reseller.settings_json and reseller.settings_json.get("smtp"):
            return reseller.settings_json["smtp"]

    return None


async def _send_email_async(cfg: dict, to_email: str, subject: str, body_html: str) -> None:
    """Run the sync send_email function in an executor thread."""
    from core.email import send_email  # type: ignore[import]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_email, cfg, to_email, subject, body_html)


# ── POST /requests/phone ──────────────────────────────────────────────────────

@router.post("/phone", status_code=status.HTTP_201_CREATED)
async def create_phone_request(
    request: Request,
    body: PhoneRequestBody,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a PhoneRequest and send an email to the contact."""

    # Load contact (must belong to current user)
    contact_result = await db.execute(
        select(Contact).where(
            Contact.id == body.contact_id,
            Contact.user_id == current_user.id,
        )
    )
    contact = contact_result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")

    if not contact.email:
        raise HTTPException(
            status_code=400,
            detail="Dieser Kontakt hat keine E-Mail-Adresse",
        )

    # Load org name
    org_result = await db.execute(
        select(Organization).where(Organization.id == current_user.org_id)
    )
    org = org_result.scalar_one_or_none()
    org_name = org.name if org else "Unbekannte Organisation"

    # Create token + record
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    req = PhoneRequest(
        id=str(uuid.uuid4()),
        sender_id=current_user.id,
        contact_id=contact.id,
        token=token,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=14),
    )
    db.add(req)
    await db.commit()

    # Send email to contact
    sender_first = current_user.first_name or ""
    sender_last = current_user.last_name or current_user.email
    sender_name = f"{sender_first} {sender_last}".strip()
    contact_name = f"{contact.first_name} {contact.last_name}".strip() or contact.email
    link = f"{_base_url(request)}/r/phone/{token}"

    body_html = f"""
<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:600px;margin:0 auto;padding:2rem;">
  <h2 style="color:#1a56db;">Anfrage zur Mobilnummer</h2>
  <p>Hallo {contact_name},</p>
  <p>
    <strong>{sender_name}</strong> von <strong>{org_name}</strong> möchte Ihnen sicher Daten übermitteln.
    Für die sichere Passwort-Übermittlung benötigen wir Ihre Mobilnummer.
  </p>
  <p style="margin:1.5rem 0;">
    <a href="{link}" style="background:#1a56db;color:#fff;padding:0.75rem 1.5rem;border-radius:0.5rem;text-decoration:none;font-weight:700;">
      Mobilnummer jetzt angeben
    </a>
  </p>
  <p style="font-size:0.875rem;color:#64748b;">
    Oder kopieren Sie diesen Link in Ihren Browser:<br/>
    <a href="{link}" style="color:#1a56db;">{link}</a>
  </p>
  <p style="font-size:0.8125rem;color:#94a3b8;margin-top:2rem;border-top:1px solid #e2e8f0;padding-top:1rem;">
    Dieser Link ist 14 Tage gültig. SecureSend Cloud – Sicheres Senden
  </p>
</body></html>
"""

    smtp_cfg = await _get_smtp_cfg(current_user, db)
    if smtp_cfg:
        try:
            await _send_email_async(
                smtp_cfg,
                contact.email,
                f"{sender_name} von {org_name} bittet um Ihre Mobilnummer",
                body_html,
            )
        except Exception as exc:
            log.warning("E-Mail-Versand fehlgeschlagen (PhoneRequest %s): %s", req.id, exc)
    else:
        log.warning("Kein SMTP konfiguriert – PhoneRequest %s E-Mail nicht gesendet", req.id)

    return {"id": req.id, "token": token, "status": "pending"}


# ── POST /requests/upload ─────────────────────────────────────────────────────

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def create_upload_request(
    request: Request,
    body: UploadRequestBody,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create an UploadRequest and send an email to the recipient."""

    # Load org name
    org_result = await db.execute(
        select(Organization).where(Organization.id == current_user.org_id)
    )
    org = org_result.scalar_one_or_none()
    org_name = org.name if org else "Unbekannte Organisation"

    days = max(1, min(body.days, 90))
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    req = UploadRequest(
        id=str(uuid.uuid4()),
        sender_id=current_user.id,
        recipient_email=body.recipient_email,
        recipient_name=body.recipient_name,
        message=body.message,
        token=token,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(req)
    await db.commit()

    sender_first = current_user.first_name or ""
    sender_last = current_user.last_name or current_user.email
    sender_name = f"{sender_first} {sender_last}".strip()
    link = f"{_base_url(request)}/r/upload/{token}"

    message_block = ""
    if body.message:
        message_block = f"""
  <div style="background:#f8fafc;border-left:4px solid #1a56db;padding:1rem 1.25rem;margin:1rem 0;border-radius:0 0.5rem 0.5rem 0;">
    <p style="margin:0;font-size:0.9375rem;">{body.message}</p>
  </div>"""

    body_html = f"""
<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:600px;margin:0 auto;padding:2rem;">
  <h2 style="color:#1a56db;">Anfrage zur sicheren Dateiübermittlung</h2>
  <p>Hallo{(' ' + body.recipient_name) if body.recipient_name else ''},</p>
  <p>
    <strong>{sender_name}</strong> von <strong>{org_name}</strong> bittet Sie,
    Dateien sicher zu übermitteln.
  </p>
  {message_block}
  <p style="margin:1.5rem 0;">
    <a href="{link}" style="background:#1a56db;color:#fff;padding:0.75rem 1.5rem;border-radius:0.5rem;text-decoration:none;font-weight:700;">
      Dateien jetzt hochladen
    </a>
  </p>
  <p style="font-size:0.875rem;color:#64748b;">
    Oder kopieren Sie diesen Link in Ihren Browser:<br/>
    <a href="{link}" style="color:#1a56db;">{link}</a>
  </p>
  <p style="font-size:0.8125rem;color:#94a3b8;margin-top:2rem;border-top:1px solid #e2e8f0;padding-top:1rem;">
    Dieser Link ist {days} Tage gültig. SecureSend Cloud – Sicheres Senden
  </p>
</body></html>
"""

    smtp_cfg = await _get_smtp_cfg(current_user, db)
    if smtp_cfg:
        try:
            await _send_email_async(
                smtp_cfg,
                body.recipient_email,
                f"{sender_name} von {org_name} bittet um Dateiübermittlung",
                body_html,
            )
        except Exception as exc:
            log.warning("E-Mail-Versand fehlgeschlagen (UploadRequest %s): %s", req.id, exc)
    else:
        log.warning("Kein SMTP konfiguriert – UploadRequest %s E-Mail nicht gesendet", req.id)

    return {"id": req.id, "token": token, "status": "pending"}
