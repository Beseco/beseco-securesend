"""
cloud/routers/send.py — Secure send endpoint.

Sicherheitsstufen:
  normal    — Link per E-Mail, kein Passwort, kein SMS
  standard  — wie secure, Passwort am Share (ohne SMS)
  secure    — Passwortgeschützter Link per E-Mail + Passwort per SMS  (Standard)
  extended  — AES-ZIP im Ordner + ZIP-Passwort per SMS
  advanced  — Client AES-GCM, Schlüssel nur per SMS, Entschlüsselung im Browser (/decrypt)
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import string
import uuid
from datetime import datetime, timezone
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
from models.shared import CloudProvider, History, SmsGateway
from models.user import User
from schemas.shared import SendResponse
from services.hosted_provider import (
    merge_org_settings_with_storage_defaults,
    resolve_send_cloud_provider,
    resolve_storage_quota_bytes,
)

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

_INFO_TXT = """\
=== SecureSend – Sicherheitshinweis ===

Die Datei(en) in diesem Ordner sind in einem verschlüsselten
ZIP-Archiv (AES-256) gespeichert.

Das Passwort haben Sie separat per SMS erhalten.

So öffnen Sie die Dateien:
─────────────────────────────────────
  Windows : 7-Zip oder WinRAR
  macOS   : Finder oder „The Unarchiver"
  Linux   : unzip -P <passwort> datei.zip

1. ZIP-Datei herunterladen
2. Entpackprogramm öffnen
3. Per SMS erhaltenes Passwort eingeben
4. Fertig

Warum zwei Kanäle?
──────────────────
  Link  →  E-Mail   (etwas das Sie haben)
  PW    →  SMS      (etwas das Ihr Handy hat)

Nur wer Zugang zu beiden Kanälen hat, kann die Dateien öffnen.

Gesendet mit SecureSend – Sicheres Übermittlungssystem
"""


def _random_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _build_encrypted_zip(
    file_entries: list[tuple[str, bytes, str]],
    message: str,
    password: str,
) -> bytes:
    """Erstellt ein AES-256-verschlüsseltes ZIP (pyzipper), Fallback: Standard-ZIP."""
    buf = io.BytesIO()
    try:
        import pyzipper

        with pyzipper.AESZipFile(
            buf,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode())
            for fname, data, _ in file_entries:
                zf.writestr(fname, data)
            if message:
                zf.writestr("nachricht.md", message.encode("utf-8"))
    except ImportError:
        import zipfile
        import logging

        logging.getLogger("send").warning(
            "pyzipper not installed – falling back to unencrypted ZIP"
        )
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data, _ in file_entries:
                zf.writestr(fname, data)
            if message:
                zf.writestr("nachricht.md", message.encode("utf-8"))
    return buf.getvalue()


async def _get_sms_gateway(db: AsyncSession, org_id: str) -> Optional[SmsGateway]:
    result = await db.execute(
        select(SmsGateway).where(
            SmsGateway.org_id == org_id,
            SmsGateway.is_default == True,  # noqa: E712
            SmsGateway.is_active == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


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
    security_level: str = Form(
        default="secure"
    ),  # normal | secure | extended | advanced | maximal
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

    # ── Bulk: Mehrere E-Mails aufteilen ─────────────────────────────────────────
    recipient_emails = []
    if to_email:
        # Split by comma, semicolon, or newline
        import re

        emails = re.split(r"[,\n;]+", to_email)
        for e in emails:
            e = e.strip()
            if e and "@" in e:
                # Basic validation
                if len(e) < 320 and "." in e.split("@")[1]:
                    recipient_emails.append(e)

    # Use first email as primary, rest as additional tracking
    if recipient_emails:
        to_email = recipient_emails[0]

    # ── Eingaben bereinigen (Header-Injection-Schutz) ───────────────────────
    subject = subject.replace("\n", " ").replace("\r", " ").strip()[:200]
    personal_message = personal_message[:2000]

    org_id = current_user.org_id

    # ── Sicherheitsstufe validieren (gegen erlaubte Stufen der Org) ─────────
    org_settings = {}
    if current_user.organization and current_user.organization.settings_json:
        org_settings = current_user.organization.settings_json

    allowed_levels = org_settings.get(
        "allowed_security_levels",
        ["normal", "standard", "secure", "extended", "advanced", "maximal"],
    )
    if security_level not in allowed_levels:
        security_level = org_settings.get("default_security_level", "secure")
        if security_level not in allowed_levels:
            security_level = allowed_levels[0] if allowed_levels else "secure"

    # Sicherheitsstufe normalisieren (alle bekannten Stufen)
    valid_levels = ["normal", "standard", "secure", "extended", "advanced", "maximal"]
    if security_level not in valid_levels:
        security_level = "secure"

    # use_sms für Stufen die SMS benötigen
    use_sms = security_level in ("secure", "extended", "advanced", "maximal")
    share_password: Optional[str] = None
    zip_password: Optional[str] = None
    e2e_sms_password: Optional[str] = None
    enc_data_parsed: Optional[list] = None

    if security_level == "secure":
        share_password = _random_password()
    elif security_level == "extended":
        zip_password = _random_password()
    elif security_level in ("advanced", "maximal"):
        if encrypted_files:
            try:
                enc_data_parsed = json.loads(encrypted_files)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ungültiges verschlüsseltes Dateiformat",
                )
            if not enc_data_parsed:
                share_password = _random_password()
            else:
                pws = {
                    item.get("password")
                    for item in enc_data_parsed
                    if item.get("password")
                }
                if len(pws) == 1:
                    e2e_sms_password = next(iter(pws))
                elif pws:
                    e2e_sms_password = enc_data_parsed[0].get("password")
        else:
            share_password = _random_password()

    # ── Dateien validieren und lesen ───────────────────────────────────────
    valid_files = [f for f in files if f.filename]
    file_entries: list[tuple[str, bytes, str]] = []

    if security_level in ("advanced", "maximal") and enc_data_parsed:
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
    from core.antivirus import scan_bytes  # type: ignore

    for fname, data, mime in file_entries:
        is_clean, msg = scan_bytes(data, fname)
        if not is_clean:
            raise HTTPException(
                status_code=422,
                detail=f"Datei '{fname}' enthält Schadsoftware und wurde abgelehnt.",
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

    # ── Upload je nach Sicherheitsstufe ───────────────────────────────────
    try:
        import asyncio
        from core.storage import upload_files_and_share_folder, upload_and_share  # type: ignore

        if security_level == "extended":
            # Alle Dateien + Nachricht in verschlüsseltem ZIP
            zip_bytes = _build_encrypted_zip(file_entries, message, zip_password)
            upload_list = [
                ("securesend_verschluesselt.zip", zip_bytes, "application/zip"),
                ("Info.txt", _INFO_TXT.encode("utf-8"), "text/plain"),
            ]
            share_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: upload_files_and_share_folder(
                    cfg=cfg,
                    files=upload_list,
                    folder_path=folder_path,
                    password=None,  # kein Passwort auf Share, Datei selbst ist verschlüsselt
                    days=expiry_days,
                ),
            )
            filename = (
                f"{len(file_entries)} Datei(en) (verschlüsselt)"
                if file_entries
                else "Nachricht (verschlüsselt)"
            )

        elif security_level in ("advanced", "maximal") and enc_data_parsed:
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

    except HTTPException:
        raise
    except Exception as exc:
        import logging as _log

        _log.getLogger("securesend").exception(
            "Upload fehlgeschlagen für User %s: %s", current_user.id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Fehler beim Hochladen. Bitte versuchen Sie es erneut.",
        ) from exc

    # ── Speicherverbrauch (Hosted + alle Anbieter) ──────────────────────────
    post_sj = dict(org_settings_merged)
    post_sj["storage_used_bytes"] = used_bytes + total_size
    org_ent.settings_json = post_sj

    # ── SMTP-Konfiguration ermitteln (Org → Reseller Fallback) ─────────────
    org_settings: dict = post_sj

    smtp_cfg: Optional[dict] = org_settings.get("smtp")
    signature: str = org_settings.get("signature", "")

    if not smtp_cfg and current_user.organization:
        res_result = await db.execute(
            select(Reseller).where(Reseller.id == current_user.organization.reseller_id)
        )
        reseller = res_result.scalar_one_or_none()
        if reseller and reseller.settings_json:
            smtp_cfg = reseller.settings_json.get("smtp")
            if not signature:
                signature = reseller.settings_json.get("signature", "")

    # ── Verlauf speichern (vor E-Mail, damit tracking_token verfügbar) ─────
    client_ip = request.client.host if request.client else ""
    history_id = str(uuid.uuid4())

    # Nur Metadaten für E2E (keine Passwörter persistieren)
    encrypted_files_store = None
    if security_level in ("advanced", "maximal") and enc_data_parsed:
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
        share_url=share_url,
        provider=provider.service,
        expiry_days=expiry_days,
        security_level=security_level,
        ip_address=client_ip,
        encrypted_files_json=encrypted_files_store,
        files_json=files_json,
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)

    # ── E-Mail senden ──────────────────────────────────────────────────────
    from config import settings as app_settings

    base_url = str(request.base_url).rstrip("/")
    if is_hosted_service(provider.service):
        eff = (app_settings.PUBLIC_BASE_URL or base_url).rstrip("/")
        h.share_url = f"{eff}/track/l/{h.tracking_token}"
        await db.commit()
        await db.refresh(h)
    effective_password = zip_password or share_password or e2e_sms_password
    sender_name = (
        f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
        or current_user.email
    )
    org_name = (
        current_user.organization.name if current_user.organization else ""
    ) or ""

    if to_email and smtp_cfg:
        try:
            from core.email import send_email  # type: ignore

            personal_block = (
                f"<div style='background:#f8fafc;border-left:3px solid #1a56db;"
                f"padding:0.75rem 1rem;margin-bottom:1.5rem;border-radius:0 0.375rem 0.375rem 0;"
                f"font-style:italic;color:#475569;'>{personal_message}</div>"
                if personal_message
                else ""
            )

            if security_level == "normal":
                pw_hint = ""
                link_hint = "<p>Der Link ist ohne Passwort zugänglich.</p>"
            elif security_level == "standard":
                pw_hint = (
                    "<p>Dieser Link ist passwortgeschützt. Das Passwort wurde mit Ihnen "
                    "auf anderem Weg vereinbart.</p>"
                )
                link_hint = ""
            elif security_level == "secure":
                pw_hint = "<p>Das Passwort für den Link erhalten Sie separat per SMS.</p>"
                link_hint = ""
            elif security_level in ("advanced", "maximal") and enc_data_parsed:
                pw_hint = (
                    "<p><strong>Ende-zu-Ende-Verschlüsselung:</strong> Öffnen Sie den Link "
                    "und geben Sie das Entschlüsselungspasswort ein, das Sie per SMS erhalten.</p>"
                    "<p>Der Server speichert Ihre Dateiinhalte nicht im Klartext.</p>"
                )
                link_hint = ""
            elif security_level == "extended":
                pw_hint = (
                    "<p>Das Passwort zum Entschlüsseln der ZIP-Datei erhalten Sie per SMS.</p>"
                    "<p>Bitte beachten Sie die <strong>Info.txt</strong> im Download-Ordner.</p>"
                )
                link_hint = ""
            elif security_level in ("advanced", "maximal"):
                pw_hint = "<p>Das Passwort für den Link erhalten Sie separat per SMS.</p>"
                link_hint = ""

            tracking_link = f"{base_url}/track/l/{h.tracking_token}"
            tracking_pixel = f'<img src="{base_url}/track/o/{h.tracking_token}" width="1" height="1" style="display:none" alt="" />'

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
                  Datei herunterladen
                </a>
              </p>
              <p style="font-size:0.875rem;color:#64748b;word-break:break-all;">
                Link: <a href="{tracking_link}" style="color:#1a56db;">{share_url}</a>
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

    # ── SMS senden ─────────────────────────────────────────────────────────
    if use_sms and to_phone and effective_password:
        gateway = await _get_sms_gateway(db, org_id)
        if gateway and gateway.config_json:
            try:
                from core.sms import send_sms_sipgate  # type: ignore

                if security_level == "extended":
                    sms_text = f"{subject}\nLink: {share_url}\nZIP-Passwort: {effective_password}"
                elif security_level in ("advanced", "maximal") and enc_data_parsed:
                    short_link = f"{base_url}/track/l/{h.tracking_token}"
                    sms_text = f"{subject}\n{short_link}\nE2E-Passwort: {effective_password}"
                else:
                    sms_text = f"{subject}\nLink: {share_url}\nPW: {effective_password}"
                if len(sms_text) > 160:
                    sms_text = sms_text[:157] + "..."
                send_sms_sipgate(gateway.config_json, to_phone, sms_text)
            except Exception as exc:
                import logging

                logging.getLogger("send").warning("SMS send failed: %s", exc)

    return SendResponse(
        share_url=share_url,
        filename=filename,
        provider=provider.service,
        expiry_days=expiry_days,
        history_id=history_id,
    )
