"""
db.py – SQLite-Datenbankschicht für Beseco SecureSend
Verwaltet Kontakte, History, Nachrichten-Vorlagen und E-Mail-Vorlagen.
"""

import sqlite3
import uuid
import json
from pathlib import Path
from datetime import datetime

DB_PATH          = Path(__file__).parent / "data.db"
ADDRESSBOOK_FILE = Path(__file__).parent / "addressbook.json"
HISTORY_FILE     = Path(__file__).parent / "history.json"

# ── Verbindung ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema & Init ─────────────────────────────────────────────────────────────

def init_db():
    """Erstellt alle Tabellen, führt Migrationen durch und befüllt Defaults."""
    conn = get_db()
    try:
        c = conn.cursor()

        # ── contacts ──────────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id         TEXT PRIMARY KEY,
                company    TEXT DEFAULT '',
                last_name  TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                mobile     TEXT DEFAULT '',
                email      TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
        """)

        # ── history ───────────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id             TEXT PRIMARY KEY,
                to_email       TEXT DEFAULT '',
                to_phone       TEXT DEFAULT '',
                recipient_name TEXT DEFAULT '',
                filename       TEXT DEFAULT '',
                provider       TEXT DEFAULT '',
                share_url      TEXT DEFAULT '',
                expiry_days    INTEGER DEFAULT 14,
                security_level TEXT DEFAULT 'sicher',
                created_at     TEXT DEFAULT ''
            )
        """)

        # ── msg_templates ─────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS msg_templates (
                id         TEXT PRIMARY KEY,
                name       TEXT DEFAULT '',
                title      TEXT DEFAULT '',
                content    TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
        """)

        # ── email_templates ───────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS email_templates (
                id         TEXT PRIMARY KEY,
                name       TEXT DEFAULT '',
                subject    TEXT DEFAULT '',
                html_body  TEXT DEFAULT '',
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
        """)

        # ── cloud_providers ───────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS cloud_providers (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                service     TEXT NOT NULL DEFAULT 'nextcloud',
                is_default  INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                config_json TEXT DEFAULT '{}',
                created_at  TEXT DEFAULT ''
            )
        """)

        # ── sms_gateways ──────────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS sms_gateways (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                service     TEXT NOT NULL DEFAULT 'sipgate',
                is_default  INTEGER DEFAULT 0,
                is_active   INTEGER DEFAULT 1,
                config_json TEXT DEFAULT '{}',
                created_at  TEXT DEFAULT ''
            )
        """)

        conn.commit()

        # ── Migration: addressbook.json → contacts (einmalig) ────────────────
        count = c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        if count == 0 and ADDRESSBOOK_FILE.exists():
            try:
                with open(ADDRESSBOOK_FILE, "r", encoding="utf-8") as f:
                    ab = json.load(f)
                for entry in ab:
                    c.execute("""
                        INSERT OR IGNORE INTO contacts
                            (id, company, last_name, first_name, mobile, email, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.get("id", str(uuid.uuid4())),
                        entry.get("company", ""),
                        entry.get("last_name", ""),
                        entry.get("first_name", ""),
                        entry.get("mobile", ""),
                        entry.get("email", ""),
                        entry.get("created_at", datetime.now().isoformat()),
                    ))
                conn.commit()
            except Exception:
                pass

        # ── Migration: history.json → history (einmalig) ─────────────────────
        hcount = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if hcount == 0 and HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                for entry in hist:
                    # Legacy-Feld-Namen abbilden
                    c.execute("""
                        INSERT OR IGNORE INTO history
                            (id, to_email, to_phone, recipient_name, filename,
                             provider, share_url, expiry_days, security_level, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entry.get("id", str(uuid.uuid4())),
                        entry.get("recipient_email", entry.get("to_email", "")),
                        entry.get("to_phone", ""),
                        entry.get("recipient_name", ""),
                        entry.get("filename", ""),
                        entry.get("provider", ""),
                        entry.get("share_url", ""),
                        int(entry.get("expiry_days", 14)),
                        entry.get("security_level", "sicher"),
                        entry.get("timestamp", entry.get("created_at", datetime.now().isoformat())),
                    ))
                conn.commit()
            except Exception:
                pass

        # ── Seed: msg_templates ───────────────────────────────────────────────
        tcount = c.execute("SELECT COUNT(*) FROM msg_templates").fetchone()[0]
        if tcount == 0:
            _seed_msg_templates(c)
            conn.commit()

        # ── Seed: email_templates ─────────────────────────────────────────────
        ecount = c.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
        if ecount == 0:
            _seed_email_templates(c)
            conn.commit()

    finally:
        conn.close()


def _seed_msg_templates(c):
    now = datetime.now().isoformat()
    templates = [
        {
            "id": str(uuid.uuid4()),
            "name": "Angebot",
            "title": "Angebot – [Betreff]",
            "sort_order": 1,
            "content": """# Angebot – [Betreff]

Sehr geehrte Damen und Herren,

vielen Dank für Ihr Interesse. Gerne unterbreite ich Ihnen folgendes Angebot:

## Leistungsumfang

- Position 1: [Beschreibung]
- Position 2: [Beschreibung]

## Preise

| Leistung | Preis (netto) |
|---|---|
| Position 1 | 0,00 € |
| Position 2 | 0,00 € |
| **Gesamt** | **0,00 €** |

> Das Angebot ist freibleibend und gilt für 30 Tage.

Mit freundlichen Grüßen
Florian Beubl · Beseco IT Systems""",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Rechnungsbegleitung",
            "title": "Rechnungsbegleitung",
            "sort_order": 2,
            "content": """# Rechnungsbegleitung

Sehr geehrte Damen und Herren,

anbei übersende ich Ihnen die Rechnung Nr. **[Rechnungsnummer]** vom [Datum].

## Rechnungsdetails

- **Betrag:** [Betrag] € (inkl. MwSt.)
- **Fälligkeit:** [Datum]
- **Verwendungszweck:** [Rechnungsnummer]

Bitte überweisen Sie den Betrag auf das Ihnen bekannte Konto.

Bei Fragen stehe ich Ihnen gerne zur Verfügung.

Mit freundlichen Grüßen
Florian Beubl · Beseco IT Systems""",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Passwort-Übermittlung",
            "title": "Zugangsdaten",
            "sort_order": 3,
            "content": """# Zugangsdaten

Sehr geehrte Damen und Herren,

anbei übermittle ich Ihnen Ihre Zugangsdaten für **[Dienst/System]**.

## Ihre Zugangsdaten

| Feld | Wert |
|---|---|
| Benutzername | [Benutzername] |
| Passwort | [Passwort] |
| URL / Server | [URL] |

> **Bitte ändern Sie das Passwort nach dem ersten Login.**

Diese Nachricht wird nach Ablauf automatisch gelöscht.

Mit freundlichen Grüßen
Florian Beubl · Beseco IT Systems""",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Dokument / Vertrag",
            "title": "Unterlagen: [Betreff]",
            "sort_order": 4,
            "content": """# Unterlagen: [Betreff]

Sehr geehrte Damen und Herren,

anbei übersende ich Ihnen folgende Unterlagen:

## Inhalt

1. [Dokument 1]
2. [Dokument 2]

Bitte prüfen und bestätigen Sie den Empfang.

---

Bei Fragen stehe ich gerne zur Verfügung.

Mit freundlichen Grüßen
Florian Beubl · Beseco IT Systems""",
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Allgemeine Nachricht",
            "title": "Vertrauliche Mitteilung",
            "sort_order": 5,
            "content": """# Vertrauliche Mitteilung

Sehr geehrte Damen und Herren,

[Ihre Nachricht hier]

Mit freundlichen Grüßen
Florian Beubl · Beseco IT Systems""",
        },
    ]
    for t in templates:
        c.execute("""
            INSERT INTO msg_templates (id, name, title, content, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (t["id"], t["name"], t["title"], t["content"], t["sort_order"], now, now))


def _seed_email_templates(c):
    now = datetime.now().isoformat()
    html_body = """<!DOCTYPE html>
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
              Guten Tag *vorname* *nachname*,<br><br>
              ich habe Ihnen eine vertrauliche Nachricht bzw. Datei bereitgestellt.
              Sie können diese über den folgenden Link abrufen:
            </p>
            *custom_message*
            <div style="text-align:center;margin:28px 0;">
              <a href="*link*"
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
                  <strong>⏱ Gültigkeit:</strong> *ablauf* Tage
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
              *signatur*
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    c.execute("""
        INSERT INTO email_templates (id, name, subject, html_body, is_default, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid.uuid4()),
        "Standard-Vorlage",
        "Sichere Nachricht von Beseco IT – *dateiname*",
        html_body,
        1,
        now,
        now,
    ))


# ── Contacts CRUD ─────────────────────────────────────────────────────────────

def get_all_contacts() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM contacts ORDER BY last_name, first_name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def save_contact(data: dict) -> dict:
    entry = {
        "id":         str(uuid.uuid4()),
        "company":    data.get("company", "").strip(),
        "last_name":  data.get("last_name", "").strip(),
        "first_name": data.get("first_name", "").strip(),
        "mobile":     data.get("mobile", "").strip(),
        "email":      data.get("email", "").strip(),
        "created_at": datetime.now().isoformat(),
    }
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO contacts (id, company, last_name, first_name, mobile, email, created_at)
            VALUES (:id, :company, :last_name, :first_name, :mobile, :email, :created_at)
        """, entry)
        conn.commit()
    finally:
        conn.close()
    return entry


def update_contact(contact_id: str, data: dict) -> "dict | None":
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        updated = {
            "company":    data.get("company", existing["company"]).strip(),
            "last_name":  data.get("last_name", existing["last_name"]).strip(),
            "first_name": data.get("first_name", existing["first_name"]).strip(),
            "mobile":     data.get("mobile", existing["mobile"]).strip(),
            "email":      data.get("email", existing["email"]).strip(),
        }
        conn.execute("""
            UPDATE contacts SET company=:company, last_name=:last_name,
                first_name=:first_name, mobile=:mobile, email=:email
            WHERE id=:id
        """, {**updated, "id": contact_id})
        conn.commit()
        return {**existing, **updated}
    finally:
        conn.close()


def upsert_contact(data: dict) -> tuple:
    """Insert or update a contact keyed by e-mail (case-insensitive).
    Returns (contact_dict, was_created: bool).
    Contacts with empty e-mail are always inserted fresh."""
    email = data.get("email", "").strip().lower()
    conn = get_db()
    try:
        existing_row = None
        if email:
            existing_row = conn.execute(
                "SELECT * FROM contacts WHERE lower(email) = ?", (email,)
            ).fetchone()
        if existing_row:
            existing = dict(existing_row)
            updated = {
                "company":    data.get("company", existing["company"]).strip(),
                "last_name":  data.get("last_name", existing["last_name"]).strip(),
                "first_name": data.get("first_name", existing["first_name"]).strip(),
                "mobile":     data.get("mobile", existing["mobile"]).strip(),
                "email":      data.get("email", existing["email"]).strip(),
            }
            conn.execute("""
                UPDATE contacts SET company=:company, last_name=:last_name,
                    first_name=:first_name, mobile=:mobile, email=:email
                WHERE id=:id
            """, {**updated, "id": existing["id"]})
            conn.commit()
            return ({**existing, **updated}, False)
        else:
            entry = {
                "id":         str(uuid.uuid4()),
                "company":    data.get("company", "").strip(),
                "last_name":  data.get("last_name", "").strip(),
                "first_name": data.get("first_name", "").strip(),
                "mobile":     data.get("mobile", "").strip(),
                "email":      data.get("email", "").strip(),
                "created_at": datetime.now().isoformat(),
            }
            conn.execute("""
                INSERT INTO contacts (id, company, last_name, first_name, mobile, email, created_at)
                VALUES (:id, :company, :last_name, :first_name, :mobile, :email, :created_at)
            """, entry)
            conn.commit()
            return (entry, True)
    finally:
        conn.close()


def delete_contact(contact_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


# ── History CRUD ──────────────────────────────────────────────────────────────

def get_all_history() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM history ORDER BY created_at DESC"
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                # Backward-compat aliases for templates using old field names
                d["timestamp"]       = d.get("created_at", "")
                d["recipient_email"] = d.get("to_email", "")
                result.append(d)
            return result
        finally:
            conn.close()
    except Exception:
        return []


def append_history(entry: dict):
    record = {
        "id":             entry.get("id", str(uuid.uuid4())),
        "to_email":       entry.get("recipient_email", entry.get("to_email", "")),
        "to_phone":       entry.get("to_phone", ""),
        "recipient_name": entry.get("recipient_name", ""),
        "filename":       entry.get("filename", ""),
        "provider":       entry.get("provider", ""),
        "share_url":      entry.get("share_url", ""),
        "expiry_days":    int(entry.get("expiry_days", 14)),
        "security_level": entry.get("security_level", "sicher"),
        "created_at":     entry.get("timestamp", entry.get("created_at", datetime.now().isoformat())),
    }
    conn = get_db()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO history
                (id, to_email, to_phone, recipient_name, filename,
                 provider, share_url, expiry_days, security_level, created_at)
            VALUES
                (:id, :to_email, :to_phone, :recipient_name, :filename,
                 :provider, :share_url, :expiry_days, :security_level, :created_at)
        """, record)
        conn.commit()
    finally:
        conn.close()


def delete_history_entry(entry_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


# ── Msg Templates CRUD ────────────────────────────────────────────────────────

def get_all_msg_templates() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM msg_templates ORDER BY sort_order, name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def save_msg_template(data: dict) -> dict:
    now = datetime.now().isoformat()
    entry = {
        "id":         str(uuid.uuid4()),
        "name":       data.get("name", "").strip(),
        "title":      data.get("title", "").strip(),
        "content":    data.get("content", "").strip(),
        "sort_order": int(data.get("sort_order", 0)),
        "created_at": now,
        "updated_at": now,
    }
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO msg_templates (id, name, title, content, sort_order, created_at, updated_at)
            VALUES (:id, :name, :title, :content, :sort_order, :created_at, :updated_at)
        """, entry)
        conn.commit()
    finally:
        conn.close()
    return entry


def update_msg_template(template_id: str, data: dict) -> "dict | None":
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM msg_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        now = datetime.now().isoformat()
        updated = {
            "name":       data.get("name", existing["name"]).strip(),
            "title":      data.get("title", existing["title"]).strip(),
            "content":    data.get("content", existing["content"]),
            "sort_order": int(data.get("sort_order", existing["sort_order"])),
            "updated_at": now,
        }
        conn.execute("""
            UPDATE msg_templates SET name=:name, title=:title, content=:content,
                sort_order=:sort_order, updated_at=:updated_at
            WHERE id=:id
        """, {**updated, "id": template_id})
        conn.commit()
        return {**existing, **updated}
    finally:
        conn.close()


def delete_msg_template(template_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM msg_templates WHERE id = ?", (template_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


# ── Email Templates CRUD ──────────────────────────────────────────────────────

def get_all_email_templates() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM email_templates ORDER BY is_default DESC, name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def save_email_template(data: dict) -> dict:
    now = datetime.now().isoformat()
    is_default = int(bool(data.get("is_default", 0)))
    entry = {
        "id":         str(uuid.uuid4()),
        "name":       data.get("name", "").strip(),
        "subject":    data.get("subject", "").strip(),
        "html_body":  data.get("html_body", ""),
        "is_default": is_default,
        "created_at": now,
        "updated_at": now,
    }
    conn = get_db()
    try:
        if is_default:
            conn.execute("UPDATE email_templates SET is_default = 0")
        conn.execute("""
            INSERT INTO email_templates (id, name, subject, html_body, is_default, created_at, updated_at)
            VALUES (:id, :name, :subject, :html_body, :is_default, :created_at, :updated_at)
        """, entry)
        conn.commit()
    finally:
        conn.close()
    return entry


def update_email_template(template_id: str, data: dict) -> "dict | None":
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        now = datetime.now().isoformat()
        is_default = int(bool(data.get("is_default", existing["is_default"])))
        updated = {
            "name":       data.get("name", existing["name"]).strip(),
            "subject":    data.get("subject", existing["subject"]).strip(),
            "html_body":  data.get("html_body", existing["html_body"]),
            "is_default": is_default,
            "updated_at": now,
        }
        if is_default:
            conn.execute("UPDATE email_templates SET is_default = 0")
        conn.execute("""
            UPDATE email_templates SET name=:name, subject=:subject, html_body=:html_body,
                is_default=:is_default, updated_at=:updated_at
            WHERE id=:id
        """, {**updated, "id": template_id})
        conn.commit()
        return {**existing, **updated}
    finally:
        conn.close()


def delete_email_template(template_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM email_templates WHERE id = ?", (template_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


def get_default_email_template() -> "dict | None":
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM email_templates WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            # Fallback: erste Vorlage
            row = conn.execute(
                "SELECT * FROM email_templates ORDER BY created_at LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ── Cloud Providers & SMS Gateways Migration ──────────────────────────────────

def migrate_connections_from_config(config: dict):
    """Migriert Verbindungsdaten aus config.json in die DB (nur wenn Tabellen leer)."""
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        c = conn.cursor()

        # ── cloud_providers ──
        cp_count = c.execute("SELECT COUNT(*) FROM cloud_providers").fetchone()[0]
        if cp_count == 0:
            inserted = False

            # Nextcloud
            nc_url  = config.get("NC_URL", "")
            nc_user = config.get("NC_USER", "")
            nc_pass = config.get("NC_PASSWORD", "")
            nc_folder = config.get("NC_FOLDER", "SecureSend")
            if nc_url or nc_user:
                c.execute("""
                    INSERT INTO cloud_providers (id, name, service, is_default, is_active, config_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()), "Nextcloud", "nextcloud", 1, 1,
                    json.dumps({"url": nc_url, "user": nc_user, "password": nc_pass, "folder": nc_folder}),
                    now,
                ))
                inserted = True

            # OneDrive
            az_client_id     = config.get("AZURE_CLIENT_ID", "")
            az_client_secret = config.get("AZURE_CLIENT_SECRET", "")
            az_tenant_id     = config.get("AZURE_TENANT_ID", "")
            od_user          = config.get("ONEDRIVE_USER", "")
            od_folder        = config.get("ONEDRIVE_FOLDER", "SecureSend")
            if az_client_id or az_tenant_id:
                c.execute("""
                    INSERT INTO cloud_providers (id, name, service, is_default, is_active, config_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()), "OneDrive", "onedrive", 1 if not inserted else 0, 1,
                    json.dumps({"client_id": az_client_id, "client_secret": az_client_secret,
                                "tenant_id": az_tenant_id, "user": od_user, "folder": od_folder}),
                    now,
                ))

            conn.commit()

        # ── sms_gateways ──
        sg_count = c.execute("SELECT COUNT(*) FROM sms_gateways").fetchone()[0]
        if sg_count == 0:
            token_id = config.get("SIPGATE_TOKEN_ID", "")
            token    = config.get("SIPGATE_TOKEN", "")
            sms_id   = config.get("SIPGATE_SMS_ID", "s0")
            if token_id or token:
                c.execute("""
                    INSERT INTO sms_gateways (id, name, service, is_default, is_active, config_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()), "sipgate", "sipgate", 1, 1,
                    json.dumps({"token_id": token_id, "token": token, "sms_id": sms_id}),
                    now,
                ))
                conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ── Cloud Providers CRUD ──────────────────────────────────────────────────────

def get_all_cloud_providers() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM cloud_providers ORDER BY is_default DESC, name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def save_cloud_provider(data: dict) -> dict:
    now = datetime.now().isoformat()
    cfg = data.get("config_json", {})
    if isinstance(cfg, dict):
        cfg = json.dumps(cfg)
    entry = {
        "id":          str(uuid.uuid4()),
        "name":        data.get("name", "").strip(),
        "service":     data.get("service", "nextcloud"),
        "is_default":  int(bool(data.get("is_default", 0))),
        "is_active":   int(bool(data.get("is_active", 1))),
        "config_json": cfg,
        "created_at":  now,
    }
    conn = get_db()
    try:
        if entry["is_default"]:
            conn.execute("UPDATE cloud_providers SET is_default = 0")
        conn.execute("""
            INSERT INTO cloud_providers (id, name, service, is_default, is_active, config_json, created_at)
            VALUES (:id, :name, :service, :is_default, :is_active, :config_json, :created_at)
        """, entry)
        conn.commit()
    finally:
        conn.close()
    return entry


def update_cloud_provider(provider_id: str, data: dict) -> "dict | None":
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM cloud_providers WHERE id = ?", (provider_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        cfg = data.get("config_json", existing["config_json"])
        if isinstance(cfg, dict):
            cfg = json.dumps(cfg)
        updated = {
            "name":        data.get("name", existing["name"]).strip(),
            "service":     data.get("service", existing["service"]),
            "is_default":  int(bool(data.get("is_default", existing["is_default"]))),
            "is_active":   int(bool(data.get("is_active", existing["is_active"]))),
            "config_json": cfg,
        }
        if updated["is_default"]:
            conn.execute("UPDATE cloud_providers SET is_default = 0")
        conn.execute("""
            UPDATE cloud_providers SET name=:name, service=:service, is_default=:is_default,
                is_active=:is_active, config_json=:config_json
            WHERE id=:id
        """, {**updated, "id": provider_id})
        conn.commit()
        return {**existing, **updated}
    finally:
        conn.close()


def delete_cloud_provider(provider_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM cloud_providers WHERE id = ?", (provider_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


def set_default_cloud_provider(provider_id: str):
    conn = get_db()
    try:
        conn.execute("UPDATE cloud_providers SET is_default = 0")
        conn.execute("UPDATE cloud_providers SET is_default = 1 WHERE id = ?", (provider_id,))
        conn.commit()
    finally:
        conn.close()


def get_default_cloud_provider() -> "dict | None":
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM cloud_providers WHERE is_default = 1 AND is_active = 1 LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM cloud_providers WHERE is_active = 1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ── SMS Gateways CRUD ─────────────────────────────────────────────────────────

def get_all_sms_gateways() -> list:
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM sms_gateways ORDER BY is_default DESC, name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def save_sms_gateway(data: dict) -> dict:
    now = datetime.now().isoformat()
    cfg = data.get("config_json", {})
    if isinstance(cfg, dict):
        cfg = json.dumps(cfg)
    entry = {
        "id":          str(uuid.uuid4()),
        "name":        data.get("name", "").strip(),
        "service":     data.get("service", "sipgate"),
        "is_default":  int(bool(data.get("is_default", 0))),
        "is_active":   int(bool(data.get("is_active", 1))),
        "config_json": cfg,
        "created_at":  now,
    }
    conn = get_db()
    try:
        if entry["is_default"]:
            conn.execute("UPDATE sms_gateways SET is_default = 0")
        conn.execute("""
            INSERT INTO sms_gateways (id, name, service, is_default, is_active, config_json, created_at)
            VALUES (:id, :name, :service, :is_default, :is_active, :config_json, :created_at)
        """, entry)
        conn.commit()
    finally:
        conn.close()
    return entry


def update_sms_gateway(gateway_id: str, data: dict) -> "dict | None":
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sms_gateways WHERE id = ?", (gateway_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        cfg = data.get("config_json", existing["config_json"])
        if isinstance(cfg, dict):
            cfg = json.dumps(cfg)
        updated = {
            "name":        data.get("name", existing["name"]).strip(),
            "service":     data.get("service", existing["service"]),
            "is_default":  int(bool(data.get("is_default", existing["is_default"]))),
            "is_active":   int(bool(data.get("is_active", existing["is_active"]))),
            "config_json": cfg,
        }
        if updated["is_default"]:
            conn.execute("UPDATE sms_gateways SET is_default = 0")
        conn.execute("""
            UPDATE sms_gateways SET name=:name, service=:service, is_default=:is_default,
                is_active=:is_active, config_json=:config_json
            WHERE id=:id
        """, {**updated, "id": gateway_id})
        conn.commit()
        return {**existing, **updated}
    finally:
        conn.close()


def delete_sms_gateway(gateway_id: str) -> bool:
    conn = get_db()
    try:
        result = conn.execute("DELETE FROM sms_gateways WHERE id = ?", (gateway_id,))
        conn.commit()
        return result.rowcount > 0
    finally:
        conn.close()


def set_default_sms_gateway(gateway_id: str):
    conn = get_db()
    try:
        conn.execute("UPDATE sms_gateways SET is_default = 0")
        conn.execute("UPDATE sms_gateways SET is_default = 1 WHERE id = ?", (gateway_id,))
        conn.commit()
    finally:
        conn.close()


def get_default_sms_gateway() -> "dict | None":
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM sms_gateways WHERE is_default = 1 AND is_active = 1 LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM sms_gateways WHERE is_active = 1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
