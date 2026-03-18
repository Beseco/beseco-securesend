"""core/antivirus.py — ClamAV virus scanner integration."""
from __future__ import annotations

import io
import logging

log = logging.getLogger("securesend")


def scan_bytes(data: bytes, filename: str = "") -> tuple[bool, str]:
    """
    Scan file bytes with ClamAV.
    Returns (is_clean, message).
    Behaviour on ClamAV unavailability is controlled by CLAMAV_FAIL_OPEN setting.
    """
    from config import settings  # local import to avoid circular at module load

    try:
        import clamd
        cd = clamd.ClamdNetworkSocket(
            host=settings.CLAMAV_HOST,
            port=settings.CLAMAV_PORT,
            timeout=15,
        )
        result = cd.instream(io.BytesIO(data))
        status, details = result["stream"]
        if status == "OK":
            return True, "clean"
        log.warning("ClamAV: virus found in %r: %s", filename, details)
        return False, details or "Virus gefunden"
    except Exception as exc:
        log.warning("ClamAV nicht erreichbar für %r: %s", filename, exc)
        if settings.CLAMAV_FAIL_OPEN:
            return True, "skipped"
        return False, "Virenscanner nicht verfügbar – Upload abgelehnt"
