"""
Beseco SecureSend – sicherer Datei- und Nachrichtenversand
Flask-App für lokalen Betrieb auf dem Arbeitsrechner.
Unterstützte Storage-Provider: OneDrive (Microsoft Graph) | Nextcloud (WebDAV + OCS)
"""

import os
import json
import secrets
import string
import smtplib
import functools
import requests
import msal
import xml.etree.ElementTree as ET
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from urllib.parse import quote

load_dotenv()

CONFIG_FILE = Path(__file__).parent / "config.json"

def _load_cfg() -> dict:
    """Liest config.json – leeres Dict wenn nicht vorhanden."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _get(key: str, default: str = "") -> str:
    """Wert aus config.json, dann Umgebungsvariable, dann Default."""
    return _load_cfg().get(key) or os.getenv(key) or default

app = Flask(__name__)
app.secret_key = _get("SECRET_KEY", "dev-secret-key")

# ── Konfiguration ────────────────────────────────────────────────────────────

def _apply_config():
    """Lädt alle Konfig-Werte in Modulvariablen (beim Start + nach Speichern)."""
    global AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
    global ONEDRIVE_USER, ONEDRIVE_FOLDER
    global NC_URL, NC_USER, NC_PASSWORD, NC_FOLDER
    global SIPGATE_TOKEN_ID, SIPGATE_TOKEN, SIPGATE_SMS_ID
    global SMTP_HOST, SMTP_PORT, SMTP_MODE, SMTP_USER, SMTP_PASSWORD, MAIL_FROM, MAIL_FROM_NAME
    global SETTINGS_PASSWORD
    global MAIL_NOTIFY, MAIL_NOTIFY_ENABLED

    # OneDrive / Microsoft Graph
    AZURE_CLIENT_ID     = _get("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET = _get("AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID     = _get("AZURE_TENANT_ID")
    ONEDRIVE_USER       = _get("ONEDRIVE_USER", "florian@beubl.de")
    ONEDRIVE_FOLDER     = _get("ONEDRIVE_FOLDER", "SecureSend")

    # Nextcloud
    NC_URL              = _get("NC_URL")
    NC_USER             = _get("NC_USER")
    NC_PASSWORD         = _get("NC_PASSWORD")
    NC_FOLDER           = _get("NC_FOLDER", "SecureSend")

    # sipgate
    SIPGATE_TOKEN_ID    = _get("SIPGATE_TOKEN_ID")
    SIPGATE_TOKEN       = _get("SIPGATE_TOKEN")
    SIPGATE_SMS_ID      = _get("SIPGATE_SMS_ID", "s0")

    # SMTP
    SMTP_HOST           = _get("SMTP_HOST", "192.168.10.14")
    SMTP_PORT           = int(_get("SMTP_PORT", "25"))
    SMTP_MODE           = _get("SMTP_MODE", "none")   # none | starttls | ssl
    SMTP_USER           = _get("SMTP_USER", "")
    SMTP_PASSWORD       = _get("SMTP_PASSWORD", "")
    MAIL_FROM           = _get("MAIL_FROM", "florian@beubl.de")
    MAIL_FROM_NAME      = _get("MAIL_FROM_NAME", "Florian Beubl – Beseco IT")

    # App-Sicherheit
    SETTINGS_PASSWORD   = _get("SETTINGS_PASSWORD", "")

    # Benachrichtigungen
    MAIL_NOTIFY         = _get("MAIL_NOTIFY", "")
    MAIL_NOTIFY_ENABLED = _get("MAIL_NOTIFY_ENABLED", "false").lower() in ("true", "1")

_apply_config()

# ── Settings-Auth Decorator ──────────────────────────────────────────────────

def require_settings_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if SETTINGS_PASSWORD and not session.get("settings_auth"):
            return redirect(url_for("settings_login"))
        return f(*args, **kwargs)
    return decorated

# ── Microsoft Graph Token ────────────────────────────────────────────────────

def get_graph_token() -> str:
    authority = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
    app_msal = msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID,
        authority=authority,
        client_credential=AZURE_CLIENT_SECRET,
    )
    result = app_msal.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"MSAL Token-Fehler: {result.get('error_description')}")
    return result["access_token"]

# ── OneDrive: Datei hochladen ────────────────────────────────────────────────

def upload_to_onedrive(token: str, filename: str, content: bytes) -> str:
    url = (
        f"https://graph.microsoft.com/v1.0/users/{ONEDRIVE_USER}"
        f"/drive/root:/{ONEDRIVE_FOLDER}/{filename}:/content"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/markdown; charset=utf-8",
    }
    resp = requests.put(url, headers=headers, data=content, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]

# ── OneDrive: Passwortgeschützter Freigabe-Link ──────────────────────────────

def create_onedrive_share_link(token: str, item_id: str, password: str, days: int) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://graph.microsoft.com/v1.0/users/{ONEDRIVE_USER}"
        f"/drive/items/{item_id}/createLink"
    )
    payload = {
        "type": "view",
        "scope": "anonymous",
        "password": password,
        "expirationDateTime": expiry,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["link"]["webUrl"]

# ── Nextcloud: Internen User-ID auflösen (kann UUID sein) ───────────────────

_nc_user_id_cache = None

def _nc_user_id() -> str:
    """Gibt die interne Nextcloud-User-ID zurück (per OCS-API, wird gecacht)."""
    global _nc_user_id_cache
    if _nc_user_id_cache:
        return _nc_user_id_cache
    try:
        resp = requests.get(
            f"{NC_URL.rstrip('/')}/ocs/v2.php/cloud/user",
            auth=(NC_USER, NC_PASSWORD),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        uid = resp.json()["ocs"]["data"]["id"]
        _nc_user_id_cache = uid
        return uid
    except Exception:
        # Fallback: NC_USER direkt verwenden
        return quote(NC_USER, safe="")

# ── Nextcloud: Ordner sicherstellen (WebDAV MKCOL) ───────────────────────────

def _nc_webdav_url(path: str) -> str:
    base = NC_URL.rstrip("/")
    return f"{base}/remote.php/dav/files/{_nc_user_id()}/{path.lstrip('/')}"

def _nc_auth():
    return (NC_USER, NC_PASSWORD)

def nc_ensure_folder():
    url = _nc_webdav_url(NC_FOLDER)
    resp = requests.request("MKCOL", url, auth=_nc_auth(), timeout=15)
    # 201 = erstellt, 405/409 = existiert schon → beides OK
    if resp.status_code not in (201, 405, 409):
        resp.raise_for_status()

# ── Nextcloud: Datei hochladen (WebDAV PUT) ──────────────────────────────────

def upload_to_nextcloud(filename: str, content: bytes) -> str:
    """Lädt Datei hoch, gibt den Datei-Pfad zurück."""
    nc_ensure_folder()
    path = f"{NC_FOLDER}/{filename}"
    url  = _nc_webdav_url(path)
    resp = requests.put(
        url,
        auth=_nc_auth(),
        data=content,
        headers={"Content-Type": "text/markdown; charset=utf-8"},
        timeout=30,
    )
    resp.raise_for_status()
    return path

# ── Nextcloud: Passwortgeschützter Freigabe-Link (OCS Share API) ─────────────

def create_nextcloud_share_link(file_path: str, password: str, days: int) -> str:
    """Erstellt Share via OCS API, gibt die öffentliche URL zurück."""
    base    = NC_URL.rstrip("/")
    api_url = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"

    expiry_str = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    resp = requests.post(
        api_url,
        auth=_nc_auth(),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        data={
            "path":        f"/{file_path}",
            "shareType":   3,           # 3 = öffentlicher Link
            "permissions": 1,           # 1 = nur lesen
            "password":    password,
            "expireDate":  expiry_str,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["ocs"]["data"]["url"]
    except (KeyError, TypeError):
        raise RuntimeError(f"Nextcloud Share-URL nicht gefunden: {data}")

# ── Einheitlicher Upload-Dispatcher ─────────────────────────────────────────

def upload_and_share(provider: str, filename: str, content: bytes,
                     password: str, days: int) -> str:
    """Lädt Datei hoch und gibt passwortgeschützten Link zurück."""
    if provider == "nextcloud":
        file_path = upload_to_nextcloud(filename, content)
        return create_nextcloud_share_link(file_path, password, days)
    else:  # onedrive
        token   = get_graph_token()
        item_id = upload_to_onedrive(token, filename, content)
        return create_onedrive_share_link(token, item_id, password, days)

# ── E-Mail senden ────────────────────────────────────────────────────────────

def _smtp_connect():
    """Öffnet SMTP-Verbindung je nach SMTP_MODE (none/starttls/ssl)."""
    if SMTP_MODE == "ssl":
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        if SMTP_MODE == "starttls":
            s.starttls()
    if SMTP_USER and SMTP_PASSWORD:
        s.login(SMTP_USER, SMTP_PASSWORD)
    return s

def send_email(to_email: str, subject: str, body_html: str):
    """Sendet HTML-E-Mail via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with _smtp_connect() as s:
        s.sendmail(MAIL_FROM, [to_email], msg.as_string())

# ── SMS via sipgate ──────────────────────────────────────────────────────────

def send_sms_sipgate(to_number: str, message: str):
    """Sendet SMS über die sipgate REST API."""
    # Nummer normalisieren → E.164 ohne +
    number = to_number.strip().replace(" ", "").replace("-", "")
    if number.startswith("0"):
        number = "49" + number[1:]
    elif number.startswith("+"):
        number = number[1:]

    resp = requests.post(
        "https://api.sipgate.com/v2/sessions/sms",
        auth=(SIPGATE_TOKEN_ID, SIPGATE_TOKEN),
        json={
            "smsId":     SIPGATE_SMS_ID,
            "recipient": number,
            "message":   message,
        },
        timeout=15,
    )
    resp.raise_for_status()

# ── Passwort generieren ──────────────────────────────────────────────────────

def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))

# ── Flask Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/providers")
def api_providers():
    """Gibt eine Liste der konfigurierten Storage-Provider zurück."""
    providers = []
    # Nextcloud: konfiguriert wenn NC_URL, NC_USER, NC_PASSWORD alle non-empty
    if NC_URL and NC_USER and NC_PASSWORD:
        providers.append({"id": "nextcloud", "name": "Nextcloud", "icon": "🟢"})
    # OneDrive: konfiguriert wenn Azure-Credentials gesetzt und kein Placeholder
    def _is_placeholder(val: str) -> bool:
        return val.upper().startswith("DEIN")
    if (AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_TENANT_ID
            and not _is_placeholder(AZURE_CLIENT_ID)
            and not _is_placeholder(AZURE_CLIENT_SECRET)
            and not _is_placeholder(AZURE_TENANT_ID)):
        providers.append({"id": "onedrive", "name": "OneDrive", "icon": "☁️"})
    return jsonify(providers)


@app.route("/send", methods=["POST"])
def send():
    data        = request.get_json()
    md_content  = data.get("content", "").strip()
    filename    = data.get("filename", "nachricht").strip()
    to_email    = data.get("email", "").strip()
    to_phone    = data.get("phone", "").strip()
    expiry_days = int(data.get("expiry_days", 14))
    custom_msg  = data.get("custom_message", "").strip()

    if not md_content or not to_email or not to_phone:
        return jsonify({"ok": False, "error": "E-Mail, Telefon und Inhalt sind Pflichtfelder."}), 400

    # Dateiname sichern
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_")
    if not safe_name:
        safe_name = "nachricht"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_filename = f"{ts}_{safe_name}.md"

    provider = data.get("provider", "onedrive")

    try:
        # 1+2. Upload + passwortgeschützte Freigabe (provider-unabhängig)
        password  = generate_password()
        share_url = upload_and_share(
            provider, full_filename, md_content.encode("utf-8"), password, expiry_days
        )

        # 3. E-Mail an Kunden
        email_body = build_email_html(
            share_url=share_url,
            expiry_days=expiry_days,
            custom_message=custom_msg,
        )
        send_email(to_email, f"Sichere Nachricht von Beseco IT – {safe_name}", email_body)

        # 4. SMS mit Passwort
        sms_text = (
            f"Beseco IT: Ihr Zugangscode für die sichere Nachricht lautet: {password}\n"
            f"(Gültig {expiry_days} Tage)"
        )
        send_sms_sipgate(to_phone, sms_text)

        # 5. Sender-Benachrichtigung (optional)
        if MAIL_NOTIFY_ENABLED and MAIL_NOTIFY:
            try:
                notify_html = build_notify_email_html(to_email, full_filename, provider, share_url, expiry_days)
                send_email(MAIL_NOTIFY, f"✅ SecureSend: Versand an {to_email}", notify_html)
            except Exception:
                pass

        return jsonify({
            "ok":       True,
            "url":      share_url,
            "password": password,
            "filename": full_filename,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def build_email_html(share_url: str, expiry_days: int, custom_message: str) -> str:
    custom_block = ""
    if custom_message:
        custom_block = f"""
        <div style="background:#f4f7fb;border-left:4px solid #1a56db;
                    padding:12px 18px;margin:20px 0;border-radius:4px;
                    color:#374151;font-size:15px;line-height:1.6;">
          {custom_message}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px;">
      <table width="580" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;
                    box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:#1a56db;padding:28px 36px;">
            <p style="margin:0;color:#ffffff;font-size:13px;
                      letter-spacing:2px;text-transform:uppercase;
                      font-weight:600;">Beseco IT Systems · Freising</p>
            <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;
                       font-weight:700;">🔒 Sichere Nachricht für Sie</h1>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:32px 36px;">
            <p style="margin:0 0 16px;color:#374151;font-size:15px;line-height:1.7;">
              Guten Tag,<br><br>
              ich habe Ihnen eine vertrauliche Nachricht bzw. Datei bereitgestellt.
              Sie können diese über den folgenden Link abrufen:
            </p>
            {custom_block}
            <div style="text-align:center;margin:28px 0;">
              <a href="{share_url}"
                 style="background:#1a56db;color:#ffffff;text-decoration:none;
                        padding:14px 32px;border-radius:8px;font-size:15px;
                        font-weight:600;display:inline-block;
                        letter-spacing:.3px;">
                Nachricht öffnen →
              </a>
            </div>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#fef9c3;border-radius:8px;margin:20px 0;">
              <tr>
                <td style="padding:14px 18px;color:#713f12;font-size:14px;line-height:1.6;">
                  <strong>🔑 Passwort:</strong>
                  Sie erhalten das Passwort zum Öffnen dieser Nachricht
                  per SMS auf Ihre hinterlegte Mobilnummer.<br>
                  <strong>⏱ Gültigkeit:</strong> {expiry_days} Tage
                </td>
              </tr>
            </table>
            <p style="margin:20px 0 0;color:#6b7280;font-size:13px;line-height:1.6;">
              Sollten Sie Fragen haben, antworten Sie einfach auf diese E-Mail
              oder rufen Sie mich an.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:18px 36px;
                     border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              Florian Beubl · Beseco IT Systems · Freising<br>
              florian@beubl.de
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def build_notify_email_html(to_email: str, filename: str, provider: str, share_url: str, expiry_days: int) -> str:
    provider_name = "OneDrive" if provider == "onedrive" else "Nextcloud"
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px;">
      <table width="580" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:12px;
                    box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:#16a34a;padding:28px 36px;">
            <p style="margin:0;color:#ffffff;font-size:13px;
                      letter-spacing:2px;text-transform:uppercase;
                      font-weight:600;">Beseco IT Systems · SecureSend</p>
            <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;
                       font-weight:700;">✅ Nachricht erfolgreich versendet</h1>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:32px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border-collapse:collapse;font-size:14px;">
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#6b7280;width:140px;font-weight:600;">Empfänger</td>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#111827;">{to_email}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#6b7280;font-weight:600;">Datei</td>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#111827;font-family:monospace;font-size:13px;">{filename}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#6b7280;font-weight:600;">Provider</td>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#111827;">{provider_name}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#6b7280;font-weight:600;">Gültigkeit</td>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#111827;">{expiry_days} Tage</td>
              </tr>
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#6b7280;font-weight:600;">Zeitstempel</td>
                <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;
                           color:#111827;">{timestamp}</td>
              </tr>
              <tr>
                <td style="padding:10px 0;color:#6b7280;font-weight:600;">Link</td>
                <td style="padding:10px 0;">
                  <a href="{share_url}" style="color:#1a56db;word-break:break-all;">{share_url}</a>
                </td>
              </tr>
            </table>
            <p style="margin:20px 0 0;color:#6b7280;font-size:12px;line-height:1.6;">
              Diese Benachrichtigung enthält keinen PIN und keine Nachrichteninhalte.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:18px 36px;
                     border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">
              Florian Beubl · Beseco IT Systems · Freising<br>
              florian@beubl.de
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


@app.route("/settings")
@require_settings_auth
def settings():
    cfg = _load_cfg()
    def v(key, default=""):
        return cfg.get(key) or os.getenv(key, default) or default
    return render_template("settings.html", cfg={
        "AZURE_CLIENT_ID":      v("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET":  v("AZURE_CLIENT_SECRET"),
        "AZURE_TENANT_ID":      v("AZURE_TENANT_ID"),
        "ONEDRIVE_USER":        v("ONEDRIVE_USER", "florian@beubl.de"),
        "ONEDRIVE_FOLDER":      v("ONEDRIVE_FOLDER", "SecureSend"),
        "NC_URL":               v("NC_URL"),
        "NC_USER":              v("NC_USER"),
        "NC_PASSWORD":          v("NC_PASSWORD"),
        "NC_FOLDER":            v("NC_FOLDER", "SecureSend"),
        "SIPGATE_TOKEN_ID":     v("SIPGATE_TOKEN_ID"),
        "SIPGATE_TOKEN":        v("SIPGATE_TOKEN"),
        "SIPGATE_SMS_ID":       v("SIPGATE_SMS_ID", "s0"),
        "SMTP_HOST":            v("SMTP_HOST", "192.168.10.14"),
        "SMTP_PORT":            v("SMTP_PORT", "25"),
        "SMTP_MODE":            v("SMTP_MODE", "none"),
        "SMTP_USER":            v("SMTP_USER", ""),
        "SMTP_PASSWORD":        v("SMTP_PASSWORD", ""),
        "MAIL_FROM":            v("MAIL_FROM", "florian@beubl.de"),
        "MAIL_FROM_NAME":       v("MAIL_FROM_NAME", "Florian Beubl – Beseco IT"),
        "SECRET_KEY":           v("SECRET_KEY"),
        "FLASK_PORT":           v("FLASK_PORT", "5001"),
        "SETTINGS_PASSWORD_SET": bool(SETTINGS_PASSWORD),
        "MAIL_NOTIFY":          v("MAIL_NOTIFY"),
        "MAIL_NOTIFY_ENABLED":  v("MAIL_NOTIFY_ENABLED", "false"),
    })


@app.route("/settings/login")
def settings_login():
    if not SETTINGS_PASSWORD or session.get("settings_auth"):
        return redirect(url_for("settings"))
    return render_template("settings_login.html", error=None)


@app.route("/settings/auth", methods=["POST"])
def settings_auth():
    password = request.form.get("password", "")
    if SETTINGS_PASSWORD and check_password_hash(SETTINGS_PASSWORD, password):
        session["settings_auth"] = True
        return redirect(url_for("settings"))
    return render_template("settings_login.html", error="Falsches Passwort. Bitte erneut versuchen.")


@app.route("/settings/logout")
def settings_logout():
    session.pop("settings_auth", None)
    return redirect(url_for("settings_login"))


@app.route("/settings/save", methods=["POST"])
@require_settings_auth
def settings_save():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400

    # Passwort-Handling: aus data herausnehmen, nicht direkt speichern
    pw_new     = data.pop("SETTINGS_PASSWORD_NEW", "").strip()
    pw_confirm = data.pop("SETTINGS_PASSWORD_CONFIRM", "").strip()

    if pw_new:
        if pw_new != pw_confirm:
            return jsonify({"ok": False, "error": "Passwörter stimmen nicht überein."}), 400
        data["SETTINGS_PASSWORD"] = generate_password_hash(pw_new)
    else:
        # Bestehendes Passwort aus config.json übernehmen (nicht überschreiben)
        existing = _load_cfg().get("SETTINGS_PASSWORD", "")
        if existing:
            data["SETTINGS_PASSWORD"] = existing

    # Port als Integer validieren
    try:
        int(data.get("SMTP_PORT", "25"))
        int(data.get("FLASK_PORT", "5001"))
    except ValueError:
        return jsonify({"ok": False, "error": "SMTP_PORT und FLASK_PORT müssen Zahlen sein."}), 400

    # In config.json speichern
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Sofort in Modulvariablen übernehmen (kein Neustart nötig)
    _apply_config()
    # User-ID-Cache leeren (neuer NC_USER möglich)
    global _nc_user_id_cache
    _nc_user_id_cache = None

    return jsonify({"ok": True})


@app.route("/settings/test-smtp", methods=["POST"])
def test_smtp():
    """Testet SMTP-Verbindung mit den übermittelten Einstellungen."""
    data = request.get_json() or {}

    host     = data.get("host", "").strip()
    port     = int(data.get("port", 25))
    mode     = data.get("mode", "none")   # none | starttls | ssl
    user     = data.get("user", "").strip()
    password = data.get("password", "").strip()
    mail_from = data.get("mail_from", "").strip()

    if not host:
        return jsonify({"ok": False, "error": "Kein SMTP-Host angegeben."})

    try:
        if mode == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            s = smtplib.SMTP(host, port, timeout=10)
            if mode == "starttls":
                s.starttls()
        if user and password:
            s.login(user, password)
        s.noop()
        s.quit()
        return jsonify({"ok": True, "message": f"Verbindung zu {host}:{port} erfolgreich!"})
    except smtplib.SMTPAuthenticationError as e:
        return jsonify({"ok": False, "error": f"Authentifizierung fehlgeschlagen: {e.smtp_error.decode()}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    port = int(_get("FLASK_PORT", "5001"))
    print(f"\n✅  Beseco SecureSend läuft auf http://localhost:{port}\n")
    app.run(debug=False, port=port)
