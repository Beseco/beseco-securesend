"""
Beseco SecureSend – sicherer Datei- und Nachrichtenversand
Flask-App für lokalen Betrieb auf dem Arbeitsrechner.
Unterstützte Storage-Provider: OneDrive (Microsoft Graph) | Nextcloud (WebDAV + OCS)
"""

import os
import json
import uuid
import re
import socket
import mimetypes
import secrets
import string
import smtplib
import functools
import requests
import markdown as mdlib
import xml.etree.ElementTree as ET
import db
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash

# Projekt-Root zu sys.path hinzufügen, damit core.* importierbar ist
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import upload_and_share as _core_upload_and_share
from core.sms import send_sms_sipgate as _core_send_sms
from core.email import send_email as _core_send_email
from core.pdf import (
    md_to_pdf_bytes as _core_md_to_pdf_bytes,
    encrypt_pdf_bytes,
    _PDF_LIBS_AVAILABLE,
)
from core.vcf import parse_vcf as _parse_vcf, contacts_to_vcf as _contacts_to_vcf

CONFIG_FILE      = Path(__file__).parent / "config.json"
ADDRESSBOOK_FILE = Path(__file__).parent / "addressbook.json"
HISTORY_FILE     = Path(__file__).parent / "history.json"

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

# ── Adressbuch Hilfsfunktionen ───────────────────────────────────────────────

def _load_ab() -> list:
    """Liest addressbook.json – leere Liste wenn nicht vorhanden."""
    if ADDRESSBOOK_FILE.exists():
        try:
            with open(ADDRESSBOOK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _save_ab(data: list):
    """Schreibt addressbook.json."""
    with open(ADDRESSBOOK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ── History Hilfsfunktionen ──────────────────────────────────────────────────

def _load_history() -> list:
    """Liest history.json – leere Liste wenn nicht vorhanden."""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _append_history(entry: dict):
    """Fügt einen Eintrag am Anfang der history.json ein (neueste zuerst)."""
    history = _load_history()
    history.insert(0, entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

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
    global SEND_AS_PDF
    global SIGNATURE

    # OneDrive / Microsoft Graph
    AZURE_CLIENT_ID     = _get("AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET = _get("AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID     = _get("AZURE_TENANT_ID")
    ONEDRIVE_USER       = _get("ONEDRIVE_USER", "")
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
    SMTP_HOST           = _get("SMTP_HOST", "")
    SMTP_PORT           = int(_get("SMTP_PORT", "25"))
    SMTP_MODE           = _get("SMTP_MODE", "none")   # none | starttls | ssl
    SMTP_USER           = _get("SMTP_USER", "")
    SMTP_PASSWORD       = _get("SMTP_PASSWORD", "")
    MAIL_FROM           = _get("MAIL_FROM", "")
    MAIL_FROM_NAME      = _get("MAIL_FROM_NAME", "")

    # App-Sicherheit
    SETTINGS_PASSWORD   = _get("SETTINGS_PASSWORD", "")

    # Benachrichtigungen
    MAIL_NOTIFY         = _get("MAIL_NOTIFY", "")
    MAIL_NOTIFY_ENABLED = _get("MAIL_NOTIFY_ENABLED", "false").lower() in ("true", "1")

    # Sicherheit: Nachrichten als verschlüsselte PDF senden
    SEND_AS_PDF = _get("SEND_AS_PDF", "false").lower() in ("true", "1")

    # Signatur
    SIGNATURE = _get("SIGNATURE", "")

_apply_config()
db.init_db()
db.migrate_connections_from_config({
    "NC_URL": NC_URL, "NC_USER": NC_USER, "NC_PASSWORD": NC_PASSWORD, "NC_FOLDER": NC_FOLDER,
    "AZURE_CLIENT_ID": AZURE_CLIENT_ID, "AZURE_CLIENT_SECRET": AZURE_CLIENT_SECRET,
    "AZURE_TENANT_ID": AZURE_TENANT_ID, "ONEDRIVE_USER": ONEDRIVE_USER, "ONEDRIVE_FOLDER": ONEDRIVE_FOLDER,
    "SIPGATE_TOKEN_ID": SIPGATE_TOKEN_ID, "SIPGATE_TOKEN": SIPGATE_TOKEN, "SIPGATE_SMS_ID": SIPGATE_SMS_ID,
})

# ── Settings-Auth Decorator ──────────────────────────────────────────────────

def require_settings_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if SETTINGS_PASSWORD and not session.get("settings_auth"):
            # AJAX-Anfragen brauchen JSON, kein HTML-Redirect
            if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                return jsonify({"ok": False, "error": "Session abgelaufen – bitte Seite neu laden und anmelden."}), 401
            return redirect(url_for("settings_login"))
        return f(*args, **kwargs)
    return decorated

# ── Upload-Dispatcher (Wrapper um core.storage) ──────────────────────────────

def upload_and_share(provider: str, filename: str, content: bytes,
                     password: str, days: int,
                     content_type: str = "text/markdown; charset=utf-8",
                     subfolder: str = "") -> str:
    """Lädt Datei hoch und gibt passwortgeschützten Link zurück.
    provider kann ein DB-UUID (enthält '-'), 'nextcloud' oder 'onedrive' sein."""

    # UUID → cfg aus DB aufbauen
    if "-" in provider:
        db_prov = db.get_all_cloud_providers()
        match = next((p for p in db_prov if p["id"] == provider), None)
        if match:
            cfg = json.loads(match["config_json"]) if isinstance(match["config_json"], str) else match["config_json"]
            cfg = dict(cfg)
            cfg["service"] = match["service"]
            return _core_upload_and_share(cfg, filename, content, password, days,
                                          content_type=content_type, subfolder=subfolder)

    # Legacy string providers → cfg aus Globals
    if provider == "nextcloud":
        cfg = {"service": "nextcloud", "url": NC_URL, "user": NC_USER,
               "password": NC_PASSWORD, "folder": NC_FOLDER}
    else:  # onedrive
        cfg = {"service": "onedrive", "client_id": AZURE_CLIENT_ID,
               "client_secret": AZURE_CLIENT_SECRET, "tenant_id": AZURE_TENANT_ID,
               "user": ONEDRIVE_USER, "folder": ONEDRIVE_FOLDER}
    return _core_upload_and_share(cfg, filename, content, password, days,
                                  content_type=content_type, subfolder=subfolder)

# ── E-Mail senden (Wrapper um core.email) ────────────────────────────────────

def send_email(to_email: str, subject: str, body_html: str):
    """Sendet HTML-E-Mail via SMTP."""
    cfg = {
        "host": SMTP_HOST, "port": SMTP_PORT, "mode": SMTP_MODE,
        "user": SMTP_USER, "password": SMTP_PASSWORD,
        "from_addr": MAIL_FROM, "from_name": MAIL_FROM_NAME,
    }
    _core_send_email(cfg, to_email, subject, body_html)

# ── SMS via sipgate (Wrapper um core.sms) ────────────────────────────────────

def send_sms_sipgate_with_config(cfg: dict, to_number: str, message: str):
    """Sendet SMS über die sipgate REST API mit expliziter Konfiguration."""
    _core_send_sms(cfg, to_number, message)


def send_sms_sipgate(to_number: str, message: str):
    """Sendet SMS über die sipgate REST API (verwendet globale Vars als Fallback)."""
    _core_send_sms(
        {"token_id": SIPGATE_TOKEN_ID, "token": SIPGATE_TOKEN, "sms_id": SIPGATE_SMS_ID},
        to_number, message,
    )

# ── Passwort generieren ──────────────────────────────────────────────────────

def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))

# ── PDF-Erstellung (Wrapper um core.pdf) ─────────────────────────────────────

def md_to_pdf_bytes(md_text: str, title: str = "Sichere Nachricht") -> bytes:
    """Konvertiert Markdown-Text zu PDF (via core.pdf, mit Absender-Infos aus Globals)."""
    return _core_md_to_pdf_bytes(md_text, title=title,
                                 sender_name=MAIL_FROM_NAME, sender_email=MAIL_FROM)


# ── Flask Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/providers")
def api_providers():
    """Gibt eine Liste der konfigurierten Storage-Provider zurück (aus DB)."""
    providers = db.get_all_cloud_providers()
    return jsonify([{
        "id":         p["id"],
        "name":       p["name"],
        "icon":       "🟢" if p["service"] == "nextcloud" else "☁️",
        "service":    p["service"],
        "is_default": bool(p["is_default"]),
    } for p in providers if p["is_active"]])


@app.route("/api/status")
def api_status():
    """Schnell-Check aller konfigurierten Dienste (für Status-Bar in der UI)."""
    result = {}

    # ── Nextcloud (aus DB) ──
    nc_prov = next((p for p in db.get_all_cloud_providers()
                    if p["service"] == "nextcloud" and p["is_active"]), None)
    if nc_prov:
        cfg = json.loads(nc_prov["config_json"]) if isinstance(nc_prov["config_json"], str) else nc_prov["config_json"]
        nc_url = cfg.get("url", "")
        try:
            r = requests.get(f"{nc_url.rstrip('/')}/status.php", timeout=4)
            result["nextcloud"] = {"configured": True, "ok": r.status_code < 400,
                                   "label": f"Nextcloud ({nc_prov['name']})"}
        except Exception as exc:
            result["nextcloud"] = {"configured": True, "ok": False,
                                   "label": f"Nextcloud ({nc_prov['name']})", "error": str(exc)[:80]}
    else:
        result["nextcloud"] = {"configured": False, "ok": None, "label": "Nextcloud"}

    # ── OneDrive (aus DB, nur konfiguriert prüfen) ──
    od_prov = next((p for p in db.get_all_cloud_providers()
                    if p["service"] == "onedrive" and p["is_active"]), None)
    if od_prov:
        cfg = json.loads(od_prov["config_json"]) if isinstance(od_prov["config_json"], str) else od_prov["config_json"]
        od_configured = bool(cfg.get("client_id") and cfg.get("tenant_id"))
        result["onedrive"] = {"configured": od_configured, "ok": None,
                              "label": f"OneDrive ({od_prov['name']})"}
    else:
        result["onedrive"] = {"configured": False, "ok": None, "label": "OneDrive"}

    # ── SMTP (Socket-Connect-Test) ──
    if SMTP_HOST:
        try:
            s = socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=4)
            s.close()
            result["smtp"] = {"configured": True, "ok": True,
                              "label": f"SMTP ({SMTP_HOST})"}
        except Exception as exc:
            result["smtp"] = {"configured": True, "ok": False,
                              "label": f"SMTP ({SMTP_HOST})", "error": str(exc)[:80]}
    else:
        result["smtp"] = {"configured": False, "ok": None, "label": "SMTP"}

    # ── sipgate (aus DB, API-Verbindung testen) ──
    sg_gw = db.get_default_sms_gateway()
    if sg_gw:
        cfg = json.loads(sg_gw["config_json"]) if isinstance(sg_gw["config_json"], str) else sg_gw["config_json"]
        token_id = cfg.get("token_id", "")
        token    = cfg.get("token", "")
        if token_id and token:
            try:
                sg_resp = requests.get(
                    "https://api.sipgate.com/v2/account",
                    auth=(token_id, token),
                    timeout=5,
                )
                sg_api_ok = sg_resp.status_code == 200
                result["sipgate"] = {
                    "configured": True,
                    "ok": sg_api_ok,
                    "label": f"sipgate ({sg_gw['name']})",
                }
                if not sg_api_ok:
                    result["sipgate"]["error"] = f"HTTP {sg_resp.status_code}"
            except Exception as exc:
                result["sipgate"] = {
                    "configured": True,
                    "ok": False,
                    "label": f"sipgate ({sg_gw['name']})",
                    "error": str(exc)[:80],
                }
        else:
            result["sipgate"] = {"configured": False, "ok": None, "label": "sipgate"}
    else:
        result["sipgate"] = {"configured": False, "ok": None, "label": "sipgate"}

    # ── PDF-Bibliotheken (für Sicherheitsstufe "Erhöhte Sicherheit") ──
    result["pdf"] = {
        "configured": _PDF_LIBS_AVAILABLE,
        "ok": _PDF_LIBS_AVAILABLE,
        "label": "PDF (Erhöhte Sicherheit)",
    }

    return jsonify(result)


# ── Adressbuch Routes ────────────────────────────────────────────────────────

@app.route("/contacts")
def contacts():
    return render_template("contacts.html")


@app.route("/api/contacts", methods=["GET"])
def api_contacts_get():
    return jsonify(db.get_all_contacts())


@app.route("/api/contacts", methods=["POST"])
def api_contacts_post():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    entry = db.save_contact(data)
    return jsonify(entry), 201


@app.route("/api/contacts/<contact_id>", methods=["PUT"])
def api_contacts_put(contact_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    updated = db.update_contact(contact_id, data)
    if updated is None:
        return jsonify({"ok": False, "error": "Kontakt nicht gefunden."}), 404
    return jsonify(updated)


@app.route("/api/contacts/<contact_id>", methods=["DELETE"])
def api_contacts_delete(contact_id):
    if db.delete_contact(contact_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Kontakt nicht gefunden."}), 404




@app.route("/api/contacts/export.vcf")
def api_contacts_export_vcf():
    contacts = db.get_all_contacts()
    vcf_text = _contacts_to_vcf(contacts)
    return Response(
        vcf_text,
        mimetype="text/vcard; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=kontakte.vcf"},
    )


@app.route("/api/contacts/import", methods=["POST"])
def api_contacts_import():
    if "vcf_file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei empfangen."}), 400
    f = request.files["vcf_file"]
    raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    parsed = _parse_vcf(text)
    imported = skipped = 0
    for entry in parsed:
        _, created = db.upsert_contact(entry)
        if created:
            imported += 1
        else:
            skipped += 1

    return jsonify({
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "message": f"{imported} neue Kontakte importiert, {skipped} bereits vorhanden",
    })


@app.route("/api/contacts/sync-nextcloud", methods=["POST"])
def api_contacts_sync_nextcloud():
    import xml.etree.ElementTree as ET
    body = request.get_json(silent=True) or {}
    provider_id = body.get("provider_id")

    # Provider laden
    providers = db.get_all_cloud_providers()
    nc_cfg = None
    if provider_id:
        for p in providers:
            if p["id"] == provider_id and p["service"] == "nextcloud":
                nc_cfg = json.loads(p.get("config_json") or "{}")
                break
    else:
        for p in providers:
            if p["service"] == "nextcloud" and p.get("is_default"):
                nc_cfg = json.loads(p.get("config_json") or "{}")
                break
        if nc_cfg is None:
            for p in providers:
                if p["service"] == "nextcloud":
                    nc_cfg = json.loads(p.get("config_json") or "{}")
                    break

    if not nc_cfg:
        return jsonify({"ok": False, "error": "Kein Nextcloud-Provider konfiguriert."}), 400

    nc_url  = nc_cfg.get("url", "").rstrip("/")
    nc_user = nc_cfg.get("user", "")
    nc_pass = nc_cfg.get("password", "")
    if not nc_url or not nc_user:
        return jsonify({"ok": False, "error": "Nextcloud-Zugangsdaten unvollständig."}), 400

    # Interne User-ID per OCS ermitteln (LDAP-User haben oft UUID statt Login-Name)
    try:
        ocs_resp = requests.get(
            f"{nc_url}/ocs/v2.php/cloud/user",
            auth=(nc_user, nc_pass),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=10,
        )
        nc_user_id = ocs_resp.json()["ocs"]["data"]["id"]
    except Exception:
        nc_user_id = nc_user  # Fallback: Login-Name direkt verwenden

    carddav_url = f"{nc_url}/remote.php/dav/addressbooks/users/{nc_user_id}/contacts/"
    propfind_xml = '<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><getcontenttype/><getetag/></prop></propfind>'

    try:
        resp = requests.request(
            "PROPFIND", carddav_url,
            auth=(nc_user, nc_pass),
            headers={"Depth": "1", "Content-Type": "application/xml"},
            data=propfind_xml,
            timeout=20,
        )
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"Verbindungsfehler: {e}"}), 502

    if resp.status_code not in (207, 200):
        return jsonify({"ok": False, "error": f"CardDAV-Fehler: HTTP {resp.status_code}"}), 502

    # hrefs mit .vcf extrahieren
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        return jsonify({"ok": False, "error": f"XML-Fehler: {e}"}), 502

    ns = {"d": "DAV:"}
    hrefs = [
        el.text.strip()
        for el in root.findall(".//d:href", ns)
        if el.text and el.text.strip().endswith(".vcf")
    ]

    imported = skipped = errors = 0
    for href in hrefs:
        url = f"{nc_url}{href}" if href.startswith("/") else href
        try:
            vresp = requests.get(url, auth=(nc_user, nc_pass), timeout=10)
            if vresp.status_code != 200:
                errors += 1
                continue
            try:
                vtext = vresp.content.decode("utf-8")
            except UnicodeDecodeError:
                vtext = vresp.content.decode("latin-1", errors="replace")
            for entry in _parse_vcf(vtext):
                _, created = db.upsert_contact(entry)
                if created:
                    imported += 1
                else:
                    skipped += 1
        except requests.RequestException:
            errors += 1

    result = {"ok": True, "imported": imported, "skipped": skipped,
              "message": f"{imported} neue Kontakte importiert, {skipped} bereits vorhanden"}
    if errors:
        result["message"] += f", {errors} Fehler"
    return jsonify(result)


# ── History Routes ───────────────────────────────────────────────────────────

@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/api/history", methods=["GET"])
def api_history_get():
    return jsonify(db.get_all_history())


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_history_delete(entry_id):
    if db.delete_history_entry(entry_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Eintrag nicht gefunden."}), 404


# ── Send Route ───────────────────────────────────────────────────────────────

def _sanitize_subfolder(raw: str) -> str:
    """Bereinigt den Unterordner-Pfad und erlaubt / für verschachtelte Ordner."""
    # Erlaubte Zeichen: alphanumerisch, Leerzeichen, Bindestrich, Slash
    cleaned = re.sub(r"[^\w\s\-/]", "", raw).strip()
    # Mehrfache Slashes normalisieren, führende/abschließende entfernen
    cleaned = re.sub(r"/+", "/", cleaned).strip("/")
    return cleaned


@app.route("/send", methods=["POST"])
def send():
    """
    Einheitlicher Versand-Endpoint (immer multipart/form-data).
    Unterstützt drei Modi:
      • Nur Text  → Markdown als .md in Cloud hochladen
      • Nur Datei → Datei in Cloud hochladen
      • Text + Datei → Datei in Cloud hochladen, Markdown als Deckblatt in E-Mail
    """
    to_email       = request.form.get("to_email", "").strip()
    to_phone       = request.form.get("to_phone", "").strip()
    expiry_days    = int(request.form.get("expiry_days", 14))
    provider       = request.form.get("provider", "nextcloud")
    recipient_name = request.form.get("recipient_name", "").strip()
    subfolder_raw  = request.form.get("subfolder", "").strip()
    custom_msg     = request.form.get("custom_message", "").strip()
    md_content     = request.form.get("content", "").strip()
    filename_hint  = request.form.get("filename", "nachricht").strip()

    security_level = request.form.get("security_level", "sicher").strip()
    # normal   = Link ohne Passwort, keine SMS mit Code
    # sicher   = Passwortgeschützter Link + Code per SMS  (Standard)
    # erhoeht  = Verschlüsselte PDF + Code per SMS (erzwingt PDF)

    subfolder = _sanitize_subfolder(subfolder_raw)

    has_file    = ("file" in request.files and
                   bool(request.files["file"].filename))
    has_content = bool(md_content)

    if not to_email:
        return jsonify({"ok": False, "error": "E-Mail ist ein Pflichtfeld."}), 400
    if security_level != "normal" and not to_phone:
        return jsonify({"ok": False, "error": "Mobilnummer ist für den SMS-Code erforderlich."}), 400
    if not has_file and not has_content:
        return jsonify({"ok": False, "error": "Bitte Nachricht eingeben oder Datei auswählen."}), 400

    try:
        password = generate_password() if security_level != "normal" else ""

        # Standard-E-Mail-Vorlage aus DB laden (für Betreff)
        _email_tpl = db.get_default_email_template()
        _default_subject_file = (_email_tpl["subject"] if _email_tpl
                                 else "Sichere Datei von Beseco IT – *dateiname*")
        _default_subject_msg  = (_email_tpl["subject"] if _email_tpl
                                 else "Sichere Nachricht von Beseco IT – *dateiname*")

        if has_file:
            # ── Datei hochladen ──
            uploaded_file = request.files["file"]
            original_name = uploaded_file.filename or "datei"

            name_parts    = original_name.rsplit(".", 1)
            safe_base     = "".join(c for c in name_parts[0] if c.isalnum() or c in "-_")
            if not safe_base:
                safe_base = "datei"
            ext           = ("." + name_parts[1]) if len(name_parts) > 1 else ""
            ts            = datetime.now().strftime("%Y%m%d_%H%M%S")
            full_filename = f"{ts}_{safe_base}{ext}"
            file_bytes    = uploaded_file.read()

            guessed_type, _ = mimetypes.guess_type(original_name)
            content_type    = guessed_type or uploaded_file.mimetype or "application/octet-stream"

            share_url = upload_and_share(
                provider, full_filename, file_bytes, password, expiry_days,
                content_type=content_type, subfolder=subfolder
            )

            # Wenn Markdown-Text vorhanden → als Deckblatt in der E-Mail rendern
            if has_content:
                rendered_md = mdlib.markdown(
                    md_content, extensions=["tables", "fenced_code"]
                )
                # Persönliche Notiz (custom_msg) als Präambel, dann gerendertes Markdown
                combined = rendered_md
                if custom_msg:
                    combined = (
                        f"<p style='color:#374151;font-size:14px;'>"
                        f"<em>{custom_msg}</em></p>"
                        f"<hr style='border:none;border-top:1px solid #e5e7eb;margin:12px 0;'>"
                        + rendered_md
                    )
                email_body = _render_email_template(
                    share_url=share_url,
                    expiry_days=expiry_days,
                    custom_message=combined,
                    password=password,
                    recipient_name=recipient_name,
                    filename=full_filename,
                )
            else:
                email_body = _render_email_template(
                    share_url=share_url,
                    expiry_days=expiry_days,
                    custom_message=custom_msg,
                    password=password,
                    recipient_name=recipient_name,
                    filename=full_filename,
                )

            email_subject = _render_email_subject(_default_subject_file, filename=f"{safe_base}{ext}")
            send_email(to_email, email_subject, email_body)
            if password:
                sms_text = (
                    f"Beseco IT: Ihr Zugangscode für die sichere Datei lautet: {password}\n"
                    f"(Gültig {expiry_days} Tage)"
                )
            else:
                sms_text = f"Beseco IT: Ihre sichere Datei ist verfügbar. (Gültig {expiry_days} Tage)"

        else:
            # ── Nur Text → verschlüsselte PDF oder Markdown hochladen ──
            safe_name = "".join(c for c in filename_hint if c.isalnum() or c in "-_")
            if not safe_name:
                safe_name = "nachricht"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            use_pdf = (security_level == "erhoeht" and _PDF_LIBS_AVAILABLE)
            if use_pdf and password:
                full_filename = f"{ts}_{safe_name}.pdf"
                raw_pdf       = md_to_pdf_bytes(md_content, title=safe_name)
                upload_bytes  = encrypt_pdf_bytes(raw_pdf, password)
                ct            = "application/pdf"
            else:
                full_filename = f"{ts}_{safe_name}.md"
                upload_bytes  = md_content.encode("utf-8")
                ct            = "text/markdown; charset=utf-8"

            share_url = upload_and_share(
                provider, full_filename, upload_bytes, password, expiry_days,
                content_type=ct, subfolder=subfolder
            )

            email_body = _render_email_template(
                share_url=share_url,
                expiry_days=expiry_days,
                custom_message=custom_msg,
                password=password,
                recipient_name=recipient_name,
                filename=full_filename,
            )
            email_subject = _render_email_subject(_default_subject_msg, filename=safe_name)
            send_email(to_email, email_subject, email_body)
            if password:
                sms_text = (
                    f"Beseco IT: Ihr Zugangscode für die sichere Nachricht lautet: {password}\n"
                    f"(Gültig {expiry_days} Tage)"
                )
            else:
                sms_text = f"Beseco IT: Ihre sichere Nachricht ist verfügbar. (Gültig {expiry_days} Tage)"

        if security_level != "normal" and to_phone:
            default_gw = db.get_default_sms_gateway()
            if default_gw:
                gw_cfg = json.loads(default_gw["config_json"]) if isinstance(default_gw["config_json"], str) else default_gw["config_json"]
                send_sms_sipgate_with_config(gw_cfg, to_phone, sms_text)
            else:
                send_sms_sipgate(to_phone, sms_text)

        if MAIL_NOTIFY_ENABLED and MAIL_NOTIFY:
            try:
                notify_html = build_notify_email_html(
                    to_email, full_filename, provider, share_url, expiry_days
                )
                send_email(MAIL_NOTIFY, f"✅ SecureSend: Versand an {to_email}", notify_html)
            except Exception:
                pass

        db.append_history({
            "id":              str(uuid.uuid4()),
            "created_at":      datetime.now().isoformat(),
            "provider":        provider,
            "filename":        full_filename,
            "share_url":       share_url,
            "to_email":        to_email,
            "to_phone":        to_phone,
            "recipient_name":  recipient_name,
            "expiry_days":     expiry_days,
            "security_level":  security_level,
        })

        return jsonify({
            "ok":       True,
            "url":      share_url,
            "password": password,
            "filename": full_filename,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _render_email_template(share_url: str, expiry_days: int, custom_message: str,
                            password: str = "", recipient_name: str = "",
                            filename: str = "") -> str:
    """Lädt Standard-E-Mail-Vorlage aus DB und ersetzt Variablen.
    Fällt auf build_email_html() zurück wenn keine DB-Vorlage gefunden."""
    tpl = db.get_default_email_template()
    if not tpl:
        return build_email_html(share_url, expiry_days, custom_message)

    # Empfängername aufteilen
    name_parts = recipient_name.strip().split(" ", 1) if recipient_name else []
    vorname  = name_parts[0] if len(name_parts) > 0 else ""
    nachname = name_parts[1] if len(name_parts) > 1 else ""

    # custom_message Block bauen
    if custom_message:
        custom_block = (
            f"<div style=\"background:#f4f7fb;border-left:4px solid #1a56db;"
            f"padding:12px 18px;margin:20px 0;border-radius:4px;"
            f"color:#374151;font-size:15px;line-height:1.6;\">"
            f"{custom_message}</div>"
        )
    else:
        custom_block = ""

    datum     = datetime.now().strftime("%d.%m.%Y")
    signature = SIGNATURE or ""

    html = tpl["html_body"]
    html = html.replace("*vorname*",        vorname)
    html = html.replace("*nachname*",       nachname)
    html = html.replace("*firma*",          "")
    html = html.replace("*link*",           share_url)
    html = html.replace("*ablauf*",         str(expiry_days))
    html = html.replace("*passcode*",       password)
    html = html.replace("*datum*",          datum)
    html = html.replace("*signatur*",       signature)
    html = html.replace("*custom_message*", custom_block)
    html = html.replace("*dateiname*",      filename)
    return html


def _render_email_subject(tpl_subject: str, filename: str = "", safe_name: str = "") -> str:
    """Ersetzt Variablen im E-Mail-Betreff."""
    name = filename or safe_name
    subject = tpl_subject.replace("*dateiname*", name)
    subject = subject.replace("*datum*", datetime.now().strftime("%d.%m.%Y"))
    return subject


def build_email_html(share_url: str, expiry_days: int, custom_message: str) -> str:
    return render_template(
        "email_send.html",
        share_url=share_url,
        expiry_days=expiry_days,
        custom_message=custom_message,
        sender_name=MAIL_FROM_NAME or MAIL_FROM or "SecureSend",
    )


def build_notify_email_html(to_email: str, filename: str, provider: str, share_url: str, expiry_days: int) -> str:
    return render_template(
        "email_notify.html",
        to_email=to_email,
        filename=filename,
        provider_name="OneDrive" if provider == "onedrive" else "Nextcloud",
        share_url=share_url,
        expiry_days=expiry_days,
        timestamp=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        sender_name=MAIL_FROM_NAME or MAIL_FROM or "SecureSend",
    )


# ── Cloud Providers API ───────────────────────────────────────────────────────

@app.route("/api/cloud-providers", methods=["GET"])
@require_settings_auth
def api_cloud_providers_get():
    return jsonify(db.get_all_cloud_providers())


@app.route("/api/cloud-providers", methods=["POST"])
@require_settings_auth
def api_cloud_providers_post():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    entry = db.save_cloud_provider(data)
    return jsonify(entry), 201


@app.route("/api/cloud-providers/<provider_id>", methods=["PUT"])
@require_settings_auth
def api_cloud_providers_put(provider_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    updated = db.update_cloud_provider(provider_id, data)
    if updated is None:
        return jsonify({"ok": False, "error": "Provider nicht gefunden."}), 404
    return jsonify(updated)


@app.route("/api/cloud-providers/<provider_id>", methods=["DELETE"])
@require_settings_auth
def api_cloud_providers_delete(provider_id):
    if db.delete_cloud_provider(provider_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Provider nicht gefunden."}), 404


@app.route("/api/cloud-providers/<provider_id>/set-default", methods=["POST"])
@require_settings_auth
def api_cloud_providers_set_default(provider_id):
    db.set_default_cloud_provider(provider_id)
    return jsonify({"ok": True})


# ── SMS Gateways API ──────────────────────────────────────────────────────────

@app.route("/api/sms-gateways", methods=["GET"])
@require_settings_auth
def api_sms_gateways_get():
    return jsonify(db.get_all_sms_gateways())


@app.route("/api/sms-gateways", methods=["POST"])
@require_settings_auth
def api_sms_gateways_post():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    entry = db.save_sms_gateway(data)
    return jsonify(entry), 201


@app.route("/api/sms-gateways/<gateway_id>", methods=["PUT"])
@require_settings_auth
def api_sms_gateways_put(gateway_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    updated = db.update_sms_gateway(gateway_id, data)
    if updated is None:
        return jsonify({"ok": False, "error": "Gateway nicht gefunden."}), 404
    return jsonify(updated)


@app.route("/api/sms-gateways/<gateway_id>", methods=["DELETE"])
@require_settings_auth
def api_sms_gateways_delete(gateway_id):
    if db.delete_sms_gateway(gateway_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Gateway nicht gefunden."}), 404


@app.route("/api/sms-gateways/<gateway_id>/set-default", methods=["POST"])
@require_settings_auth
def api_sms_gateways_set_default(gateway_id):
    db.set_default_sms_gateway(gateway_id)
    return jsonify({"ok": True})


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
        "ONEDRIVE_USER":        v("ONEDRIVE_USER", ""),
        "ONEDRIVE_FOLDER":      v("ONEDRIVE_FOLDER", "SecureSend"),
        "NC_URL":               v("NC_URL"),
        "NC_USER":              v("NC_USER"),
        "NC_PASSWORD":          v("NC_PASSWORD"),
        "NC_FOLDER":            v("NC_FOLDER", "SecureSend"),
        "SIPGATE_TOKEN_ID":     v("SIPGATE_TOKEN_ID"),
        "SIPGATE_TOKEN":        v("SIPGATE_TOKEN"),
        "SIPGATE_SMS_ID":       v("SIPGATE_SMS_ID", "s0"),
        "SMTP_HOST":            v("SMTP_HOST", ""),
        "SMTP_PORT":            v("SMTP_PORT", "25"),
        "SMTP_MODE":            v("SMTP_MODE", "none"),
        "SMTP_USER":            v("SMTP_USER", ""),
        "SMTP_PASSWORD":        v("SMTP_PASSWORD", ""),
        "MAIL_FROM":            v("MAIL_FROM", ""),
        "MAIL_FROM_NAME":       v("MAIL_FROM_NAME", ""),
        "SECRET_KEY":           v("SECRET_KEY"),
        "FLASK_PORT":           v("FLASK_PORT", "5001"),
        "SETTINGS_PASSWORD_SET": bool(SETTINGS_PASSWORD),
        "MAIL_NOTIFY":          v("MAIL_NOTIFY"),
        "MAIL_NOTIFY_ENABLED":  v("MAIL_NOTIFY_ENABLED", "false"),
        "SEND_AS_PDF":          v("SEND_AS_PDF", "false"),
        "PDF_LIBS_AVAILABLE":   _PDF_LIBS_AVAILABLE,
        "SIGNATURE":            v("SIGNATURE", ""),
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


@app.route("/api/config/signature")
def api_signature():
    return jsonify({"signature": SIGNATURE})


# ── Vorlagen Routes ───────────────────────────────────────────────────────────

@app.route("/vorlagen")
@require_settings_auth
def vorlagen():
    return render_template("vorlagen.html")


@app.route("/api/templates", methods=["GET"])
def api_templates_get():
    return jsonify(db.get_all_msg_templates())


@app.route("/api/templates", methods=["POST"])
def api_templates_post():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    entry = db.save_msg_template(data)
    return jsonify(entry), 201


@app.route("/api/templates/<template_id>", methods=["PUT"])
def api_templates_put(template_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    updated = db.update_msg_template(template_id, data)
    if updated is None:
        return jsonify({"ok": False, "error": "Vorlage nicht gefunden."}), 404
    return jsonify(updated)


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def api_templates_delete(template_id):
    if db.delete_msg_template(template_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Vorlage nicht gefunden."}), 404


@app.route("/api/email-templates", methods=["GET"])
def api_email_templates_get():
    return jsonify(db.get_all_email_templates())


@app.route("/api/email-templates/default", methods=["GET"])
def api_email_templates_default():
    tpl = db.get_default_email_template()
    if tpl is None:
        return jsonify({"ok": False, "error": "Keine Standard-Vorlage gefunden."}), 404
    return jsonify(tpl)


@app.route("/api/email-templates", methods=["POST"])
def api_email_templates_post():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    entry = db.save_email_template(data)
    return jsonify(entry), 201


@app.route("/api/email-templates/<template_id>", methods=["PUT"])
def api_email_templates_put(template_id):
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "Keine Daten empfangen."}), 400
    updated = db.update_email_template(template_id, data)
    if updated is None:
        return jsonify({"ok": False, "error": "E-Mail-Vorlage nicht gefunden."}), 404
    return jsonify(updated)


@app.route("/api/email-templates/<template_id>", methods=["DELETE"])
def api_email_templates_delete(template_id):
    if db.delete_email_template(template_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "E-Mail-Vorlage nicht gefunden."}), 404


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
