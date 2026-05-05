"""
SMTP-Konfiguration: Normalisierung und Fallback-Kette (Org → Reseller → Umgebung).
"""

from __future__ import annotations

from typing import Any, Optional

from core.smtp_config import get_env_smtp_cfg


def _as_dict(cfg: Any) -> Optional[dict]:
    if cfg is None:
        return None
    if isinstance(cfg, dict):
        return cfg
    return None


def normalize_smtp(cfg: dict) -> dict:
    """Port/Mode/From-Adresse vereinheitlichen (ohne Secrets zu loggen)."""
    out = dict(cfg)
    port_raw = out.get("port", 587)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 587
    out["port"] = max(1, min(65535, port))
    mode = str(out.get("mode") or "starttls").strip().lower() or "starttls"
    if mode not in ("none", "starttls", "ssl"):
        mode = "starttls"
    out["mode"] = mode
    user = str(out.get("user") or "").strip()
    from_addr = str(out.get("from_addr") or "").strip() or user
    out["from_addr"] = from_addr
    out["host"] = str(out.get("host") or "").strip()
    return out


def is_usable_smtp(cfg: Optional[dict]) -> bool:
    """Mindestanforderungen für send_email (core/email.py)."""
    if not cfg or not isinstance(cfg, dict):
        return False
    n = normalize_smtp(cfg)
    if not n["host"]:
        return False
    if not n["from_addr"]:
        return False
    return True


def resolve_smtp_with_fallback(
    org_settings: dict, reseller_settings_json: Optional[dict]
) -> tuple[Optional[dict], str]:
    """
    Liefert (smtp_dict, quelle) mit quelle in org|reseller|env|none.
    Überspringt unvollständige Konfigurationen in der Kette.
    """
    candidates: list[tuple[dict, str]] = []

    use_own = org_settings.get("use_own_smtp")
    if use_own is not False:
        org_smtp = _as_dict(org_settings.get("smtp"))
        if org_smtp and (org_smtp.get("host") or "").strip():
            if use_own is True or use_own is None:
                candidates.append((org_smtp, "org"))

    r_smtp = _as_dict((reseller_settings_json or {}).get("smtp"))
    if r_smtp and (r_smtp.get("host") or "").strip():
        candidates.append((r_smtp, "reseller"))

    env_cfg = get_env_smtp_cfg()
    if env_cfg and (env_cfg.get("host") or "").strip():
        candidates.append((env_cfg, "env"))

    for raw, label in candidates:
        if is_usable_smtp(raw):
            return normalize_smtp(raw), label
    return None, "none"
