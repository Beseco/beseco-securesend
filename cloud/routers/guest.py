"""
cloud/routers/guest.py — Gast-Portal (Empfänger-Portal)

GET  /r/{token}           — Landing (passwortgeschützt)
POST /r/{token}/verify   — Passwort/SMS-Code verifizieren
GET  /r/{token}/download/{file} — Datei-Download

GET  /r/register/{token}  — Registrierung Schritt 1
POST /r/register/{token}  — Schritt 1: E-Mail + Passwort
GET/POST /r/register/{token}/2fa — Schritt 2: 2FA (E-Mail / SMS / App)

GET  /r/dashboard/{token} — Dateien + Nachricht
POST /r/dashboard/{token} — Datei-Upload

GET  /r/reset/{token}     — Passwort-Reset
POST /r/reset/{token}     — Reset per E-Mail oder SMS anstoßen
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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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
from services.audit import log_audit_event, merge_actor_fields
from services.guest_password import validate_guest_password
from services.security_levels import (
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    LEVEL_4,
    is_e2e_level,
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
        gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
        _g = gr.scalar_one_or_none()
        if _g and _g.twofa_pending:
            return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)
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
            **merge_actor_fields(None, org_id=user.org_id),
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

    level = normalize_security_level(h.security_level)
    if requires_guest_account(level) and not h.guest_id:
        return RedirectResponse(url=f"/r/register/{token}", status_code=302)

    if h.guest_id:
        gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
        _gp = gr.scalar_one_or_none()
        if _gp and _gp.twofa_pending:
            return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)

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
            "is_e2e": is_e2e_level(level),
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
        gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
        ex = gr.scalar_one_or_none()
        if ex and ex.twofa_pending:
            return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)
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
            "error": "",
            "message": "",
        },
    )


def _guest_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _render_register_2fa(
    request: Request,
    db: AsyncSession,
    token: str,
    *,
    error: str = "",
    message: str = "",
    totp_uri: str = "",
    app_started: bool = False,
) -> HTMLResponse:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)
    gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = gr.scalar_one_or_none()
    if not guest or not guest.twofa_pending:
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)
    user = (
        await db.execute(select(User).where(User.id == h.user_id))
    ).scalar_one_or_none()
    org = None
    if user:
        org = (
            await db.execute(select(Organization).where(Organization.id == user.org_id))
        ).scalar_one_or_none()
    level = normalize_security_level(h.security_level)
    require_phone = requires_guest_account(level)
    return templates.TemplateResponse(
        "receive-register-2fa.html",
        {
            "request": request,
            "token": token,
            "org_name": org.name if org else "",
            "email": guest.email,
            "default_phone": (guest.phone or h.to_phone or "").strip(),
            "security_level": level,
            "require_phone": require_phone,
            "error": error,
            "message": message,
            "totp_uri": totp_uri,
            "app_started": app_started,
        },
    )


@router.get("/r/register/{token}/2fa", response_class=HTMLResponse)
async def guest_register_2fa_page(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Schritt 2: 2FA-Methode wählen und abschließen."""
    return await _render_register_2fa(request, db, token)


@router.post("/r/register/{token}")
async def guest_register_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Schritt 1: E-Mail + Passwort + Bestätigung."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    if h.guest_id:
        gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
        exg = gr.scalar_one_or_none()
        if exg and exg.twofa_pending:
            return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    level = normalize_security_level(h.security_level)
    require_phone = requires_guest_account(level)
    email_n = (email or "").strip().lower()

    pw_ok, pw_err = validate_guest_password(password)
    if not pw_ok:
        return templates.TemplateResponse(
            "receive-register.html",
            {
                "request": request,
                "token": token,
                "email": email,
                "security_level": level,
                "require_phone": require_phone,
                "error": pw_err,
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
                "email": email_n,
                "security_level": level,
                "require_phone": require_phone,
                "error": "Passwörter stimmen nicht überein.",
                "message": "",
            },
            status_code=400,
        )

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
                    "security_level": level,
                    "require_phone": require_phone,
                    "error": "E-Mail bereits registriert — falsches Passwort.",
                    "message": "",
                },
                status_code=400,
            )
        if require_phone and not existing.phone_verified_at:
            existing.twofa_pending = True
        h.guest_id = existing.id
        h.password_changed_at = _guest_now_naive()
        await db.commit()
        if existing.twofa_pending:
            return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    phone0 = (h.to_phone or "").strip()
    guest = Guest(
        email=email_n,
        phone=phone0,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        history_id=h.id,
        twofa_pending=True,
    )
    db.add(guest)
    await db.flush()
    h.guest_id = guest.id
    h.password_changed_at = _guest_now_naive()
    await db.commit()
    return RedirectResponse(url=f"/r/register/{token}/2fa", status_code=302)


@router.post("/r/register/{token}/2fa")
async def guest_register_2fa_submit(
    token: str,
    request: Request,
    action: str = Form(...),
    email_code: str = Form(""),
    sms_code_in: str = Form(""),
    phone: str = Form(""),
    totp_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Schritt 2: E-Mail-, SMS- oder App-2FA."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)
    gr = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = gr.scalar_one_or_none()
    if not guest or not guest.twofa_pending:
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    user = (
        await db.execute(select(User).where(User.id == h.user_id))
    ).scalar_one_or_none()
    level = normalize_security_level(h.security_level)
    require_phone = requires_guest_account(level)
    now_n = _guest_now_naive()

    def _phone_blocked_for_non_sms() -> bool:
        return bool(require_phone and not guest.phone_verified_at)

    if action == "email_send":
        if _phone_blocked_for_non_sms():
            return await _render_register_2fa(
                request,
                db,
                token,
                error="Bitte bestätigen Sie zuerst Ihre Mobilnummer per SMS (Pflicht für diese Nachricht).",
            )
        code = "".join(secrets.choice(string.digits) for _ in range(6))
        guest.email_code = code
        guest.email_code_expires = now_n + timedelta(minutes=10)
        await db.commit()
        _send_email_simple(
            guest.email,
            "SecureSend – Bestätigungscode",
            f"Ihr Code: {code}\n\nGültig 10 Minuten.",
        )
        return await _render_register_2fa(
            request, db, token, message="Code wurde an Ihre E-Mail gesendet."
        )

    if action == "email_verify":
        if (
            not guest.email_code
            or (guest.email_code or "").strip() != (email_code or "").strip()
        ):
            return await _render_register_2fa(
                request, db, token, error="Ungültiger E-Mail-Code."
            )
        if guest.email_code_expires and guest.email_code_expires < now_n:
            return await _render_register_2fa(
                request, db, token, error="Code abgelaufen. Bitte neu anfordern."
            )
        guest.email_code = None
        guest.email_code_expires = None
        guest.totp_enabled = True
        guest.twofa_pending = False
        await db.commit()
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    if action == "sms_send":
        ph = (phone or guest.phone or h.to_phone or "").strip()
        if not ph:
            return await _render_register_2fa(
                request, db, token, error="Bitte Mobilnummer angeben."
            )
        guest.phone = ph
        guest.sms_code = "".join(secrets.choice(string.digits) for _ in range(4))
        guest.sms_code_expires_at = now_n + timedelta(minutes=10)
        sms_ok = await _send_guest_sms_code(
            db, user.org_id if user else None, ph, guest.sms_code
        )
        await log_audit_event(
            event_type="guest_sms_activation_sent",
            severity="info" if sms_ok else "warning",
            status="success" if sms_ok else "failure",
            target_type="guest",
            target_id=guest.id,
            error_code=None if sms_ok else "sms_send_failed",
            **merge_actor_fields(None, org_id=user.org_id if user else None),
            db=db,
            commit=True,
        )
        return await _render_register_2fa(
            request,
            db,
            token,
            message="SMS-Code gesendet." if sms_ok else "",
            error="" if sms_ok else "SMS konnte nicht gesendet werden.",
        )

    if action == "sms_verify":
        if (
            not guest.sms_code
            or guest.sms_code != (sms_code_in or "").strip()
            or (
                guest.sms_code_expires_at
                and guest.sms_code_expires_at < now_n
            )
        ):
            return await _render_register_2fa(
                request, db, token, error="Ungültiger oder abgelaufener SMS-Code."
            )
        guest.phone_verified_at = now_n
        guest.sms_code = None
        guest.sms_code_expires_at = None
        guest.totp_enabled = True
        guest.twofa_pending = False
        await log_audit_event(
            event_type="guest_sms_activation_verified",
            severity="info",
            status="success",
            target_type="guest",
            target_id=guest.id,
            **merge_actor_fields(None, org_id=user.org_id if user else None),
            db=db,
            commit=True,
        )
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    if action == "app_prepare":
        if _phone_blocked_for_non_sms():
            return await _render_register_2fa(
                request,
                db,
                token,
                error="Bitte bestätigen Sie zuerst Ihre Mobilnummer per SMS (Pflicht für diese Nachricht).",
            )
        totp_secret = pyotp.random_base32()
        guest.totp_secret = totp_secret
        await db.commit()
        totp_uri = pyotp.TOTP(totp_secret).provisioning_uri(
            name=guest.email, issuer_name="SecureSend"
        )
        return await _render_register_2fa(
            request, db, token, totp_uri=totp_uri, app_started=True
        )

    if action == "app_verify":
        if not guest.totp_secret or not (totp_code or "").strip():
            return await _render_register_2fa(
                request, db, token, error="Bitte zuerst Authenticator einrichten und Code eingeben."
            )
        totp = pyotp.TOTP(guest.totp_secret)
        if not totp.verify((totp_code or "").strip(), valid_window=1):
            uri = pyotp.TOTP(guest.totp_secret).provisioning_uri(
                name=guest.email, issuer_name="SecureSend"
            )
            return await _render_register_2fa(
                request,
                db,
                token,
                error="Ungültiger Authenticator-Code.",
                totp_uri=uri,
                app_started=True,
            )
        guest.totp_enabled = True
        guest.twofa_pending = False
        await db.commit()
        return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)

    return await _render_register_2fa(request, db, token, error="Unbekannte Aktion.")


# ── Passwort vergessen ─────────────────────────────────────────────────


@router.get("/r/reset/{token}", response_class=HTMLResponse)
async def guest_reset(
    token: str,
    request: Request,
    code: Optional[str] = Query(default=None),
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
            "prefilled_code": (code or "").strip(),
            "message": "",
            "error": "",
        },
    )


@router.post("/r/reset/{token}")
async def guest_reset_send(
    token: str,
    request: Request,
    method: str = Form("email"),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Reset-Link (E-Mail) oder PIN (SMS) senden."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return HTMLResponse("Gast nicht gefunden", status_code=404)

    user = (
        await db.execute(select(User).where(User.id == h.user_id))
    ).scalar_one_or_none()
    now_n = _guest_now_naive()
    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")

    if method == "email":
        guest.sms_code = None
        guest.sms_code_expires_at = None
        reset_token = secrets.token_urlsafe(32)
        guest.email_code = reset_token
        guest.email_code_expires = now_n + timedelta(hours=24)
        reset_url = f"{base}/r/reset/{token}?code={reset_token}"
        _send_email_simple(
            guest.email,
            "Passwort zurücksetzen",
            f"Öffnen Sie den Link zum Zurücksetzen:\n{reset_url}",
        )
        await db.commit()
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "prefilled_code": "",
                "message": "Wir haben Ihnen einen Link an Ihre E-Mail gesendet.",
                "error": "",
            },
        )

    if method == "sms":
        guest.email_code = None
        guest.email_code_expires = None
        ph = (guest.phone or "").strip()
        if not ph:
            return templates.TemplateResponse(
                "receive-reset.html",
                {
                    "request": request,
                    "token": token,
                    "prefilled_code": "",
                    "message": "",
                    "error": "Keine Mobilnummer im Konto hinterlegt. Nutzen Sie die E-Mail-Option oder kontaktieren Sie den Support.",
                },
                status_code=400,
            )
        pin = "".join(secrets.choice(string.digits) for _ in range(6))
        guest.sms_code = pin
        guest.sms_code_expires_at = now_n + timedelta(minutes=15)
        sms_ok = await _send_guest_sms_code(
            db, user.org_id if user else None, ph, pin
        )
        await db.commit()
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "prefilled_code": "",
                "message": "SMS-PIN gesendet." if sms_ok else "",
                "error": "" if sms_ok else "SMS konnte nicht gesendet werden.",
            },
            status_code=400 if not sms_ok else 200,
        )

    return templates.TemplateResponse(
        "receive-reset.html",
        {
            "request": request,
            "token": token,
            "prefilled_code": "",
            "message": "",
            "error": "Unbekannte Methode.",
        },
        status_code=400,
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
    """Neues Passwort setzen (nach gültigem Code / Link-Token)."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h or not h.guest_id:
        return HTMLResponse("Link nicht gefunden", status_code=404)

    result = await db.execute(select(Guest).where(Guest.id == h.guest_id))
    guest = result.scalar_one_or_none()

    if not guest:
        return HTMLResponse("Gast nicht gefunden", status_code=404)

    now_n = _guest_now_naive()
    code_n = (code or "").strip()
    ok_code = False
    ec = (guest.email_code or "").strip()
    if ec and len(ec) > 12 and code_n == ec:
        if guest.email_code_expires and guest.email_code_expires < now_n:
            return templates.TemplateResponse(
                "receive-reset.html",
                {
                    "request": request,
                    "token": token,
                    "prefilled_code": code_n,
                    "error": "Link oder Code abgelaufen.",
                    "message": "",
                },
                status_code=400,
            )
        ok_code = True
    elif guest.sms_code and code_n == (guest.sms_code or "").strip():
        if guest.sms_code_expires_at and guest.sms_code_expires_at < now_n:
            return templates.TemplateResponse(
                "receive-reset.html",
                {
                    "request": request,
                    "token": token,
                    "prefilled_code": code_n,
                    "error": "SMS-Code abgelaufen.",
                    "message": "",
                },
                status_code=400,
            )
        ok_code = True

    if not ok_code or not code_n:
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "prefilled_code": code_n,
                "error": "Ungültiger Code oder Link.",
                "message": "",
            },
            status_code=400,
        )

    pw_ok, pw_err = validate_guest_password(password)
    if not pw_ok:
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "prefilled_code": code_n,
                "error": pw_err,
                "message": "",
            },
            status_code=400,
        )

    if password != password2:
        return templates.TemplateResponse(
            "receive-reset.html",
            {
                "request": request,
                "token": token,
                "prefilled_code": code_n,
                "error": "Passwörter stimmen nicht überein.",
                "message": "",
            },
            status_code=400,
        )

    guest.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    guest.email_code = None
    guest.email_code_expires = None
    guest.sms_code = None
    guest.sms_code_expires_at = None
    h.password_changed_at = now_n
    await db.commit()

    return RedirectResponse(url=f"/r/dashboard/{token}", status_code=302)


def _send_email_simple(to: str, subject: str, body: str):
    """Send simple email (placeholder - use core.email in production)."""
    log.info(f"Would send email to {to}: {subject}")
