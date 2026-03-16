"""
cloud/routers/send.py — Secure send endpoint.

POST /send  — multipart file upload  (file field required)
POST /send  — JSON body               (message field required, no file)

Both paths:
  1. Build file content + filename
  2. Look up org's default cloud provider
  3. Upload + create share link via core.storage.upload_and_share
  4. Optionally send email  via core.email.send_email
  5. Optionally send SMS    via core.sms.send_sms_sipgate
  6. Write history entry
  7. Return share_url
"""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user, org_user_required
from models.shared import CloudProvider, History, SmsGateway
from models.user import User
from schemas.shared import SendResponse

router = APIRouter(prefix="/send", tags=["send"])

_ALPHABET = string.ascii_letters + string.digits


def _random_password(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def _get_provider(
    db: AsyncSession, org_id: str, provider_id: Optional[str]
) -> CloudProvider:
    """Load the requested or default cloud provider for the org."""
    if provider_id:
        result = await db.execute(
            select(CloudProvider).where(
                CloudProvider.id == provider_id,
                CloudProvider.org_id == org_id,
                CloudProvider.is_active == True,  # noqa: E712
            )
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(status_code=404, detail="Cloud provider not found")
        return provider

    # Fall back to org default
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.org_id == org_id,
            CloudProvider.is_default == True,  # noqa: E712
            CloudProvider.is_active == True,   # noqa: E712
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="No default cloud provider configured for this organisation",
        )
    return provider


async def _get_sms_gateway(db: AsyncSession, org_id: str) -> Optional[SmsGateway]:
    result = await db.execute(
        select(SmsGateway).where(
            SmsGateway.org_id == org_id,
            SmsGateway.is_default == True,  # noqa: E712
            SmsGateway.is_active == True,    # noqa: E712
        )
    )
    return result.scalar_one_or_none()


@router.post("", response_model=SendResponse)
async def send_secure(
    request: Request,
    # ---- File upload (optional) ----
    file: Optional[UploadFile] = File(default=None),
    # ---- Form / JSON fields ----
    to_email: str = Form(default=""),
    to_phone: str = Form(default=""),
    subject: str = Form(default="Ihr sicheres Dokument"),
    message: str = Form(default=""),
    expiry_days: int = Form(default=7),
    security_level: str = Form(default="standard"),
    send_sms: bool = Form(default=False),
    send_email_flag: bool = Form(default=True, alias="send_email"),
    provider_id: Optional[str] = Form(default=None),
    # ---- Auth ----
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> SendResponse:
    """
    Upload a file OR a Markdown message to the org's cloud storage,
    create a password-protected share link, and optionally notify via
    email and/or SMS.
    """
    # Superadmins have no org_id; block them here
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organisation users can send files",
        )

    org_id = current_user.org_id

    # ── Build content ──────────────────────────────────────────────────────
    if file and file.filename:
        content = await file.read()
        filename = file.filename
        content_type = file.content_type or "application/octet-stream"
    elif message:
        content = message.encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"nachricht_{ts}.md"
        content_type = "text/markdown; charset=utf-8"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either a file upload or a message body is required",
        )

    # ── Load cloud provider ────────────────────────────────────────────────
    provider = await _get_provider(db, org_id, provider_id)
    cfg: dict = dict(provider.config_json or {})
    cfg["service"] = provider.service

    # ── Upload + share link ────────────────────────────────────────────────
    password = _random_password()
    subfolder = current_user.id  # one sub-folder per user

    try:
        # core.storage is imported here to ensure sys.path is already set up by main.py
        from core.storage import upload_and_share  # type: ignore[import]

        share_url = upload_and_share(
            cfg=cfg,
            filename=filename,
            content=content,
            password=password,
            days=expiry_days,
            content_type=content_type,
            subfolder=subfolder,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cloud storage error: {exc}",
        ) from exc

    # ── Resolve org SMTP config ────────────────────────────────────────────
    org_settings: dict = {}
    if current_user.organization and current_user.organization.settings_json:
        org_settings = current_user.organization.settings_json

    smtp_cfg: Optional[dict] = org_settings.get("smtp")

    # ── Send email ─────────────────────────────────────────────────────────
    if send_email_flag and to_email and smtp_cfg:
        try:
            from core.email import send_email  # type: ignore[import]

            signature = org_settings.get("signature", "")
            body_html = (
                f"<p>{subject}</p>"
                f"<p>Download-Link: <a href='{share_url}'>{share_url}</a></p>"
                f"<p>Passwort: <strong>{password}</strong></p>"
                f"<p>Gültig für {expiry_days} Tag(e).</p>"
                f"{'<hr/>' + signature if signature else ''}"
            )
            send_email(smtp_cfg, to_email, subject, body_html)
        except Exception as exc:
            # Non-fatal: log but don't abort
            import logging
            logging.getLogger("send").warning("Email send failed: %s", exc)

    # ── Send SMS ───────────────────────────────────────────────────────────
    if send_sms and to_phone:
        gateway = await _get_sms_gateway(db, org_id)
        if gateway and gateway.config_json:
            try:
                from core.sms import send_sms_sipgate  # type: ignore[import]

                sms_text = f"{subject}\nLink: {share_url}\nPW: {password}"
                send_sms_sipgate(gateway.config_json, to_phone, sms_text)
            except Exception as exc:
                import logging
                logging.getLogger("send").warning("SMS send failed: %s", exc)

    # ── Write history ──────────────────────────────────────────────────────
    client_ip = request.client.host if request.client else ""
    history_id = str(uuid.uuid4())
    history_entry = History(
        id=history_id,
        user_id=current_user.id,
        to_email=to_email,
        to_phone=to_phone,
        filename=filename,
        share_url=share_url,
        provider=provider.service,
        expiry_days=expiry_days,
        security_level=security_level,
        ip_address=client_ip,
    )
    db.add(history_entry)
    await db.commit()

    return SendResponse(
        share_url=share_url,
        filename=filename,
        provider=provider.service,
        expiry_days=expiry_days,
        history_id=history_id,
    )
