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


def get_sipgate_status(cfg: dict) -> dict:
    """Liefert Kontostand, Geräte-Status und SMS-Verlauf (EVN).

    Rückgabe:
      {
        "ok": bool,
        "balance": {"amount": float, "currency": str} | None,
        "device_ok": bool,
        "history": [ {id, direction, created, recipient, status, price} ],
        "error": str | None,
      }
    """
    auth = (cfg.get("token_id", ""), cfg.get("token", ""))
    sms_id = cfg.get("sms_id", "s0")
    result: dict = {"ok": False, "balance": None, "history": [], "error": None}

    # 1. Kontostand
    try:
        r = requests.get("https://api.sipgate.com/v2/balance", auth=auth, timeout=10)
        if r.status_code == 401:
            result["error"] = "Ungültige Zugangsdaten (401 Unauthorized)"
            return result
        r.raise_for_status()
        data = r.json()
        result["balance"] = {"amount": data.get("amount", 0) / 10000, "currency": data.get("currency", "EUR")}
    except requests.RequestException as e:
        result["error"] = f"Verbindungsfehler: {e}"
        return result

    # 2. SMS-Verlauf (EVN) — letzte 20 Einträge
    try:
        r = requests.get(
            "https://api.sipgate.com/v2/history",
            auth=auth,
            params={"types": "SMS", "limit": 20, "offset": 0},
            timeout=10,
        )
        if r.ok:
            items = r.json().get("items", [])
            history = []
            for item in items:
                # Preis: sipgate liefert entweder "price" (int, BU) oder "cost.amount" (EUR-Cents)
                raw_price = item.get("price")
                if raw_price is None:
                    cost = item.get("cost")
                    if isinstance(cost, dict):
                        raw_price = cost.get("amount")
                price_eur = raw_price / 10000 if raw_price is not None else None

                history.append({
                    "id":        item.get("id", ""),
                    "direction": item.get("direction", ""),
                    "created":   item.get("created", ""),
                    "recipient": (
                        item["target"].get("number", "")
                        if isinstance(item.get("target"), dict)
                        else item.get("target") or item.get("calleeNumber", "")
                    ),
                    "status":    item.get("status", ""),
                    "price":     price_eur,
                })
            result["history"] = history
    except requests.RequestException:
        pass  # EVN ist optional

    result["ok"] = True
    return result
