"""
core/smtp_config.py — Shared SMTP config helpers.
"""

from __future__ import annotations

import os
from typing import Optional


def get_env_smtp_cfg() -> Optional[dict]:
    """Build SMTP config from environment variables.

    Returns None when SMTP_HOST is not set.
    """
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return None

    port_raw = os.getenv("SMTP_PORT", "587").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 587

    mode = os.getenv("SMTP_MODE", "starttls").strip().lower() or "starttls"
    if mode not in {"none", "starttls", "ssl"}:
        mode = "starttls"

    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_addr = os.getenv("MAIL_FROM", "").strip() or user
    from_name = os.getenv("MAIL_FROM_NAME", "").strip()

    return {
        "host": host,
        "port": port,
        "mode": mode,
        "user": user,
        "password": password,
        "from_addr": from_addr,
        "from_name": from_name,
    }
