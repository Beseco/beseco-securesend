"""
core/sms.py — SMS-Versand via sipgate

Alle Funktionen erhalten die Konfiguration als `cfg: dict`.
Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import requests


def send_sms_sipgate(cfg: dict, to_number: str, message: str):
    """Sendet SMS über die sipgate REST API.

    cfg muss enthalten:
      - token_id: sipgate Token-ID (z.B. "token-XXXXX")
      - token:    sipgate API-Token
      - sms_id:   SMS-Gerät (z.B. "s0")
    """
    # Unsichtbare Unicode-Zeichen entfernen, nur Ziffern und + behalten
    cleaned = "".join(c for c in to_number if c.isdigit() or c == "+").strip()
    if cleaned.startswith("00"):
        number = "+" + cleaned[2:]
    elif cleaned.startswith("0"):
        number = "+49" + cleaned[1:]
    elif cleaned.startswith("+"):
        number = cleaned
    else:
        number = "+" + cleaned

    resp = requests.post(
        "https://api.sipgate.com/v2/sessions/sms",
        auth=(cfg.get("token_id", ""), cfg.get("token", "")),
        json={
            "smsId":     cfg.get("sms_id", "s0"),
            "recipient": number,
            "message":   message,
        },
        timeout=15,
    )
    resp.raise_for_status()
