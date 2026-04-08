"""cloud/routers/tracking.py — Email open tracking + link-click redirect."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import Response, RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import org_admin_required
from models.shared import History, DownloadLog
from models.user import User, UserRole

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
            from config import settings

            # Get org SMTP if available
            if sender.organization and sender.organization.settings_json:
                smtp_cfg = sender.organization.settings_json.get("smtp")
                if smtp_cfg:
                    await send_email(
                        smtp_cfg,
                        sender.email,
                        f"Download: {h.subject or h.filename}",
                        f"<p>Eine Datei wurde heruntergeladen:</p>"
                        f"<p><strong>Datei:</strong> {filename or h.filename}</p>"
                        f"<p><strong>Empfänger:</strong> {email or h.to_email}</p>"
                        f"<p><strong>Zeit:</strong> {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')}</p>",
                    )
        except Exception as e:
            log.warning("Download notification email failed: %s", e)

    # Log detailed download info (anonymized IP - last 2 bytes only for privacy)
    client_ip = request.client.host if request else ""
    if client_ip and "." in client_ip:
        # Anonymize IPv4: x.x.x.x -> x.x.x.0
        parts = client_ip.rsplit(".", 1)
        if len(parts) == 2:
            client_ip = parts[0] + ".0"
    user_agent = request.headers.get("user-agent", "")[:200] if request else ""

    log = DownloadLog(
        history_id=h.id,
        ip_address=client_ip,
        user_agent=user_agent,
        email=email or h.to_email,
        filename=filename or h.filename,
    )
    db.add(log)
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

    # Extend expiry
    history.expiry_days = min(history.expiry_days + days, 90)  # Max 90 days
    await db.commit()

    return {"ok": True, "expiry_days": history.expiry_days}

    # Unrevoke the send
    history.is_revoked = False
    history.revoked_at = None
    history.revoked_by = None

    await db.commit()

    return {"ok": True, "message": "Link wurde wiederhergestellt"}


# ── Decryption Portal for Client-Side Encrypted Files ────────────────────────

_DECRYPT_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Datei entschlüsseln – SecureSend Cloud</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; margin: 0; padding: 2rem; }
    .container { max-width: 28rem; margin: 0 auto; }
    .card { background: #fff; border-radius: 0.75rem; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    h1 { font-size: 1.25rem; color: #1e293b; margin: 0 0 1rem 0; }
    .info { font-size: 0.875rem; color: #64748b; margin-bottom: 1rem; }
    .file-list { margin-top: 1rem; }
    .file-item { padding: 0.75rem; background: #f8fafc; border-radius: 0.5rem; margin-bottom: 0.5rem; }
    .file-name { font-weight: 600; color: #1e293b; }
    .password-hint { background: #fef3c7; border: 1px solid #fde68a; border-radius: 0.5rem; padding: 1rem; margin-top: 1rem; }
    .password-hint strong { color: #92400e; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>🔐 Ende-zu-Ende verschlüsselte Dateien</h1>
      <p class="info">Die folgenden Dateien wurden mit AES-256 Ende-zu-Ende verschlüsselt:</p>
      
      <div id="fileList" class="file-list">
        <div id="files"></div>
      </div>

      <div class="password-hint">
        <strong>📱 Passwort erforderlich</strong>
        <p style="margin:0.5rem 0 0 0;font-size:0.8125rem;color:#78350f;">
          Das Entschlüsselungspasswort wurde dem Sender per SMS gesendet.<br>
          Bitte kontaktieren Sie den Sender, um das Passwort zu erhalten.
        </p>
      </div>

      <p style="margin-top:1rem;font-size:0.8125rem;color:#94a3b8;">
        Oder klicken Sie auf den Original-Link, um die Dateien im Cloud-Provider anzuzeigen:
      </p>
      <a id="cloudLink" href="#" style="color:#1a56db;">Zum Cloud-Provider →</a>
    </div>
  </div>

  <script>
    const FILENAMES = {{ filenames_json }};
    const CLOUD_URL = "{{ cloud_url }}";

    const filesDiv = document.getElementById('files');
    filesDiv.innerHTML = FILENAMES.map(f => 
      '<div class="file-item"><span class="file-name">📄 ' + f + '</span></div>'
    ).join('');

    if (CLOUD_URL && CLOUD_URL !== 'None') {
      document.getElementById('cloudLink').href = CLOUD_URL;
      document.getElementById('cloudLink').style.display = 'inline';
    } else {
      document.getElementById('cloudLink').style.display = 'none';
    }
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

    # Check if this is an encrypted send (security_level in advanced/maximal)
    if h.security_level not in ("advanced", "maximal"):
        # Regular send - redirect to cloud share
        if h.share_url:
            return RedirectResponse(url=h.share_url, status_code=302)
        raise HTTPException(status_code=400, detail="Keine verschlüsselten Dateien")

    # Get encrypted files metadata from History
    enc_files = h.encrypted_files_json
    if not enc_files or not enc_files.get("files"):
        raise HTTPException(
            status_code=400, detail="Keine verschlüsselten Dateien gefunden"
        )

    # Get file names from the encrypted files data
    original_filenames = [f["filename"] for f in enc_files["files"]]

    return HTMLResponse(
        content=_DECRYPT_HTML.replace(
            "{{ filenames_json }}", str(original_filenames)
        ).replace("{{ cloud_url }}", h.share_url or "None"),
        headers={"Cache-Control": "no-store"},
    )

    # Get file names from the encrypted files data
    original_filenames = [f["filename"] for f in enc_files["files"]]

    # For the decryption page, we need to fetch the actual encrypted data from the cloud
    # TODO: This is a placeholder - in production, we'd fetch from the cloud storage
    # For now, show an error that says "contact sender" or implement cloud fetch

    return HTMLResponse(
        content=_DECRYPT_HTML.replace("{{ encrypted_files_json }}", "[]").replace(
            "{{ original_filenames_json }}", str(original_filenames)
        ),
        headers={"Cache-Control": "no-store"},
    )
