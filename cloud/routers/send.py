"""
cloud/routers/send.py — Secure send endpoint.

Sicherheitsstufen:
  level1  — Sicherer Link
  level2  — Sicherer Link + Gastkonto
  level3  — E2E-Dateien + Gastkonto
  level4  — E2E-Dateien+Text + Gastkonto
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user, org_user_required
from hosted_cfg import merge_hosted_storage_cfg, is_hosted_service
from models.organization import Organization
from models.reseller import Reseller
from models.shared import CloudProvider, Guest, History, SmsGateway
from models.user import User
from schemas.shared import CloudProviderSendOption, SendResponse
from services.audit import (
    actor_fields,
    email_domain_hash,
    log_audit_event,
    mask_email,
    redact_exception_message,
)
from services.hosted_provider import (
    ensure_hosted_cloud_provider,
    merge_org_settings_with_storage_defaults,
    resolve_send_cloud_provider,
    resolve_storage_quota_bytes,
)
from services.smtp_resolve import resolve_smtp_with_fallback
from services.security_levels import (
    DEFAULT_SECURITY_LEVEL,
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    LEVEL_4,
    is_addin_channel,
    normalize_client_channel,
    normalize_allowed_security_levels,
    normalize_security_level,
    resolve_effective_level_for_channel,
)


def _parse_single_recipient_email(raw: str) -> str:
    """
    Exactly one e-mail address, or empty. Rejects comma/semicolon/newline-separated lists.
    """
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    parts = re.split(r"[,\n;]+", s)
    candidates: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if "@" not in p or len(p) >= 320:
            continue
        _local, _, domain = p.partition("@")
        if not _local or not domain or "." not in domain:
            continue
        candidates.append(p)
    nonempty_parts = [p.strip() for p in parts if p.strip()]
    if len(candidates) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nur eine E-Mail-Adresse erlaubt.",
        )
    if len(candidates) == 1:
        return candidates[0]
    if nonempty_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bitte eine gültige E-Mail-Adresse angeben.",
        )
    return ""


def _form_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    return default

from config import settings as app_settings
from core.hosted_storage import HOSTED_SERVICE_NAME

router = APIRouter(prefix="/send", tags=["send"])

_ALPHABET = string.ascii_letters + string.digits

BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
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

def _random_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def _get_sms_gateway(db: AsyncSession, org_id: str) -> Optional[SmsGateway]:
    result = await db.execute(
        select(SmsGateway).where(
            SmsGateway.org_id == org_id,
            SmsGateway.is_default == True,  # noqa: E712
            SmsGateway.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


@router.get("/providers", response_model=list[CloudProviderSendOption])
async def list_send_providers(
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> list[CloudProviderSendOption]:
    """
    Aktive Cloud-Anbieter der eigenen Organisation für die Senden-Seite.
    (Org-User haben keinen Zugriff auf GET /admin/org/providers.)
    """
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Organisations-Benutzer können Dateien senden",
        )
    org_id = current_user.org_id
    await ensure_hosted_cloud_provider(db, org_id)
    await db.commit()
    result = await db.execute(
        select(CloudProvider)
        .where(
            CloudProvider.org_id == org_id,
            CloudProvider.is_active == True,  # noqa: E712
        )
        .order_by(CloudProvider.name)
    )
    providers = result.scalars().all()
    return [CloudProviderSendOption.model_validate(p) for p in providers]


@router.post("", response_model=SendResponse)
async def send_secure(
    request: Request,
    files: List[UploadFile] = File(default=[]),
    to_email: str = Form(default=""),
    to_phone: str = Form(default=""),
    subject: str = Form(default="Ihr sicheres Dokument"),
    message: str = Form(default=""),
    personal_message: str = Form(default=""),
    expiry_days: int = Form(default=7),
    security_level: str = Form(default=DEFAULT_SECURITY_LEVEL),
    sms_password_delivery: str = Form(default="0"),
    provider_id: Optional[str] = Form(default=None),
    encrypted_files: Optional[str] = Form(
        default=None
    ),  # JSON string for client-side encryption
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> SendResponse:
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Organisations-Benutzer können Dateien senden",
        )

    # ── Genau ein Empfänger (E-Mail) ──────────────────────────────────────────
    to_email = _parse_single_recipient_email(to_email)

    # ── Eingaben bereinigen (Header-Injection-Schutz) ───────────────────────
    subject = subject.replace("\n", " ").replace("\r", " ").strip()[:200]
    personal_message = personal_message[:2000]

    org_id = current_user.org_id

    # ── Sicherheitsstufe validieren (gegen erlaubte Stufen der Org) ─────────
    org_settings = {}
    if current_user.organization and current_user.organization.settings_json:
        org_settings = current_user.organization.settings_json

    allowed_levels = normalize_allowed_security_levels(
        org_settings.get("allowed_security_levels")
    )
    default_level = normalize_security_level(
        org_settings.get("default_security_level"), default=DEFAULT_SECURITY_LEVEL
    )
    if default_level not in allowed_levels:
        default_level = allowed_levels[0]

    requested_security_level = normalize_security_level(
        security_level, default=default_level
    )
    if requested_security_level not in allowed_levels:
        requested_security_level = default_level

    client_channel = normalize_client_channel(
        request.headers.get("x-securesend-client")
        or request.headers.get("x-client-channel")
        or request.headers.get("x-client-type")
    )
    level_downgrade_notice: Optional[str] = None
    security_level, level_downgrade_notice = resolve_effective_level_for_channel(
        requested_security_level, client_channel
    )

    use_sms = _form_bool(sms_password_delivery, default=False)
    if use_sms and not to_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Für Passwortversand per SMS ist eine Mobilnummer erforderlich.",
        )
    share_password: Optional[str] = None
    e2e_sms_password: Optional[str] = None
    enc_data_parsed: Optional[list] = None
    recipient_has_guest_account = False

    if to_email:
        existing_guest = await db.execute(
            select(Guest).where(Guest.email == to_email.strip().lower())
        )
        recipient_has_guest_account = existing_guest.scalar_one_or_none() is not None

    if security_level in (LEVEL_1, LEVEL_2) and use_sms:
        share_password = _random_password()

    if security_level in (LEVEL_3, LEVEL_4):
        if encrypted_files:
            try:
                enc_data_parsed = json.loads(encrypted_files)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ungültiges verschlüsseltes Dateiformat",
                )
            if not enc_data_parsed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Für Stufe 3/4 ist Ende-zu-Ende-Verschlüsselung erforderlich.",
                )
            pws = {item.get("password") for item in enc_data_parsed if item.get("password")}
            if len(pws) == 1:
                e2e_sms_password = next(iter(pws))
            elif pws:
                e2e_sms_password = enc_data_parsed[0].get("password")
            if not e2e_sms_password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="E2E-Passwort fehlt in den verschlüsselten Daten.",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Für Stufe 3/4 sind verschlüsselte Daten erforderlich.",
            )

    # ── Dateien validieren und lesen ───────────────────────────────────────
    valid_files = [f for f in files if f.filename]
    file_entries: list[tuple[str, bytes, str]] = []

    if security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed:
        for item in enc_data_parsed:
            if "filename" not in item or "encryptedData" not in item:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ungültiges verschlüsseltes Dateiformat (filename/encryptedData)",
                )
            ext = PurePosixPath(item["filename"]).suffix.lower()
            if ext in BLOCKED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Dateityp nicht erlaubt: {item['filename']} ({ext})",
                )
            encrypted_bytes = base64.b64decode(item["encryptedData"])
            file_entries.append(
                (
                    f"{item['filename']}.enc",
                    encrypted_bytes,
                    "application/octet-stream",
                )
            )
    elif valid_files:
        for f in valid_files:
            ext = PurePosixPath(f.filename).suffix.lower()
            if ext in BLOCKED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Dateityp nicht erlaubt: {f.filename} ({ext})",
                )
        for f in valid_files:
            data = await f.read()
            file_entries.append(
                (f.filename, data, f.content_type or "application/octet-stream")
            )

    # ── Upload-Größenlimit ──────────────────────────────────────────────────
    from config import settings as _cfg  # type: ignore

    max_bytes = _cfg.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    total_size = sum(len(d) for _, d, _ in file_entries)
    if total_size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Gesamtgröße überschreitet das Maximum von {_cfg.MAX_UPLOAD_SIZE_MB} MB",
        )

    # ── Virenscanner ───────────────────────────────────────────────────────
    from core.antivirus import rejection_user_message, scan_bytes  # type: ignore

    for fname, data, mime in file_entries:
        is_clean, msg = scan_bytes(data, fname)
        if not is_clean:
            raise HTTPException(
                status_code=422,
                detail=rejection_user_message(fname, msg),
            )

    if not file_entries and not message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Datei-Upload oder Nachricht erforderlich",
        )

    # ── Organisation (Kontingent) + Cloud-Anbieter ───────────────────────────
    org_row = await db.execute(
        select(Organization).where(Organization.id == org_id).with_for_update()
    )
    org_ent = org_row.scalar_one_or_none()
    if not org_ent:
        raise HTTPException(status_code=403, detail="Organisation nicht gefunden")

    org_settings_merged = merge_org_settings_with_storage_defaults(
        org_ent.settings_json
    )
    provider = await resolve_send_cloud_provider(
        db, org_id, org_settings_merged, provider_id
    )

    used_bytes = int(org_settings_merged.get("storage_used_bytes", 0))
    quota_bytes = await resolve_storage_quota_bytes(db, org_ent)
    if used_bytes + total_size > quota_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                "Speicherkontingent überschritten. Kontaktieren Sie Ihren Administrator "
                f"(Limit {quota_bytes // (1024 ** 3)} GB)."
            ),
        )

    cfg: dict = dict(provider.config_json or {})
    cfg["service"] = provider.service
    if provider.service == HOSTED_SERVICE_NAME:
        cfg = merge_hosted_storage_cfg(
            cfg,
            org_id,
            quota_used=used_bytes,
            quota_total=quota_bytes,
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_folder = cfg.get("folder", "SecureSend")
    folder_path = f"{base_folder}/{current_user.id}/{ts}"

    files_json: Optional[list] = None
    storage_folder_path: Optional[str] = None
    storage_delete_filename: Optional[str] = None

    # ── Upload je nach Sicherheitsstufe ───────────────────────────────────
    try:
        import asyncio
        from core.storage import upload_files_and_share_folder, upload_and_share  # type: ignore

        if security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed:
            upload_list = list(file_entries)
            share_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: upload_files_and_share_folder(
                    cfg=cfg,
                    files=upload_list,
                    folder_path=folder_path,
                    password=None,
                    days=expiry_days,
                ),
            )
            filename = (
                f"{len(file_entries)} Datei(en) (Ende-zu-Ende verschlüsselt)"
                if file_entries
                else "Nachricht (Ende-zu-Ende verschlüsselt)"
            )
            files_json = [
                {
                    "name": item["filename"],
                    "size": 0,
                    "type": "application/octet-stream",
                }
                for item in enc_data_parsed
            ]
            storage_folder_path = folder_path

        elif file_entries:
            share_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: upload_files_and_share_folder(
                    cfg=cfg,
                    files=file_entries,
                    folder_path=folder_path,
                    password=share_password,
                    days=expiry_days,
                ),
            )
            filename = (
                valid_files[0].filename
                if len(valid_files) == 1
                else f"{len(valid_files)} Dateien"
            )
            # Build files_json for the history
            files_json = None
            files_json_list = []
            for f in valid_files:
                size = 0
                if hasattr(f, "size"):
                    size = f.size
                files_json_list.append(
                    {
                        "name": f.filename,
                        "size": size,
                        "type": f.content_type or "application/octet-stream",
                    }
                )
            files_json = files_json_list if files_json_list else None
            storage_folder_path = folder_path

        else:
            # Nur Textnachricht
            filename = f"nachricht_{ts}.md"
            share_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: upload_and_share(
                    cfg=cfg,
                    filename=filename,
                    content=message.encode("utf-8"),
                    password=share_password,
                    days=expiry_days,
                    content_type="text/markdown; charset=utf-8",
                    subfolder=current_user.id,
                ),
            )
            if provider.service == HOSTED_SERVICE_NAME:
                rel_sub = (
                    f"{base_folder}/{current_user.id}".strip("/")
                    if current_user.id
                    else base_folder
                )
                storage_folder_path = rel_sub
                storage_delete_filename = filename

    except HTTPException:
        raise
    except Exception as exc:
        import logging as _log

        _log.getLogger("securesend").exception(
            "Upload fehlgeschlagen für User %s: %s", current_user.id, exc
        )
        await log_audit_event(
            event_type="send_failed_upload",
            severity="error",
            status="failure",
            error_code="upload_failed",
            error_message_redacted=redact_exception_message(exc),
            meta_json={
                "security_level": security_level,
            },
            **actor_fields(current_user),
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Fehler beim Hochladen. Bitte versuchen Sie es erneut.",
        ) from exc

    # ── Speicherverbrauch (Hosted + alle Anbieter) ──────────────────────────
    post_sj = dict(org_settings_merged)
    post_sj["storage_used_bytes"] = used_bytes + total_size
    org_ent.settings_json = post_sj

    # ── SMTP + Signatur (Org → Reseller → ENV, nur nutzbare Konfigurationen) ─
    org_settings: dict = post_sj
    signature: str = org_settings.get("signature", "")
    reseller_json: Optional[dict] = None
    if current_user.organization:
        res_result = await db.execute(
            select(Reseller).where(Reseller.id == current_user.organization.reseller_id)
        )
        reseller = res_result.scalar_one_or_none()
        if reseller and reseller.settings_json:
            reseller_json = dict(reseller.settings_json)
            if not signature:
                signature = reseller_json.get("signature", "")

    smtp_cfg, smtp_source = resolve_smtp_with_fallback(org_settings, reseller_json)

    tracking_token = secrets.token_urlsafe(32)
    base_url = str(request.base_url).rstrip("/")
    if is_hosted_service(provider.service):
        eff = (app_settings.PUBLIC_BASE_URL or base_url).rstrip("/")
        persisted_share_url = f"{eff}/track/l/{tracking_token}"
    else:
        persisted_share_url = share_url

    sender_name = (
        f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
        or current_user.email
    )
    org_name = (
        current_user.organization.name if current_user.organization else ""
    ) or ""

    if to_email:
        if not smtp_cfg:
            await log_audit_event(
                event_type="send_failed_smtp",
                severity="error",
                status="failure",
                error_code="smtp_not_configured",
                error_message_redacted="Kein nutzbares SMTP (Org/Reseller/ENV).",
                meta_json={
                    "smtp_source": smtp_source,
                    "recipient_masked": mask_email(to_email),
                    "recipient_domain_sha256_16": email_domain_hash(to_email),
                    "security_level": security_level,
                    "provider": provider.service,
                },
                **actor_fields(current_user),
                commit=True,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "E-Mail konnte nicht gesendet werden: Es ist kein gültiger SMTP-Server "
                    "konfiguriert (Organisation, Reseller oder Server-Umgebung)."
                ),
            )
        try:
            from core.email import send_email  # type: ignore

            personal_block = (
                f"<div style='background:#f8fafc;border-left:3px solid #1a56db;"
                f"padding:0.75rem 1rem;margin-bottom:1.5rem;border-radius:0 0.375rem 0.375rem 0;"
                f"font-style:italic;color:#475569;'>{personal_message}</div>"
                if personal_message and security_level != LEVEL_4
                else ""
            )

            if security_level == LEVEL_1:
                pw_hint = ""
                link_hint = (
                    "<p>Sicherer Link ohne Login. Optionales Passwort wurde "
                    + ("per SMS zugestellt." if use_sms else "separat vereinbart.")
                    + "</p>"
                )
            elif security_level == LEVEL_2:
                pw_hint = (
                    "<p>Zum Öffnen erstellen Sie ein Gastkonto oder melden sich im vorhandenen "
                    "Gastkonto an.</p>"
                )
                link_hint = "<p>Für zukünftige Sendungen ist damit eine höhere Sicherheit möglich.</p>"
            elif security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed:
                if use_sms and to_phone:
                    e2e_pw_channel = (
                        "<p>Das Entschlüsselungspasswort erhalten Sie zusätzlich per SMS an die "
                        "angegebene Mobilnummer.</p>"
                    )
                else:
                    e2e_pw_channel = (
                        "<p>Das Entschlüsselungspasswort wird <strong>nicht</strong> per SMS versendet. "
                        "Der Absender teilt es Ihnen auf einem anderen, mit Ihnen vereinbarten Weg mit.</p>"
                    )
                pw_hint = (
                    "<p><strong>Ende-zu-Ende-Verschlüsselung:</strong> Öffnen Sie den Link und melden Sie sich "
                    "im Gastportal an. Anschließend können Sie die Inhalte in Ihrem Browser entschlüsseln.</p>"
                    + e2e_pw_channel
                    + "<p>Der Server speichert Ihre Dateiinhalte nicht im Klartext.</p>"
                )
                if level_downgrade_notice:
                    pw_hint += (
                        "<p><strong>Hinweis:</strong> Die angeforderte Stufe 4 ist aktuell nur über das "
                        "Outlook-Add-in verfügbar. Dieser Versand wurde als Stufe 3 erstellt.</p>"
                    )
                if security_level == LEVEL_4:
                    link_hint = "<p>Auch der Nachrichtentext wurde Ende-zu-Ende verschlüsselt übertragen.</p>"
                else:
                    link_hint = ""
            else:
                pw_hint = ""
                link_hint = ""

            tracking_link = f"{base_url}/track/l/{tracking_token}"
            tracking_pixel = f'<img src="{base_url}/track/o/{tracking_token}" width="1" height="1" style="display:none" alt="" />'
            cta_label = (
                "Sichere Nachricht öffnen"
                if security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed
                else "Datei herunterladen"
            )

            body_html = f"""
            <div style="font-family:sans-serif;color:#1e293b;max-width:540px;">
              <h2 style="color:#1a56db;margin-bottom:0.5rem;">{subject}</h2>
              <p style="color:#64748b;margin-bottom:1.5rem;">
                {sender_name}{" · " + org_name if org_name else ""} hat eine Datei sicher für Sie bereitgestellt.
              </p>
              {personal_block}
              <p>
                <a href="{tracking_link}" style="display:inline-block;background:#1a56db;color:#fff;
                   padding:0.625rem 1.25rem;border-radius:0.5rem;text-decoration:none;font-weight:600;">
                  {cta_label}
                </a>
              </p>
              <p style="font-size:0.875rem;color:#64748b;word-break:break-all;">
                Link: <a href="{tracking_link}" style="color:#1a56db;">{persisted_share_url}</a>
              </p>
              {pw_hint}{link_hint}
              <p style="font-size:0.8125rem;color:#94a3b8;">Gültig für {expiry_days} Tag(e).</p>
              {'<hr style="border:none;border-top:1px solid #e2e8f0;margin-top:1.5rem;"/>' + f'<p style="font-size:0.8125rem;color:#94a3b8;">{signature}</p>' if signature else ""}
              {tracking_pixel}
            </div>
            """
            send_email(smtp_cfg, to_email, subject, body_html)
        except Exception as exc:
            import logging

            logging.getLogger("send").warning("Email send failed: %s", exc)
            await log_audit_event(
                event_type="send_failed_smtp",
                severity="error",
                status="failure",
                error_code="smtp_send_failed",
                error_message_redacted=redact_exception_message(exc),
                meta_json={
                    "smtp_source": smtp_source,
                    "recipient_masked": mask_email(to_email),
                    "recipient_domain_sha256_16": email_domain_hash(to_email),
                    "security_level": security_level,
                    "provider": provider.service,
                },
                **actor_fields(current_user),
                commit=True,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "E-Mail-Zustellung fehlgeschlagen. Bitte SMTP-Einstellungen prüfen "
                    "(Organisation, Reseller oder Server-Umgebung)."
                ),
            ) from exc

    # ── Verlauf speichern (nach erfolgreicher E-Mail, falls erforderlich) ───
    client_ip = request.client.host if request.client else ""
    history_id = str(uuid.uuid4())
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now_naive + timedelta(days=expiry_days)
    msg_src = (personal_message or message or "").strip()
    if security_level == LEVEL_4:
        message_preview = None
    elif len(msg_src) > 600:
        message_preview = msg_src[:597] + "..."
    else:
        message_preview = msg_src or None

    encrypted_files_store = None
    if security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed:
        encrypted_files_store = {
            "folder_path": folder_path,
            "provider_id": provider.id,
            "files": [
                {
                    "filename": item["filename"],
                    "storage_name": f"{item['filename']}.enc",
                }
                for item in enc_data_parsed
            ],
        }

    h = History(
        id=history_id,
        user_id=current_user.id,
        to_email=to_email,
        to_phone=to_phone,
        filename=filename,
        subject=subject,
        message_preview=message_preview,
        share_url=persisted_share_url,
        provider=provider.service,
        expiry_days=expiry_days,
        expires_at=expires_at,
        security_level=security_level,
        ip_address=client_ip,
        encrypted_files_json=encrypted_files_store,
        files_json=files_json,
        storage_folder_path=storage_folder_path,
        storage_delete_filename=storage_delete_filename,
        cloud_provider_id=provider.id,
        tracking_token=tracking_token,
    )
    db.add(h)
    await log_audit_event(
        event_type="send_success",
        severity="info",
        status="success",
        target_type="history",
        target_id=history_id,
        meta_json={
            "smtp_source": smtp_source if to_email else None,
            "has_email": bool(to_email),
            "recipient_masked": mask_email(to_email) if to_email else None,
            "recipient_domain_sha256_16": email_domain_hash(to_email) if to_email else None,
            "recipient_has_guest_account": recipient_has_guest_account,
            "requested_security_level": requested_security_level,
            "effective_security_level": security_level,
            "level4_downgraded": bool(level_downgrade_notice),
            "level4_downgrade_reason": (
                "web_channel_addin_only" if level_downgrade_notice else None
            ),
            "client_channel": client_channel or None,
            "client_is_addin": is_addin_channel(client_channel),
            "security_level": security_level,
            "provider": provider.service,
            "file_label_len": len(filename or ""),
        },
        **actor_fields(current_user),
        db=db,
        commit=False,
    )
    await db.commit()
    await db.refresh(h)

    effective_password = share_password or e2e_sms_password

    # ── SMS senden ─────────────────────────────────────────────────────────
    if use_sms and to_phone and effective_password:
        gateway = await _get_sms_gateway(db, org_id)
        if gateway and gateway.config_json:
            try:
                from core.sms import send_sms_sipgate  # type: ignore

                if security_level in (LEVEL_3, LEVEL_4) and enc_data_parsed:
                    short_link = f"{base_url}/track/l/{h.tracking_token}"
                    sms_text = f"{subject}\n{short_link}\nE2E-Passwort: {effective_password}"
                else:
                    sms_text = f"{subject}\nLink: {persisted_share_url}\nPW: {effective_password}"
                if len(sms_text) > 160:
                    sms_text = sms_text[:157] + "..."
                send_sms_sipgate(gateway.config_json, to_phone, sms_text)
            except Exception as exc:
                import logging

                logging.getLogger("send").warning("SMS send failed: %s", exc)

    return SendResponse(
        share_url=persisted_share_url,
        filename=filename,
        provider=provider.service,
        expiry_days=expiry_days,
        history_id=history_id,
        recipient_has_guest_account=recipient_has_guest_account,
        requested_security_level=requested_security_level,
        effective_security_level=security_level,
        level_downgrade_notice=level_downgrade_notice,
    )
