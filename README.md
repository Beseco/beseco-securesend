# SecureSend Cloud — Anleitung

Sichere Übermittlung von Dateien und Nachrichten über verschlüsselte Cloud-Links mit SMS-Passwortschutz.

---

## Inhaltsverzeichnis

1. [Installation (Docker)](#1-installation-docker)
2. [Ersteinrichtung](#2-ersteinrichtung)
3. [Admin-Hierarchie](#3-admin-hierarchie)
4. [Superadmin-Bereich](#4-superadmin-bereich)
5. [Reseller-Bereich](#5-reseller-bereich)
6. [Organisations-Admin-Bereich](#6-organisations-admin-bereich)
7. [Benutzer — Sicher Senden](#7-benutzer--sicher-senden)
8. [Handynummer anfragen](#8-handynummer-anfragen)
9. [Upload-Link senden](#9-upload-link-senden)
10. [Selbstregistrierung](#10-selbstregistrierung)

---

## 1. Installation (Docker)

### Voraussetzungen
- Docker + Docker Compose
- Reverse Proxy (nginx, Traefik o. ä.) für HTTPS — empfohlen, aber nicht zwingend

### Schritte

```bash
# Repository klonen
git clone git@github.com:Beseco/beseco-securesend.git /opt/securesend
cd /opt/securesend

# Konfigurationsdatei anlegen
cp cloud/.env.example .env
nano .env          # Werte anpassen (siehe unten)

# Container starten
docker compose up -d
```

### Wichtige `.env`-Einstellungen

| Variable | Beschreibung | Beispiel |
|----------|-------------|---------|
| `SECRET_KEY` | JWT-Schlüssel — **unbedingt ändern!** | `openssl rand -hex 32` |
| `PUBLIC_BASE_URL` | Öffentliche URL der App | `https://securesend.firma.de` |
| `DATABASE_URL` | SQLite (Standard) oder PostgreSQL | `sqlite+aiosqlite:///./securesend.db` |
| `SECURESEND_STORAGE_*` | Gehosteter Speicher (lokal oder S3), siehe Tabelle in [docs/Umsetzungsplan-SecureSend-Storage.md](docs/Umsetzungsplan-SecureSend-Storage.md) | — |

> **Tipp:** Ohne `PUBLIC_BASE_URL` werden E-Mail-Links automatisch aus der Anfrage-URL abgeleitet — funktioniert hinter nginx mit korrekten `proxy_set_header`-Einträgen.

Ausführlicher Betrieb und Phasenübersicht: [docs/Umsetzungsplan-SecureSend-Storage.md](docs/Umsetzungsplan-SecureSend-Storage.md).

### Update

```bash
cd /opt/securesend
git pull
docker compose up -d --build
```

### Datenbank zurücksetzen (Neustart)

```bash
docker compose down -v   # löscht auch das DB-Volume
docker compose up -d     # Einrichtungsassistent erscheint beim ersten Aufruf
```

---

## 2. Ersteinrichtung

Beim ersten Aufruf erscheint automatisch der **Einrichtungsassistent**.

| Feld | Beschreibung |
|------|-------------|
| **Name** | Firmenname (wird als Reseller und Organisation angelegt) |
| **Slug** | URL-Kürzel, wird automatisch generiert |
| **Vorname / Nachname** | Name des ersten Administrators |
| **E-Mail** | Login-E-Mail |
| **Passwort** | Mindestens 8 Zeichen |

Nach dem Absenden werden automatisch angelegt:
- Superadmin-Benutzer (der eingegebenen Organisation zugeordnet)
- Reseller (Firmenname)
- Organisation (Firmenname, dem Reseller zugeordnet)

---

## 3. Admin-Hierarchie

```
Superadmin
  └── Reseller  (z. B. IT-Dienstleister)
        └── Organisation  (z. B. Behörde, Unternehmen)
              └── Benutzer  (org_admin / org_user)
```

| Rolle | Rechte |
|-------|--------|
| `superadmin` | Alles — Reseller, Orgs, alle Einstellungen |
| `reseller_admin` | Eigene Organisationen + Reseller-Einstellungen |
| `org_admin` | Eigene Organisation: Benutzer, Cloud, SMS, SMTP |
| `org_user` | Senden, Kontakte, Verlauf |

---

## 4. Superadmin-Bereich

### Reseller verwalten (`Administration → Reseller`)
- Alle Reseller auflisten, anlegen, bearbeiten, deaktivieren
- **„Anmelden"** → wechselt in den Reseller-Kontext

### Kontext-Navigation
Der Superadmin kann stufenweise in Reseller- und Org-Kontext wechseln:

```
Reseller-Liste → [Anmelden] → Reseller-CP
                                  → [Anmelden] → Org-CP
```

Ein **✕** im Breadcrumb-Chip der Sidebar beendet den Kontext (eine Ebene zurück).

### Einstellungen (`Administration → Einstellungen`)
Globale System-Einstellungen (in Entwicklung).

---

## 5. Reseller-Bereich

Erreichbar im Reseller-Kontext über **Administration → Organisationen / Einstellungen**.

### Organisationen
- Organisationen auflisten, anlegen, bearbeiten, deaktivieren
- **„Anmelden"** → wechselt in das Org-CP

### Einstellungen
- Reseller-SMTP: dient als Fallback für Orgs ohne eigene SMTP-Konfiguration

---

## 6. Organisations-Admin-Bereich

Erreichbar über **Administration → Organisation** (`/ui/admin/org`).

### Tab: Benutzer
- Benutzer anlegen, bearbeiten, Rolle zuweisen, aktivieren/deaktivieren
- Rollen: `org_admin`, `org_user`

### Tab: Anbieter (Cloud-Speicher)
- **Nextcloud**: WebDAV-URL, Benutzername, Passwort
- **OneDrive**: Client-ID, Secret, Tenant-ID
- Verbindungsstatus und Speicherquota werden automatisch beim Tab-Öffnen geprüft

### Tab: SMS-Gateway
- **sipgate**: Token-ID, Token, SMS-ID (z. B. `s0`)
- Zeigt: Verbindungsstatus, Kontostand in EUR
- **EVN-Button**: Einzelverbindungsnachweis (letzte 20 SMS)

### Tab: Einstellungen
- **SMTP**: Mailserver für ausgehende E-Mails
- **Selbstregistrierung**: aktivieren/deaktivieren, Domain-Einschränkung (z. B. `@firma.de`)

---

## 7. Benutzer — Sicher Senden

### Nachricht / Datei senden (`Senden`)

1. Empfänger aus Kontakten wählen oder E-Mail + Handynummer manuell eingeben
2. Betreff eingeben
3. Wählen:
   - **Nachricht** im Editor verfassen (Markdown/WYSIWYG), oder
   - **Dateien** hochladen (Mehrfachauswahl möglich)
4. Absenden

**Was passiert im Hintergrund:**
- Dateien werden in einem Ordner im Cloud-Speicher der Organisation abgelegt
- Ein passwortgeschützter Freigabe-Link wird generiert
- Das Passwort wird per SMS an die Handynummer des Empfängers gesendet
- Der Empfänger erhält eine E-Mail mit dem Link

**Blockierte Dateitypen:** `.exe`, `.bat`, `.cmd`, `.ps1`, `.msi`, `.vbs`, `.sh`, `.jar`, `.scr`, `.dll`, `.hta`, `.lnk` u. a.

### Kontakte
- Kontakte anlegen, bearbeiten, löschen
- VCF-Import / VCF-Export
- Kontakte ohne Handynummer → Handynummer-Anfrage (siehe Abschnitt 8)

### Verlauf
- Alle gesendeten Nachrichten/Dateien mit Datum, Empfänger und Status

---

## 8. Handynummer anfragen

Fehlt einem Kontakt die Handynummer, kann eine Anfrage per E-Mail gesendet werden.

**Ablauf:**
1. Kontakteliste → beim Kontakt ohne Mobilnummer auf **📱** klicken
2. Bestätigungsdialog → E-Mail wird an den Kontakt gesendet
3. Kontakt öffnet den Link (kein Login nötig) und gibt seine Nummer ein
4. Kontakt wird automatisch aktualisiert, Absender erhält E-Mail-Benachrichtigung

> Links sind 14 Tage gültig.

---

## 9. Upload-Link senden

Empfänger können Dateien sicher zurückschicken — ohne Login.

**Ablauf:**
1. Kontakteliste → **📤 Upload-Link senden**
2. E-Mail des Empfängers, optionale Nachricht und Gültigkeit (7 / 14 / 30 Tage) eintragen
3. Empfänger erhält E-Mail mit Link
4. Empfänger lädt Dateien per Drag-and-Drop hoch (kein Login nötig)
5. Dateien landen im Cloud-Speicher der Organisation
6. Absender erhält E-Mail-Benachrichtigung mit dem Ordner-Link

---

## 10. Selbstregistrierung

Aktivierbar unter **Organisation → Einstellungen → Selbstregistrierung**.

**Optionen:**
- Registrierung ein-/ausschalten
- Domain-Einschränkung: z. B. `@firma.de` — nur diese Domains dürfen sich registrieren

Der **Registrierungslink** wird im Einstellungs-Tab angezeigt und kann an Mitarbeiter weitergegeben werden.

**Ablauf für neue Benutzer:**
1. Registrierungslink aufrufen → Formular mit Name, E-Mail, Passwort ausfüllen
2. Bestätigungs-E-Mail erhalten und Link anklicken
3. Konto ist aktiviert → Login möglich

---

## Passwort ändern

Jeder eingeloggte Benutzer kann sein Passwort über das **Schlüssel-Icon** unten links in der Sidebar ändern.

---

## Technische Details

| Komponente | Technologie |
|-----------|-------------|
| Backend | FastAPI + SQLAlchemy 2.0 (async) |
| Datenbank | SQLite (Entwicklung) / PostgreSQL (Produktion) |
| Authentifizierung | JWT (HttpOnly Cookie) |
| Cloud-Speicher | Nextcloud (WebDAV) / Microsoft OneDrive (Graph API) |
| SMS | sipgate REST API v2 |
| E-Mail | SMTP (STARTTLS / SSL) |
| Frontend | Jinja2 Templates + Tailwind CSS |
| Deployment | Docker + optional nginx Reverse Proxy |
