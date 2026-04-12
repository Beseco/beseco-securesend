"""core/antivirus.py — ClamAV virus scanner integration."""
from __future__ import annotations

import io
import logging

log = logging.getLogger("securesend")


def sanitize_scanner_message(msg: str, max_len: int = 160) -> str:
    """Kurzer, einzeiliger Text für UI (ClamAV-Signatur)."""
    s = (msg or "").replace("\r", " ").replace("\n", " ").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def rejection_user_message(filename: str, scanner_msg: str) -> str:
    """Deutsche Fehlermeldung inkl. Signatur und Hinweis auf Fehlalarme."""
    sig = sanitize_scanner_message(scanner_msg)
    parts = [f"Datei '{filename}' wurde vom Virenscanner abgelehnt."]
    if sig and sig != "Virus gefunden":
        parts.append(f"Meldung: {sig}.")
    parts.append(
        "Bei Fotos und ähnlichen Dateien ist das oft ein Fehlalarm — "
        "ClamAV-Signaturen aktualisieren oder CLAMAV_ENABLED=false in der Konfiguration."
    )
    return " ".join(parts)


def scan_bytes(data: bytes, filename: str = "") -> tuple[bool, str]:
    """
    Scan file bytes with ClamAV.
    Returns (is_clean, message).
    Wenn CLAMAV_ENABLED=false, wird nicht gescannt (immer clean).
    Behaviour on ClamAV unavailability is controlled by CLAMAV_FAIL_OPEN setting.
    """
    from config import settings  # local import to avoid circular at module load

    if not getattr(settings, "CLAMAV_ENABLED", True):
        return True, "disabled"

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
