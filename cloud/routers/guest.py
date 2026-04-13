"""
cloud/routers/guest.py — Gast-Portal (Empfänger-Portal)

GET  /r/{token}           — Landing (passwortgeschützt)
POST /r/{token}/verify   — Passwort/SMS-Code verifizieren
GET  /r/{token}/download/{file} — Datei-Download

GET  /r/register/{token}  — Registrierungsformular
POST /r/register/{token}  — Konto erstellen + 2FA

GET  /r/dashboard/{token} — Dateien + Nachricht
POST /r/dashboard/{token} — Datei-Upload

GET  /r/reset/{token}     — Passwort-Reset
POST /r/reset/{token}     — E-Mail Link senden
POST /r/reset/{token}/sms — SMS PIN senden
POST /r/reset/{token}/confirm — Neues Passwort setzen
"""

from __future__ import annotations

import base64
import io
import logging
import os
import smtplib
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi.templating import Jinja2Templates
from pathlib import Path
from core.hosted_storage import HOSTED_SERVICE_NAME
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import pyotp
import bcrypt

from config import settings
from database import get_db
from models.organization import Organization
from models.reseller import Reseller
from models.shared import CloudProvider, Contact, History, Guest, SmsGateway, UploadRequest
from models.user import User
from services.audit import actor_fields, log_audit_event
from services.security_levels import (
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    LEVEL_4,
    normalize_security_level,
    requires_guest_account,
)

log = logging.getLogger("securesend")
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(tags=["guest"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".bat",
        ".cmd",
        ".com",
        ".msi",
        ".ps1",
        ".vbs",
        ".vbe",
        ".sh",
        ".bash",
        ".zsh",
        ".jar",
        ".scr",
        ".pif",
        ".reg",
        ".dll",
        ".hta",
        ".lnk",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".msp",
        ".gadget",
        ".cpl",
        ".inf",
        ".ins",
        ".isp",
        ".msc",
        ".mst",
        ".application",
    }
)


def _get_allowed_extensions() -> frozenset[str]:
    return frozenset(
        {
            # Bilder
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".bmp",
            ".svg",
            # Dokumente
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".txt",
            ".rtf",
            ".odt",
            ".ods",
            ".odp",
            # Archive
            ".zip",
            ".7z",
            ".rar",
            ".tar",
            ".gz",
        }
    )


async def _get_org(tracking_token: str, db: AsyncSession) -> Optional[Organization]:
    result = await db.execute(
        select(History).where(History.tracking_token == tracking_token)
    )
    history = result.scalar_one_or_none()
    if not history:
        return None
    result = await db.execute(select(User).where(User.id == history.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return None
    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    return result.scalar_one_or_none()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _get_sms_gateway(db: AsyncSession, org_id: Optional[str]) -> Optional[SmsGateway]:
    if not org_id:
        return None
    result = await db.execute(
        select(SmsGateway).where(
            SmsGateway.org_id == org_id,
            SmsGateway.is_default == True,  # noqa: E712
            SmsGateway.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def _send_guest_sms_code(
    db: AsyncSession, org_id: Optional[str], phone: str, code: str
) -> bool:
    gateway = await _get_sms_gateway(db, org_id)
    if not gateway or not gateway.config_json:
        return False
    try:
        from core.sms import send_sms_sipgate  # type: ignore

        send_sms_sipgate(gateway.config_json, phone, f"Ihr SecureSend Code: {code}")
        return True
    except Exception:
        return False


@router.get("/r/{token}", response_class=HTMLResponse)
async def guest_landing(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Landing-Page mit Passwort-Eingabe."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("<h1>Link nicht gefunden</h1>", status_code=404)

    if h.is_revoked:
        return HTMLResponse("<h1>Dieser Link wurde zurückgerufen</h1>", status_code=410)

    level = normalize_security_level(h.security_level)
    if requires_guest_account(level) and not h.guest_id:
        return RedirectResponse(url=f"/r/register/{token}", status_code=302)

    # Hole Organisation und Reseller
    result = await db.execute(select(User).where(User.id == h.user_id))
    user = result.scalar_one_or_none()

    if not user:
        return HTMLResponse("<h1>Benutzer nicht gefunden</h1>", status_code=404)

    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalar_one_or_none()

    rname = ""
    if org:
        result = await db.execute(
            select(Reseller).where(Reseller.id == org.reseller_id)
        )
        reseller = result.scalar_one_or_none()
        if reseller:
            rname = reseller.name

    # Prüfe ob bereits als Guest registriert
    if h.guest_id:
        # Check Guest password/session
        # Redirect to dashboard
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    # Security level determines auth method
    level = normalize_security_level(h.security_level)
    if requires_guest_account(level):
        return RedirectResponse(url=f"/r/register/{token}", status_code=302)

    # Normale Stufe: kein Passwort
    if level == LEVEL_1:
        # Direkt weiterleiten
        # Track access
        h.access_count = (h.access_count or 0) + 1
        h.link_clicked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        await log_audit_event(
            event_type="guest_link_opened",
            severity="info",
            status="success",
            target_type="history",
            target_id=h.id,
            org_id=user.org_id,
            **actor_fields(None),
            db=db,
            commit=True,
        )
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    if requires_guest_account(level):
        return RedirectResponse(url=f"/r/register/{token}", status_code=302)

    # Andere Stufen: Passwort erforderlich
    return templates.TemplateResponse(
        "guest_message_gate.html",
        {
            "request": request,
            "token": token,
            "level": level,
            "org": org,
            "org_name": org.name if org else "",
            "reseller_name": rname,
            "subject": h.subject or h.filename or "Sichere Nachricht",
            "security_level": level,
            "error": "",
        },
    )


@router.post("/r/{token}/verify")
async def guest_verify(
    token: str,
    request: Request,
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Passwort oder SMS-Code verifizieren."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    if h.is_revoked:
        return HTMLResponse("Dieser Link wurde zurückgerufen", status_code=410)

    level = normalize_security_level(h.security_level)

    # Erfolgreich - Track access
    h.access_count = (h.access_count or 0) + 1
    h.link_clicked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    # Redirect to dashboard
    return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)


@router.get("/r/dashboard/{token}", response_class=HTMLResponse)
async def guest_dashboard(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Gast-Dashboard mit Dateien und Nachricht."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("<h1>Link nicht gefunden</h1>", status_code=404)

    if h.is_revoked:
        return HTMLResponse("<h1>Dieser Link wurde zurückgerufen</h1>", status_code=410)

    # Hole User und Org
    result = await db.execute(select(User).where(User.id == h.user_id))
    user = result.scalar_one_or_none()

    if not user:
        return HTMLResponse("<h1>Benutzer nicht gefunden</h1>", status_code=404)

    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalar_one_or_none()

    rname = ""
    if org:
        result = await db.execute(
            select(Reseller).where(Reseller.id == org.reseller_id)
        )
        reseller = result.scalar_one_or_none()
        if reseller:
            rname = reseller.name

    # Hole Guest falls vorhanden
    guest = None
    if h.guest_id:
        result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
        guest = result.scalar_one_or_none()
        if guest and not h.read_at:
            h.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()

    # Dateien vorbereiten
    files = []
    if h.files_json:
        files = h.files_json
    elif h.filename:
        files = [{"name": h.filename, "size": 0, "type": "file"}]

    # Formatierte Größe
    for f in files:
        size = f.get("size", 0)
        if size < 1024:
            f["size_str"] = f"{size} B"
        elif size < 1024 * 1024:
            f["size_str"] = f"{size / 1024:.1f} KB"
        else:
            f["size_str"] = f"{size / (1024 * 1024):.1f} MB"

    # Absender Name
    sender_name = f"{user.first_name} {user.last_name}" if user else "Unbekannt"

    return templates.TemplateResponse(
        "receive-dashboard.html",
        {
            "request": request,
            "token": token,
            "org": org,
            "org_name": org.name if org else "",
            "reseller_name": rname,
            "subject": (h.subject or h.filename or "Sichere Nachricht"),
            "message": h.message_preview or "",
            "sender_name": sender_name,
            "sender_email": user.email if user else "",
            "files": files,
            "created_at": h.created_at.isoformat() if h.created_at else "",
            "download_count": h.download_count or 0,
            "guest": guest,
            "security_level": level,
        },
    )


@router.get("/r/dashboard/{token}/download/{filename}")
async def guest_download(
    token: str,
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Datei-Download mit Tracking."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")

    if h.is_revoked:
        raise HTTPException(status_code=410, detail="Link zurückgerufen")

    # Update download count
    h.download_count = (h.download_count or 0) + 1
    h.last_downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    # Hole Cloud-Provider
    result = await db.execute(select(User).where(User.id == h.user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    if h.provider == HOSTED_SERVICE_NAME:
        link = h.share_url
        if not link:
            base = str(request.base_url).rstrip("/")
            link = f"{base}/track/l/{h.tracking_token}"
        return RedirectResponse(url=link, status_code=302)

    pr = await db.execute(
        select(CloudProvider).where(
            and_(
                CloudProvider.org_id == user.org_id,
                CloudProvider.is_active == True,
                CloudProvider.service == h.provider,
            )
        )
    )
    cands = list(pr.scalars().all())
    if not cands:
        raise HTTPException(status_code=404, detail="Kein Cloud-Provider konfiguriert")
    provider = next((p for p in cands if p.is_default), cands[0])

    if not provider.config_json and h.provider != HOSTED_SERVICE_NAME:
        raise HTTPException(status_code=404, detail="Cloud-Provider unvollständig")

    # Redirect zum Freigabe-Link (einzelne Gast-Datei: Ordner-Link)
    if h.share_url:
        return RedirectResponse(url=h.share_url, status_code=302)
    raise HTTPException(status_code=404, detail="Kein Download-Link")


# ── Registration ─────────────────────────────────────────────────────


@router.get("/r/register/{token}", response_class=HTMLResponse)
async def guest_register(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Registrierungsformular für neuen Gast."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    if h.guest_id:
        # Already registered
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    # Hole Organisation
    result = await db.execute(select(User).where(User.id == h.user_id))
    user = result.scalar_one_or_none()

    result = await db.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org = result.scalar_one_or_none()

    level = normalize_security_level(h.security_level)
    return templates.TemplateResponse(
        "receive-register.html",
        {
            "request": request,
            "token": token,
            "org": org,
            "org_name": org.name if org else "",
            "security_level": level,
            "require_phone": requires_guest_account(level),
            "email": h.to_email or "",
            "mobile": h.to_phone or "",
            "error": "",
            "message": "",
        },
    )


@router.post("/r/register/{token}")
async def guest_register_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    email: str = Form(...),
    mobile: str = Form(""),
    sms_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Konto erstellen."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    if h.guest_id:
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    level = normalize_security_level(h.security_level)
    require_phone = requires_guest_account(level)
    user_res = await db.execute(select(User).where(User.id == h.user_id))
    user = user_res.scalar_one_or_none()
    email_n = (email or "").strip().lower()
    mobile_n = (mobile or "").strip() or (h.to_phone or "").strip()

    # Validate passwords
    if len(password) < 12:
        return templates.TemplateResponse(
            "receive-register.html",
            {
                "request": request,
                "token": token,
                "email": email,
                "mobile": mobile_n,
                "security_level": level,
                "require_phone": require_phone,
                "error": "Passwort muss mindestens 12 Zeichen haben",
                "message": "",
            },
            status_code=400,
        )

    if password != password2:
        return templates.TemplateResponse(
            "receive-register.html",
            {
                "request": request,
                "token": token,
                "email": email,
                "mobile": mobile_n,
                "security_level": level,
                "require_phone": require_phone,
                "error": "Passwörter stimmen nicht überein",
                "message": "",
            },
            status_code=400,
        )

    if require_phone and not mobile_n:
        return templates.TemplateResponse(
            "receive-register.html",
            {
                "request": request,
                "token": token,
                "email": email_n,
                "mobile": "",
                "security_level": level,
                "require_phone": require_phone,
                "error": "Für diese Sicherheitsstufe ist eine Mobilnummer erforderlich.",
                "message": "",
            },
            status_code=400,
        )

    # Bestehendes Gastkonto: gleiche E-Mail + Passwort → verknüpfen (Posteingang)
    result = await db.execute(select(Guest).where(Guest.email == email_n))
    existing = result.scalar_one_or_none()

    if existing:
        if not bcrypt.checkpw(password.encode(), existing.password_hash.encode()):
            return templates.TemplateResponse(
                "receive-register.html",
                {
                    "request": request,
                    "token": token,
                    "email": email_n,
                    "mobile": mobile_n,
                    "security_level": level,
                    "require_phone": require_phone,
                    "error": "E-Mail bereits registriert — falsches Passwort",
                    "message": "",
                },
                status_code=400,
            )
        if mobile_n and not existing.phone:
            existing.phone = mobile_n

        if require_phone:
            if not existing.phone_verified_at:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if not sms_code:
                    existing.sms_code = "".join(secrets.choice(string.digits) for _ in range(4))
                    existing.sms_code_expires_at = now + timedelta(minutes=10)
                    sms_ok = await _send_guest_sms_code(db, user.org_id if user else None, existing.phone or mobile_n, existing.sms_code)
                    await log_audit_event(
                        event_type="guest_sms_activation_sent",
                        severity="info" if sms_ok else "warning",
                        status="success" if sms_ok else "failure",
                        target_type="guest",
                        target_id=existing.id,
                        org_id=user.org_id if user else None,
                        error_code=None if sms_ok else "sms_send_failed",
                        **actor_fields(None),
                        db=db,
                        commit=True,
                    )
                    return templates.TemplateResponse(
                        "receive-register.html",
                        {
                            "request": request,
                            "token": token,
                            "email": email_n,
                            "mobile": existing.phone or mobile_n,
                            "security_level": level,
                            "require_phone": require_phone,
                            "error": "" if sms_ok else "SMS-Code konnte nicht gesendet werden.",
                            "message": "SMS-Code gesendet. Bitte 4-stelligen Code eingeben.",
                        },
                        status_code=400 if not sms_ok else 200,
                    )
                if (
                    not existing.sms_code
                    or existing.sms_code != sms_code.strip()
                    or (
                        existing.sms_code_expires_at
                        and existing.sms_code_expires_at < now
                    )
                ):
                    return templates.TemplateResponse(
                        "receive-register.html",
                        {
                            "request": request,
                            "token": token,
                            "email": email_n,
                            "mobile": existing.phone or mobile_n,
                            "security_level": level,
                            "require_phone": require_phone,
                            "error": "Ungültiger oder abgelaufener SMS-Code.",
                            "message": "",
                        },
                        status_code=400,
                    )
                existing.phone_verified_at = now
                existing.sms_code = None
                existing.sms_code_expires_at = None
                await log_audit_event(
                    event_type="guest_sms_activation_verified",
                    severity="info",
                    status="success",
                    target_type="guest",
                    target_id=existing.id,
                    org_id=user.org_id if user else None,
                    **actor_fields(None),
                    db=db,
                    commit=False,
                )

        h.guest_id = existing.id
        h.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    # Neues Gastkonto
    guest = Guest(
        email=email_n,
        phone=mobile_n or "",
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        history_id=h.id,
    )
    if require_phone:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not sms_code:
            guest.sms_code = "".join(secrets.choice(string.digits) for _ in range(4))
            guest.sms_code_expires_at = now + timedelta(minutes=10)
            sms_ok = await _send_guest_sms_code(db, user.org_id if user else None, guest.phone or "", guest.sms_code)
            db.add(guest)
            await db.flush()
            await log_audit_event(
                event_type="guest_sms_activation_sent",
                severity="info" if sms_ok else "warning",
                status="success" if sms_ok else "failure",
                target_type="guest",
                target_id=guest.id,
                org_id=user.org_id if user else None,
                error_code=None if sms_ok else "sms_send_failed",
                **actor_fields(None),
                db=db,
                commit=False,
            )
            await db.commit()
            return templates.TemplateResponse(
                "receive-register.html",
                {
                    "request": request,
                    "token": token,
                    "email": email_n,
                    "mobile": guest.phone,
                    "security_level": level,
                    "require_phone": require_phone,
                    "error": "" if sms_ok else "SMS-Code konnte nicht gesendet werden.",
                    "message": "SMS-Code gesendet. Bitte 4-stelligen Code eingeben.",
                },
                status_code=400 if not sms_ok else 200,
            )
        if not sms_code.strip():
            return templates.TemplateResponse(
                "receive-register.html",
                {
                    "request": request,
                    "token": token,
                    "email": email_n,
                    "mobile": guest.phone,
                    "security_level": level,
                    "require_phone": require_phone,
                    "error": "Bitte zuerst SMS-Code anfordern.",
                    "message": "",
                },
                status_code=400,
            )

        # Falls Frontend direkt mit Code kommt, existiert noch kein Datensatz für Vergleich.
        return templates.TemplateResponse(
            "receive-register.html",
            {
                "request": request,
                "token": token,
                "email": email_n,
                "mobile": guest.phone,
                "security_level": level,
                "require_phone": require_phone,
                "error": "Bitte zuerst SMS-Code anfordern.",
                "message": "",
            },
            status_code=400,
        )

    db.add(guest)
    await db.flush()

    h.guest_id = guest.id
    h.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)


@router.post("/r/register/{token}/2fa")
async def guest_enable_2fa(
    token: str,
    request: Request,
    method: str = Form("app"),  # "app" or "email"
    code: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """2FA aktivieren."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return JSONResponse({"error": "Nicht autorisiert"}, status_code=401)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return JSONResponse({"error": "Gast nicht gefunden"}, status_code=404)

    if method == "app":
        # Generate TOTP secret
        totp_secret = pyotp.random_base32()
        guest.totp_secret = totp_secret
        await db.commit()

        # Generate QR code URL
        totp_uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(
            name=guest.email, issuer_name="SecureSend"
        )
        return JSONResponse(
            {
                "secret": totp_secret,
                "uri": totp_uri,
            }
        )

    elif method == "email":
        # Generate email code
        email_code = "".join(secrets.choice(string.digits) for _ in range(6))
        guest.email_code = email_code
        guest.email_code_expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        await db.commit()

        # Send email
        result = await db.execute(select(User).where(User.id == h.user_id))
        user = result.scalar_one_or_none()

        if user:
            _send_email_simple(
                user.email,
                f"2FA Code: {email_code}",
                f"Ihr Bestätigungscode: {email_code}",
            )

        return JSONResponse({"sent": True})

    return JSONResponse({"error": "Ungültige Methode"}, status_code=400)


@router.post("/r/register/{token}/2fa/verify")
async def guest_verify_2fa(
    token: str,
    request: Request,
    code: str = Form(""),
    secret: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """2FA Code verifizieren und aktivieren."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return JSONResponse({"error": "Nicht autorisiert"}, status_code=401)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return JSONResponse({"error": "Gast nicht gefunden"}, status_code=404)

    if secret:
        # TOTP verify
        totp = pyotp.TOTP(secret)
        if not totp.verify(code):
            return JSONResponse({"error": "Falscher Code"}, status_code=400)

        guest.totp_secret = secret
        guest.totp_enabled = True
        await db.commit()
        return JSONResponse({"enabled": True})

    elif guest.email_code:
        # Email code verify
        if guest.email_code != code:
            return JSONResponse({"error": "Falscher Code"}, status_code=400)

        if guest.email_code_expires and guest.email_code_expires < datetime.now(
            timezone.utc
        ):
            return JSONResponse({"error": "Code abgelaufen"}, status_code=400)

        guest.email_code = None
        guest.email_code_expires = None
        guest.totp_enabled = True
        await db.commit()
        return JSONResponse({"enabled": True})

    return JSONResponse({"error": "Keine 2FA konfiguriert"}, status_code=400)


# ── Passwort vergessen ─────────────────────────────────────────────────


@router.get("/r/reset/{token}", response_class=HTMLResponse)
async def guest_reset(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Passwort-Reset Formular."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    return templates.TemplateResponse(
        "receive-reset.html",
        {
            "request": request,
            "token": token,
            "error": "",
        },
    )


@router.post("/r/reset/{token}")
async def guest_reset_send(
    token: str,
    request: Request,
    method: str = Form("email"),  # "email" or "sms"
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Reset-Link oder PIN senden."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return HTMLResponse("Gast nicht gefunden", status_code=404)

    if method == "email":
        # Generate reset token
        reset_token = secrets.token_urlsafe(32)
        guest.email_code = reset_token
        guest.email_code_expires = datetime.now(timezone.utc) + timedelta(hours=24)

        # Send email
        result = await db.execute(select(User).where(User.id == h.user_id))
        user = result.scalar_one_or_none()

        if user:
            reset_url = f"{settings.PUBLIC_BASE_URL}/r/reset/{token}?code={reset_token}"
            _send_email_simple(
                guest.email, "Passwort zurücksetzen", f"Klicken Sie hier: {reset_url}"
            )

        await db.commit()

    return templates.TemplateResponse(
        "receive-reset.html",
        {
            "request": request,
            "token": token,
            "message": "Reset-Link gesendet" if method == "email" else "PIN gesendet",
        },
    )


@router.post("/r/reset/{token}/confirm")
async def guest_reset_confirm(
    token: str,
    request: Request,
    code: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Neues Passwort setzen."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return HTMLResponse("Gast nicht gefunden", status_code=404)

    # Validate
    if len(password) < 12:
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "error": "Passwort zu kurz",
            },
            status_code=400,
        )

    if password != password2:
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "error": "Passwörter stimmen nicht",
            },
            status_code=400,
        )

    # Update password
    guest.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    guest.email_code = None
    guest.email_code_expires = None
    h.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)


def _send_email_simple(to: str, subject: str, body: str):
    """Send simple email (placeholder - use core.email in production)."""
    log.info(f"Would send email to {to}: {subject}")
