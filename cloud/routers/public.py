"""
cloud/routers/public.py — Public (no-auth) endpoints for phone and upload requests.

GET  /r/phone/{token}   — show phone number input landing page
POST /r/phone/{token}   — save mobile, notify sender, redirect to done
GET  /r/upload/{token}  — show file upload landing page
POST /r/upload/{token}  — upload files to cloud, notify sender, redirect to done
GET  /r/done            — success page
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from hosted_cfg import merge_hosted_storage_cfg
from models.organization import Organization
from models.reseller import Reseller
from models.shared import CloudProvider, Contact, History, PhoneRequest, UploadRequest
from models.user import User
from services.hosted_provider import (
    ensure_hosted_cloud_provider,
    merge_org_settings_with_storage_defaults,
    resolve_storage_quota_bytes,
)

from core.hosted_storage import HOSTED_SERVICE_NAME
from core.smtp_config import get_env_smtp_cfg

log = logging.getLogger("securesend")

router = APIRouter(tags=["public"])
limiter = Limiter(key_func=get_remote_address)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe",
    ".sh", ".bash", ".zsh", ".jar", ".scr", ".pif", ".reg", ".dll",
    ".hta", ".lnk", ".js", ".jse", ".wsf", ".wsh", ".msp", ".gadget",
    ".cpl", ".inf", ".ins", ".isp", ".msc", ".mst", ".application",
})

_ALPHABET = string.ascii_letters + string.digits + "!@#$%&*"


def _random_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def _send_email_async(cfg: dict, to_email: str, subject: str, body_html: str) -> None:
    from core.email import send_email  # type: ignore[import]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_email, cfg, to_email, subject, body_html)


async def _get_smtp_cfg(sender: User, db: AsyncSession) -> Optional[dict]:
    org_result = await db.execute(
        select(Organization).where(Organization.id == sender.org_id)
    )
    org = org_result.scalar_one_or_none()
    if org and org.settings_json and org.settings_json.get("smtp"):
        return org.settings_json["smtp"]
    if org:
        res_result = await db.execute(
            select(Reseller).where(Reseller.id == org.reseller_id)
        )
        reseller = res_result.scalar_one_or_none()
        if reseller and reseller.settings_json and reseller.settings_json.get("smtp"):
            return reseller.settings_json["smtp"]
    return get_env_smtp_cfg()


# ── GET /r/done ───────────────────────────────────────────────────────────────

@router.get("/r/done", response_class=HTMLResponse, include_in_schema=False)
async def request_done(request: Request) -> HTMLResponse:
    req_type = request.query_params.get("type", "phone")
    return templates.TemplateResponse(
        "request_done.html",
        {"request": request, "type": req_type},
    )


# ── GET /r/phone/{token} ──────────────────────────────────────────────────────

@router.get("/r/phone/{token}", response_class=HTMLResponse, include_in_schema=False)
async def phone_landing(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(PhoneRequest)
        .options(
            selectinload(PhoneRequest.sender).selectinload(User.organization),
            selectinload(PhoneRequest.contact),
        )
        .where(PhoneRequest.token == token)
    )
    phone_req = result.scalar_one_or_none()

    if not phone_req:
        return templates.TemplateResponse(
            "phone_landing.html",
            {"request": request, "error": "Ungültiger oder abgelaufener Link.", "phone_req": None},
            status_code=404,
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired = phone_req.expires_at < now

    org_name = ""
    if phone_req.sender and phone_req.sender.organization:
        org_name = phone_req.sender.organization.name

    sender_name = ""
    if phone_req.sender:
        first = phone_req.sender.first_name or ""
        last = phone_req.sender.last_name or phone_req.sender.email
        sender_name = f"{first} {last}".strip()

    return templates.TemplateResponse(
        "phone_landing.html",
        {
            "request": request,
            "phone_req": phone_req,
            "sender_name": sender_name,
            "org_name": org_name,
            "expired": expired,
            "fulfilled": phone_req.status == "fulfilled",
            "error": None,
        },
    )


# ── POST /r/phone/{token} ─────────────────────────────────────────────────────

@router.post("/r/phone/{token}", include_in_schema=False)
@limiter.limit("5/minute")
async def phone_submit(
    token: str,
    request: Request,
    mobile: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    result = await db.execute(
        select(PhoneRequest)
        .options(
            selectinload(PhoneRequest.sender).selectinload(User.organization),
            selectinload(PhoneRequest.contact),
        )
        .where(PhoneRequest.token == token)
    )
    phone_req = result.scalar_one_or_none()

    if not phone_req:
        return RedirectResponse(url="/r/done?type=phone", status_code=303)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if phone_req.expires_at < now or phone_req.status != "pending":
        return RedirectResponse(url="/r/done?type=phone", status_code=303)

    # Save mobile to contact
    contact = phone_req.contact
    if contact:
        contact.mobile = mobile.strip()

    phone_req.status = "fulfilled"
    await db.commit()

    # Notify sender
    sender = phone_req.sender
    if sender:
        smtp_cfg = await _get_smtp_cfg(sender, db)
        if smtp_cfg:
            contact_name = ""
            if contact:
                contact_name = f"{contact.first_name} {contact.last_name}".strip() or contact.email

            org_name = sender.organization.name if sender.organization else ""
            body_html = f"""
<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:600px;margin:0 auto;padding:2rem;">
  <h2 style="color:#1a56db;">Mobilnummer erhalten</h2>
  <p>Ihr Kontakt <strong>{contact_name}</strong> hat seine Mobilnummer übermittelt:</p>
  <p style="font-size:1.25rem;font-weight:700;color:#1a56db;padding:1rem;background:#eff6ff;border-radius:0.5rem;display:inline-block;">
    {mobile.strip()}
  </p>
  <p style="font-size:0.8125rem;color:#94a3b8;margin-top:2rem;border-top:1px solid #e2e8f0;padding-top:1rem;">
    SecureSend Cloud – Sicheres Senden
  </p>
</body></html>
"""
            try:
                await _send_email_async(
                    smtp_cfg,
                    sender.email,
                    f"Mobilnummer von {contact_name} erhalten",
                    body_html,
                )
            except Exception as exc:
                log.warning("Benachrichtigungs-E-Mail fehlgeschlagen (PhoneRequest %s): %s", phone_req.id, exc)
        else:
            log.warning("Kein SMTP – PhoneRequest %s Benachrichtigung nicht gesendet", phone_req.id)

    return RedirectResponse(url="/r/done?type=phone", status_code=303)


# ── GET /r/upload/{token} ─────────────────────────────────────────────────────

@router.get("/r/upload/{token}", response_class=HTMLResponse, include_in_schema=False)
async def upload_landing(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(UploadRequest)
        .options(
            selectinload(UploadRequest.sender).selectinload(User.organization),
        )
        .where(UploadRequest.token == token)
    )
    upload_req = result.scalar_one_or_none()

    if not upload_req:
        return templates.TemplateResponse(
            "upload_landing.html",
            {"request": request, "error": "Ungültiger oder abgelaufener Link.", "upload_req": None},
            status_code=404,
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired = upload_req.expires_at < now

    org_name = ""
    if upload_req.sender and upload_req.sender.organization:
        org_name = upload_req.sender.organization.name

    sender_name = ""
    if upload_req.sender:
        first = upload_req.sender.first_name or ""
        last = upload_req.sender.last_name or upload_req.sender.email
        sender_name = f"{first} {last}".strip()

    return templates.TemplateResponse(
        "upload_landing.html",
        {
            "request": request,
            "upload_req": upload_req,
            "sender_name": sender_name,
            "org_name": org_name,
            "expired": expired,
            "fulfilled": upload_req.status == "fulfilled",
            "error": None,
        },
    )


# ── POST /r/upload/{token} ────────────────────────────────────────────────────

@router.post("/r/upload/{token}", include_in_schema=False)
@limiter.limit("5/minute")
async def upload_submit(
    token: str,
    request: Request,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    result = await db.execute(
        select(UploadRequest)
        .options(
            selectinload(UploadRequest.sender).selectinload(User.organization),
        )
        .where(UploadRequest.token == token)
    )
    upload_req = result.scalar_one_or_none()

    if not upload_req:
        return RedirectResponse(url="/r/done?type=upload", status_code=303)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if upload_req.expires_at < now or upload_req.status != "pending":
        return RedirectResponse(url="/r/done?type=upload", status_code=303)

    sender = upload_req.sender
    if not sender:
        return RedirectResponse(url="/r/done?type=upload", status_code=303)

    await ensure_hosted_cloud_provider(db, sender.org_id)
    await db.flush()

    # Read and validate files
    file_tuples: list[tuple[str, bytes, str]] = []
    for uf in files:
        if not uf.filename:
            continue
        from pathlib import PurePosixPath
        ext = PurePosixPath(uf.filename).suffix.lower()
        if ext in _BLOCKED_EXTENSIONS:
            log.warning("Blocked file extension %s in UploadRequest %s", ext, upload_req.id)
            continue
        content = await uf.read()
        mime = uf.content_type or "application/octet-stream"
        file_tuples.append((uf.filename, content, mime))

    if not file_tuples:
        return RedirectResponse(url="/r/done?type=upload", status_code=303)

    total_size = sum(len(d) for _, d, _ in file_tuples)

    org_row = await db.execute(
        select(Organization).where(Organization.id == sender.org_id).with_for_update()
    )
    org_o = org_row.scalar_one_or_none()
    if org_o:
        merged = merge_org_settings_with_storage_defaults(org_o.settings_json)
        used_b = int(merged.get("storage_used_bytes", 0))
        quota_b = await resolve_storage_quota_bytes(db, org_o)
        if used_b + total_size > quota_b:
            log.warning("UploadRequest %s: Speicherkontingent überschritten", upload_req.id)
            return RedirectResponse(url="/r/done?type=upload&error=quota", status_code=303)

    # Look up default cloud provider for the sender's org
    provider_result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.org_id == sender.org_id,
            CloudProvider.is_default == True,  # noqa: E712
            CloudProvider.is_active == True,  # noqa: E712
        )
    )
    provider = provider_result.scalar_one_or_none()
    if not provider:
        provider_result2 = await db.execute(
            select(CloudProvider).where(
                CloudProvider.user_id == sender.id,
                CloudProvider.is_default == True,  # noqa: E712
                CloudProvider.is_active == True,  # noqa: E712
            )
        )
        provider = provider_result2.scalar_one_or_none()

    if not provider:
        log.warning("Kein Cloud-Anbieter für UploadRequest %s (sender %s)", upload_req.id, sender.id)
        return RedirectResponse(url="/r/done?type=upload&error=no_provider", status_code=303)

    if provider.service != HOSTED_SERVICE_NAME and not provider.config_json:
        log.warning("Cloud-Anbieter unvollständig für UploadRequest %s", upload_req.id)
        return RedirectResponse(url="/r/done?type=upload&error=no_provider", status_code=303)

    # ── Upload-Größenlimit ──────────────────────────────────────────────────
    from config import settings as _cfg  # type: ignore[import]
    max_bytes = _cfg.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if total_size > max_bytes:
        log.warning("Upload zu groß in UploadRequest %s: %d Bytes", upload_req.id, total_size)
        return RedirectResponse(url="/r/done?type=upload&error=too_large", status_code=303)

    # Virenscanner
    from core.antivirus import scan_bytes  # type: ignore[import]
    for fname, data, mime in file_tuples:
        is_clean, msg = scan_bytes(data, fname)
        if not is_clean:
            log.warning("Virus in UploadRequest %s, Datei %r: %s", upload_req.id, fname, msg)
            return RedirectResponse(url="/r/done?type=upload&error=virus", status_code=303)

    # Upload to cloud storage
    folder_path = f"upload-requests/{upload_req.id}"
    password = _random_password()
    days_remaining = max(1, (upload_req.expires_at - now).days)

    cfg = dict(provider.config_json or {})
    cfg["service"] = provider.service
    if provider.service == HOSTED_SERVICE_NAME:
        merged_st = merge_org_settings_with_storage_defaults(
            org_o.settings_json if org_o else None
        )
        used_b = int(merged_st.get("storage_used_bytes", 0))
        quota_b = (
            await resolve_storage_quota_bytes(db, org_o)
            if org_o
            else int(merged_st.get("storage_quota_bytes", 5 * 1024**3))
        )
        cfg = merge_hosted_storage_cfg(
            cfg, sender.org_id, quota_used=used_b, quota_total=quota_b
        )

    try:
        from core.storage import upload_files_and_share_folder  # type: ignore[import]
        loop = asyncio.get_event_loop()
        share_url: str = await loop.run_in_executor(
            None,
            upload_files_and_share_folder,
            cfg,
            file_tuples,
            folder_path,
            password,
            days_remaining,
        )
    except Exception as exc:
        log.error("Upload fehlgeschlagen für UploadRequest %s: %s", upload_req.id, exc)
        return RedirectResponse(url="/r/done?type=upload", status_code=303)

    # Save result + mark fulfilled
    upload_req.status = "fulfilled"
    upload_req.result_url = share_url

    # Create history entry
    filenames = ", ".join(f for f, _, _ in file_tuples)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    exp_at = now_naive + timedelta(days=days_remaining)
    msg_prev = (upload_req.message or "").strip()
    if len(msg_prev) > 600:
        msg_prev = msg_prev[:597] + "..."
    files_json_meta = [
        {"name": fn, "size": len(data), "type": mime or "application/octet-stream"}
        for fn, data, mime in file_tuples
    ]
    storage_fp = folder_path if provider.service == HOSTED_SERVICE_NAME else None
    history = History(
        id=str(uuid.uuid4()),
        user_id=sender.id,
        to_email=upload_req.recipient_email,
        to_phone="",
        filename=filenames,
        subject="Empfangene Dateien (Upload-Anfrage)",
        message_preview=msg_prev or None,
        share_url=share_url,
        provider=provider.service,
        expiry_days=days_remaining,
        expires_at=exp_at,
        security_level="upload-request",
        ip_address=request.client.host if request.client else "",
        files_json=files_json_meta,
        storage_folder_path=storage_fp,
        cloud_provider_id=provider.id,
    )
    db.add(history)
    if org_o:
        merged = merge_org_settings_with_storage_defaults(org_o.settings_json)
        merged["storage_used_bytes"] = int(merged.get("storage_used_bytes", 0)) + total_size
        org_o.settings_json = merged
    await db.commit()
    await db.refresh(history)

    if provider.service == HOSTED_SERVICE_NAME:
        from config import settings as app_settings

        base = str(request.base_url).rstrip("/")
        eff = (app_settings.PUBLIC_BASE_URL or base).rstrip("/")
        history.share_url = f"{eff}/track/l/{history.tracking_token}"
        upload_req.result_url = history.share_url
        await db.commit()

    # Notify sender
    smtp_cfg = await _get_smtp_cfg(sender, db)
    if smtp_cfg:
        sender_first = sender.first_name or ""
        sender_last = sender.last_name or sender.email
        sender_name = f"{sender_first} {sender_last}".strip()
        recipient_display = upload_req.recipient_name or upload_req.recipient_email
        file_list_html = "".join(f"<li>{fn}</li>" for fn, _, _ in file_tuples)

        body_html = f"""
<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:600px;margin:0 auto;padding:2rem;">
  <h2 style="color:#1a56db;">Dateien sicher erhalten</h2>
  <p><strong>{recipient_display}</strong> hat Dateien für Sie hochgeladen:</p>
  <ul style="background:#f8fafc;padding:1rem 1.5rem;border-radius:0.5rem;">{file_list_html}</ul>
  <p style="margin:1.5rem 0;">
    <a href="{share_url}" style="background:#1a56db;color:#fff;padding:0.75rem 1.5rem;border-radius:0.5rem;text-decoration:none;font-weight:700;">
      Dateien ansehen
    </a>
  </p>
  <p style="font-size:0.875rem;color:#64748b;">
    Cloud-Link: <a href="{share_url}" style="color:#1a56db;">{share_url}</a><br/>
    Passwort: <code style="background:#f1f5f9;padding:0.125rem 0.375rem;border-radius:0.25rem;">{password}</code>
  </p>
  <p style="font-size:0.8125rem;color:#94a3b8;margin-top:2rem;border-top:1px solid #e2e8f0;padding-top:1rem;">
    SecureSend Cloud – Sicheres Senden
  </p>
</body></html>
"""
        try:
            await _send_email_async(
                smtp_cfg,
                sender.email,
                f"Dateien von {recipient_display} erhalten",
                body_html,
            )
        except Exception as exc:
            log.warning("Benachrichtigungs-E-Mail fehlgeschlagen (UploadRequest %s): %s", upload_req.id, exc)
    else:
        log.warning("Kein SMTP – UploadRequest %s Benachrichtigung nicht gesendet", upload_req.id)

    return RedirectResponse(url="/r/done?type=upload", status_code=303)
