"""cloud/routers/tracking.py — Email open tracking + link-click redirect."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.shared import History

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
