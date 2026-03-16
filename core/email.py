"""
core/email.py — E-Mail-Versand via SMTP

Alle Funktionen erhalten die Konfiguration als `cfg: dict`.
Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr


def _smtp_connect(cfg: dict) -> smtplib.SMTP:
    """Öffnet SMTP-Verbindung je nach smtp_mode (none/starttls/ssl).

    cfg muss enthalten:
      - host:       SMTP-Hostname
      - port:       SMTP-Port (int)
      - mode:       "none" | "starttls" | "ssl"
      - user:       SMTP-Benutzername (optional)
      - password:   SMTP-Passwort (optional)
    """
    mode = cfg.get("mode", "starttls")
    if mode == "ssl":
        s = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=15)
    else:
        s = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=15)
        if mode == "starttls":
            s.starttls()
    if cfg.get("user") and cfg.get("password"):
        s.login(cfg["user"], cfg["password"])
    return s


def send_email(cfg: dict, to_email: str, subject: str, body_html: str):
    """Sendet eine HTML-E-Mail via SMTP.

    cfg muss enthalten (zusätzlich zu _smtp_connect-Keys):
      - from_addr:  Absender-E-Mail-Adresse
      - from_name:  Absender-Anzeigename (optional)
    """
    from_addr = cfg.get("from_addr", cfg.get("user", ""))
    from_name = cfg.get("from_name", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), from_addr))
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with _smtp_connect(cfg) as s:
        s.sendmail(from_addr, [to_email], msg.as_string())
