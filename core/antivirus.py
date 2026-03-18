"""core/antivirus.py — ClamAV virus scanner integration."""
from __future__ import annotations

import io
import logging

log = logging.getLogger("securesend")


def scan_bytes(data: bytes, filename: str = "") -> tuple[bool, str]:
    """
    Scan file bytes with ClamAV.
    Returns (is_clean, message).
    If ClamAV is unavailable, returns (True, "skipped") — fail open with warning.
    """
    try:
        import clamd
        cd = clamd.ClamdNetworkSocket(host="clamav", port=3310, timeout=15)
        result = cd.instream(io.BytesIO(data))
        status, details = result["stream"]
        if status == "OK":
            return True, "clean"
        else:
            log.warning("ClamAV: virus found in %r: %s", filename, details)
            return False, details or "Virus gefunden"
    except Exception as exc:
        log.warning("ClamAV nicht erreichbar – Scan übersprungen für %r: %s", filename, exc)
        return True, "skipped"
