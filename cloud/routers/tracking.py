"""cloud/routers/tracking.py — Email open tracking + link-click redirect."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import org_admin_required
from hosted_cfg import merge_hosted_storage_cfg
from models.organization import Organization
from models.shared import CloudProvider, History, DownloadLog
from models.user import User, UserRole
from services.hosted_provider import (
    merge_org_settings_with_storage_defaults,
    resolve_storage_quota_bytes,
)

from core.hosted_storage import HOSTED_SERVICE_NAME

_log = logging.getLogger("securesend.tracking")

router = APIRouter(tags=["tracking"])

# Minimal 1x1 transparent GIF
_PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


@router.get("/track/o/{token}", include_in_schema=False)
async def track_open(token: str, db: AsyncSession = Depends(get_db)) -> Response:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    if h and not h.opened_at:
        h.opened_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/track/l/{token}", include_in_schema=False)
async def track_link(
    token: str, db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    url = "#"
    if h:
        if not h.link_clicked_at:
            h.link_clicked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
        enc = h.encrypted_files_json or {}
        if (
            h.security_level in ("advanced", "maximal")
            and enc.get("folder_path")
            and enc.get("files")
        ):
            url = f"/decrypt/{token}"
        else:
            url = h.share_url or "#"
    return RedirectResponse(url=url, status_code=302)


# ── Download Tracking ─────────────────────────────────────────────────────────


@router.get("/track/d/{token}", include_in_schema=False)
async def track_download(
    token: str,
    filename: str = "",
    email: str = "",
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """Track download event and log it."""
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        return {"error": "Not found"}, 404

    # Update download count on history
    h.download_count = (h.download_count or 0) + 1
    h.last_downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Get sender info for notification
    result_sender = await db.execute(select(User).where(User.id == h.user_id))
    sender = result_sender.scalar_one_or_none()

    # Send notification to sender (async, don't wait)
    if sender and sender.email:
        try:
            from core.email import send_email

            # Get org SMTP if available
            if sender.organization and sender.organization.settings_json:
                smtp_cfg = sender.organization.settings_json.get("smtp")
                if smtp_cfg:
                    send_email(
                        smtp_cfg,
                        sender.email,
                        f"Download: {getattr(h, 'subject', None) or h.filename}",
                        f"<p>Eine Datei wurde heruntergeladen:</p>"
                        f"<p><strong>Datei:</strong> {filename or h.filename}</p>"
                        f"<p><strong>Empfänger:</strong> {email or h.to_email}</p>"
                        f"<p><strong>Zeit:</strong> {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')}</p>",
                    )
        except Exception as e:
            _log.warning("Download notification email failed: %s", e)

    # Log detailed download info (anonymized IP - last 2 bytes only for privacy)
    client_ip = request.client.host if request else ""
    if client_ip and "." in client_ip:
        # Anonymize IPv4: x.x.x.x -> x.x.x.0
        parts = client_ip.rsplit(".", 1)
        if len(parts) == 2:
            client_ip = parts[0] + ".0"
    user_agent = request.headers.get("user-agent", "")[:200] if request else ""

    dl_log = DownloadLog(
        history_id=h.id,
        ip_address=client_ip,
        user_agent=user_agent,
        email=email or h.to_email,
        filename=filename or h.filename,
    )
    db.add(dl_log)
    await db.commit()

    # Return OK for tracking (actual file download handled by cloud provider)
    return {"ok": True, "download_count": h.download_count}


# ── Download Logs API for Admin ────────────────────────────────────────────────


@router.get("/admin/org/downloads", tags=["admin-org"])
async def get_download_logs(
    history_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
):
    """Get download logs for a history entry (admin only)."""
    from models.organization import Organization

    # Resolve org_id
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(
                status_code=400, detail="org_id required for superadmin"
            )
    else:
        org_id = current_user.org_id

    # Get history entry and verify it belongs to user's org
    result = await db.execute(select(History).where(History.id == history_id))
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # Verify org access
    if current_user.role != UserRole.superadmin:
        if history.user_id != current_user.id:
            # Check if user belongs to same org
            user_result = await db.execute(
                select(User).where(User.id == history.user_id)
            )
            history_user = user_result.scalar_one_or_none()
            if not history_user or history_user.org_id != org_id:
                raise HTTPException(status_code=403, detail="Access denied")

    # Get download logs
    logs_result = await db.execute(
        select(DownloadLog)
        .where(DownloadLog.history_id == history_id)
        .order_by(DownloadLog.downloaded_at.desc())
    )
    logs = logs_result.scalars().all()

    return [
        {
            "id": log.id,
            "downloaded_at": log.downloaded_at.isoformat()
            if log.downloaded_at
            else None,
            "ip_address": log.ip_address,
            "email": log.email,
            "filename": log.filename,
            "user_agent": log.user_agent,
        }
        for log in logs
    ]


@router.get("/admin/org/downloads/export", tags=["admin-org"])
async def export_download_logs(
    history_id: str,
    format: str = "csv",
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
):
    """Export download logs as CSV."""
    import csv
    import io

    # Verify org access
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(status_code=400, detail="org_id required")
        org_id = org_id
    else:
        org_id = current_user.org_id

    # Get history entry
    result = await db.execute(select(History).where(History.id == history_id))
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # Verify access
    if current_user.role != UserRole.superadmin:
        user_result = await db.execute(select(User).where(User.id == history.user_id))
        history_user = user_result.scalar_one_or_none()
        if not history_user or history_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Get download logs
    logs_result = await db.execute(
        select(DownloadLog)
        .where(DownloadLog.history_id == history_id)
        .order_by(DownloadLog.downloaded_at.desc())
    )
    logs = logs_result.scalars().all()

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Zeit", "E-Mail", "IP-Adresse", "Dateiname", "User-Agent"])

    for log in logs:
        writer.writerow(
            [
                log.downloaded_at.strftime("%d.%m.%Y %H:%M")
                if log.downloaded_at
                else "",
                log.email or "",
                log.ip_address or "",
                log.filename or "",
                log.user_agent[:100] if log.user_agent else "",
            ]
        )

    csv_content = output.getvalue()

    from fastapi.responses import Response

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=downloads_{history_id[:8]}.csv"
        },
    )


# ── Revoke Functionality ───────────────────────────────────────────────────────


@router.post("/admin/org/revoke/{history_id}", tags=["admin-org"])
async def revoke_send(
    history_id: str,
    reason: str = "",
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a sent file/link (admin only)."""
    from models.organization import Organization

    # Resolve org_id
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(
                status_code=400, detail="org_id required for superadmin"
            )
    else:
        org_id = current_user.org_id

    # Get history entry
    result = await db.execute(select(History).where(History.id == history_id))
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # Verify org access
    if current_user.role != UserRole.superadmin:
        user_result = await db.execute(select(User).where(User.id == history.user_id))
        history_user = user_result.scalar_one_or_none()
        if not history_user or history_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Check if already revoked
    if history.is_revoked:
        raise HTTPException(status_code=400, detail="Bereits zurückgerufen")

    # Revoke the send
    history.is_revoked = True
    history.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    history.revoked_by = current_user.id

    await db.commit()

    return {
        "ok": True,
        "revoked_at": history.revoked_at.isoformat(),
        "message": "Link wurde erfolgreich zurückgerufen",
    }


@router.post("/admin/org/unrevoke/{history_id}", tags=["admin-org"])
async def unrevoke_send(
    history_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
):
    """Unrevoke a previously revoked send."""
    from models.organization import Organization

    # Resolve org_id
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(
                status_code=400, detail="org_id required for superadmin"
            )
    else:
        org_id = current_user.org_id

    # Get history entry
    result = await db.execute(select(History).where(History.id == history_id))
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # Verify org access
    if current_user.role != UserRole.superadmin:
        user_result = await db.execute(select(User).where(User.id == history.user_id))
        history_user = user_result.scalar_one_or_none()
        if not history_user or history_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Check if not revoked
    if not history.is_revoked:
        raise HTTPException(status_code=400, detail="Nicht zurückgerufen")

    history.is_revoked = False
    history.revoked_at = None
    history.revoked_by = None

    await db.commit()

    return {"ok": True, "message": "Link wurde wiederhergestellt"}


@router.post("/admin/org/extend/{history_id}", tags=["admin-org"])
async def extend_send(
    history_id: str,
    days: int = Form(default=7),
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
):
    """Extend expiry days for a sent file/link."""
    # Resolve org_id
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(
                status_code=400, detail="org_id required for superadmin"
            )
    else:
        org_id = current_user.org_id

    # Get history entry
    result = await db.execute(select(History).where(History.id == history_id))
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History not found")

    # Verify org access
    if current_user.role != UserRole.superadmin:
        user_result = await db.execute(select(User).where(User.id == history.user_id))
        history_user = user_result.scalar_one_or_none()
        if not history_user or history_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Extend expiry: +days ab aktuellem Ablauf (oder created_at + bisherige Frist)
    old_days = history.expiry_days
    history.expiry_days = min(history.expiry_days + days, 90)  # Max 90 days
    effective = history.expires_at
    if effective is None and history.created_at:
        effective = history.created_at + timedelta(days=old_days)
    if effective is None:
        effective = datetime.now(timezone.utc).replace(tzinfo=None)
    history.expires_at = effective + timedelta(days=days)

    await db.commit()

    return {
        "ok": True,
        "expiry_days": history.expiry_days,
        "expires_at": history.expires_at.isoformat() if history.expires_at else None,
    }


# ── E2E (Stufe Advanced): Ciphertext aus Cloud, Entschlüsselung nur im Browser ─


async def _e2e_ciphertext_bundle(db: AsyncSession, h: History) -> dict[str, Any]:
    enc = h.encrypted_files_json
    if not enc or not enc.get("folder_path") or not enc.get("files"):
        raise HTTPException(
            status_code=400,
            detail="Keine vollständigen E2E-Metadaten (älterer Versand oder fehlerhafte Daten).",
        )

    ruser = await db.execute(select(User).where(User.id == h.user_id))
    user = ruser.scalar_one_or_none()
    if not user or not user.org_id:
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    pid = enc.get("provider_id")
    if pid:
        rp = await db.execute(
            select(CloudProvider).where(
                CloudProvider.id == pid,
                CloudProvider.org_id == user.org_id,
                CloudProvider.is_active == True,  # noqa: E712
            )
        )
        provider = rp.scalar_one_or_none()
    else:
        provider = None
    if not provider:
        rp = await db.execute(
            select(CloudProvider)
            .where(
                CloudProvider.org_id == user.org_id,
                CloudProvider.service == h.provider,
                CloudProvider.is_active == True,  # noqa: E712
            )
            .order_by(CloudProvider.is_default.desc())
        )
        provs = rp.scalars().all()
        provider = provs[0] if provs else None
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="Cloud-Anbieter nicht gefunden — E2E-Abruf nicht möglich.",
        )
    cfg: dict = dict(provider.config_json or {})
    cfg["service"] = provider.service
    if provider.service == HOSTED_SERVICE_NAME:
        org_row = await db.execute(
            select(Organization).where(Organization.id == user.org_id)
        )
        org_o = org_row.scalar_one_or_none()
        merged = merge_org_settings_with_storage_defaults(
            org_o.settings_json if org_o else None
        )
        used = int(merged.get("storage_used_bytes", 0))
        quota = await resolve_storage_quota_bytes(db, org_o) if org_o else 0
        cfg = merge_hosted_storage_cfg(
            cfg, user.org_id, quota_used=used, quota_total=quota
        )

    folder = enc["folder_path"]

    from core.storage import download_cloud_file

    out_files: list[dict[str, str]] = []
    for fmeta in enc["files"]:
        storage_name = fmeta.get("storage_name") or f"{fmeta.get('filename', 'file')}.enc"
        try:
            raw = await asyncio.to_thread(
                download_cloud_file, cfg, folder, storage_name
            )
        except Exception as exc:
            _log.exception("E2E Cloud-Download fehlgeschlagen: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Verschlüsselte Datei konnte nicht aus der Cloud geladen werden.",
            ) from exc
        out_files.append(
            {
                "filename": fmeta.get("filename", storage_name),
                "encryptedData": base64.b64encode(raw).decode("ascii"),
            }
        )

    return {"files": out_files}


@router.get("/track/e2e/{token}", include_in_schema=False)
async def track_e2e_bundle(
    token: str, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    if not h:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    if h.is_revoked:
        raise HTTPException(status_code=410, detail="Dieser Versand wurde zurückgerufen")
    if h.security_level not in ("advanced", "maximal"):
        raise HTTPException(status_code=400, detail="Kein E2E-Versand")
    payload = await _e2e_ciphertext_bundle(db, h)
    return JSONResponse(
        content=payload, headers={"Cache-Control": "no-store, no-transform"}
    )


_DECRYPT_PAGE = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ende-zu-Ende entschlüsseln – SecureSend</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; margin: 0; padding: 1.5rem; }
    .container { max-width: 32rem; margin: 0 auto; }
    .card { background: #fff; border-radius: 0.75rem; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    h1 { font-size: 1.2rem; color: #1e293b; margin: 0 0 0.75rem 0; }
    .muted { font-size: 0.875rem; color: #64748b; margin-bottom: 1rem; }
    label { display: block; font-size: 0.8125rem; font-weight: 600; color: #475569; margin-bottom: 0.35rem; }
    input[type=password] { width: 100%; box-sizing: border-box; padding: 0.6rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 0.5rem; font-size: 1rem; }
    button { margin-top: 1rem; width: 100%; padding: 0.65rem 1rem; background: #1a56db; color: #fff; border: none; border-radius: 0.5rem; font-weight: 600; cursor: pointer; font-size: 0.9375rem; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .err { background: #fef2f2; color: #b91c1c; padding: 0.75rem; border-radius: 0.5rem; font-size: 0.875rem; margin-top: 0.75rem; display: none; }
    .ok { margin-top: 1rem; }
    .dl { display: block; margin: 0.5rem 0; padding: 0.5rem 0.75rem; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 0.5rem; color: #166534; font-size: 0.875rem; text-decoration: none; }
    .hint { background: #fef3c7; border: 1px solid #fde68a; border-radius: 0.5rem; padding: 0.75rem; font-size: 0.8125rem; color: #78350f; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>Ende-zu-Ende entschlüsseln</h1>
      <p class="muted">Geben Sie das Passwort ein, das Sie per SMS erhalten haben. Die Entschlüsselung erfolgt nur in Ihrem Browser.</p>
      <div class="hint"><strong>Hinweis:</strong> Das Passwort wird nicht an den Server gesendet.</div>
      <label for="pw">Entschlüsselungspasswort</label>
      <input type="password" id="pw" autocomplete="off" placeholder="Passwort aus der SMS" />
      <button type="button" id="go">Dateien entschlüsseln</button>
      <div class="err" id="err"></div>
      <div class="ok" id="out"></div>
    </div>
  </div>
  <script>
  const TRACKING_TOKEN = __TOKEN_JSON__;
  function base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }
  async function deriveKey(password, salt) {
    const enc = new TextEncoder();
    const keyMaterial = await crypto.subtle.importKey(
      'raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: salt, iterations: 100000, hash: 'SHA-256' },
      keyMaterial, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  }
  async function decryptOne(combinedB64, password) {
    const combined = new Uint8Array(base64ToArrayBuffer(combinedB64));
    const salt = combined.slice(0, 16);
    const iv = combined.slice(16, 28);
    const ct = combined.slice(28);
    const key = await deriveKey(password, salt);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, ct);
    return plain;
  }
  document.getElementById('go').onclick = async function() {
    const errEl = document.getElementById('err');
    const outEl = document.getElementById('out');
    const btn = document.getElementById('go');
    errEl.style.display = 'none';
    outEl.innerHTML = '';
    const password = document.getElementById('pw').value;
    if (!password) {
      errEl.textContent = 'Bitte Passwort eingeben.';
      errEl.style.display = 'block';
      return;
    }
    btn.disabled = true;
    try {
      const res = await fetch('/track/e2e/' + encodeURIComponent(TRACKING_TOKEN));
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || res.statusText);
      }
      const data = await res.json();
      const files = data.files || [];
      if (!files.length) throw new Error('Keine Dateien');
      for (const f of files) {
        const blob = new Blob([await decryptOne(f.encryptedData, password)], { type: 'application/octet-stream' });
        const a = document.createElement('a');
        a.className = 'dl';
        a.href = URL.createObjectURL(blob);
        a.download = f.filename || 'download';
        a.textContent = 'Herunterladen: ' + (f.filename || 'Datei');
        outEl.appendChild(a);
      }
    } catch (e) {
      errEl.textContent = 'Entschlüsselung fehlgeschlagen. Passwort prüfen oder erneut versuchen. (' + (e.message || e) + ')';
      errEl.style.display = 'block';
    }
    btn.disabled = false;
  };
  </script>
</body>
</html>"""


@router.get("/decrypt/{token}", response_class=HTMLResponse, include_in_schema=False)
async def decrypt_page(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()

    if not h:
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    if h.security_level not in ("advanced", "maximal"):
        if h.share_url:
            return RedirectResponse(url=h.share_url, status_code=302)
        raise HTTPException(status_code=400, detail="Keine verschlüsselten Dateien")

    enc_files = h.encrypted_files_json
    if not enc_files or not enc_files.get("files"):
        raise HTTPException(
            status_code=400, detail="Keine verschlüsselten Dateien gefunden"
        )

    html = _DECRYPT_PAGE.replace(
        "__TOKEN_JSON__", json.dumps(token)
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, no-transform"})
