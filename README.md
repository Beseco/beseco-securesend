# 🔒 Beseco SecureSend

**v1.2** · Sicheres Web-Tool für die Übermittlung vertraulicher Nachrichten und Dateien an Kunden — lokal betrieben, keine Cloud-Infrastruktur erforderlich.

**Zwei-Kanal-Prinzip:** Link per E-Mail · Passwort per SMS

---

## Features

- **WYSIWYG-Editor** (Toast UI) mit Markdown-Unterstützung
- **Cloudspeicher** — mehrere Nextcloud- und OneDrive-Instanzen verwaltbar
- **Sicherheitsstufen** — Normal (kein Passwort) · Sicher (Passwort + SMS) · Erhöhte Sicherheit (verschlüsselte PDF + SMS)
- **Adressbuch** — Kontakte speichern, Suche mit Autovervollständigung
- **History** — alle Versendungen nachvollziehbar
- **Vorlagen** — Nachrichten- und E-Mail-Vorlagen mit Variablen (`*vorname*`, `*link*`, `*signatur*` …)
- **Signatur** — wird automatisch in den Editor vorgeladen
- **Benachrichtigung** — optionale Bestätigungs-E-Mail nach jedem Versand
- **Einstellungen-UI** — alle Verbindungen und Konfiguration über die Web-Oberfläche, kein manuelles Editieren von Dateien nötig
- **Datei-Upload** — Drag & Drop, beliebige Dateitypen
- **SQLite-Datenbank** — Kontakte, History, Vorlagen und Verbindungen persistent gespeichert

---

## Installation

### Voraussetzungen

- Python 3.10 oder neuer
- pip

### Schnellstart

```bash
git clone git@github.com:Beseco/beseco-securesend.git
cd beseco-securesend
pip install -r requirements.txt
python app.py
```

Die App ist dann erreichbar unter **http://localhost:5001**

Beim ersten Start wird automatisch die Datenbank `data.db` angelegt.

### Einrichtung nach dem ersten Start

1. **http://localhost:5001/settings** öffnen
2. Tab **Verbindungen** → Cloudspeicher und SMS Gateway konfigurieren
3. Tab **Allgemein** → Signatur, Benachrichtigungen
4. Tab **Sicherheit & App** → optionales Einstellungspasswort setzen
5. Tab **Vorlagen** → Nachrichten- und E-Mail-Vorlagen anpassen

> Alle Einstellungen werden sofort in `config.json` (allgemeine Config) bzw. `data.db` (Verbindungen, Vorlagen, Kontakte) gespeichert.

---

## Cloudspeicher einrichten

### Nextcloud

1. Nextcloud öffnen → **Einstellungen** → **Sicherheit** → **App-Passwörter**
2. Neues App-Passwort erstellen (Name z.B. `SecureSend`)
3. In SecureSend unter **Einstellungen → Verbindungen → Cloudspeicher → Hinzufügen**:
   - Name: frei wählbar (z.B. `LRA Cloud`)
   - Dienst: `Nextcloud`
   - Server-URL, Benutzername, App-Passwort, Zielordner eintragen

### OneDrive / Microsoft 365

1. [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App-Registrierungen** → **Neue Registrierung**
   - Name: `BesecoSecureSend`
   - Unterstützte Kontotypen: *Nur Konten in diesem Organisationsverzeichnis*
   - Redirect URI: leer lassen
2. Nach Erstellung notieren:
   - **Anwendungs-ID (Client-ID)** → `Client ID`
   - **Verzeichnis-ID (Tenant-ID)** → `Tenant ID`
3. **Zertifikate & Geheimnisse** → Neuer geheimer Clientschlüssel (Ablauf: 24 Monate) → Wert sofort kopieren → `Client Secret`
4. **API-Berechtigungen** → Berechtigung hinzufügen → Microsoft Graph → Anwendungsberechtigungen:
   - `Files.ReadWrite.All`
   - → **Administratorzustimmung erteilen**
5. In SecureSend unter **Einstellungen → Verbindungen → Cloudspeicher → Hinzufügen**: Felder ausfüllen

---

## SMS Gateway einrichten

### sipgate

1. [app.sipgate.com](https://app.sipgate.com) → **Persönliche Zugangsdaten** → **Token** → **Token hinzufügen**
   - Beschreibung: `BesecoSecureSend`
   - Rechte: `sessions:sms:write`
2. Token-ID und Token notieren
3. SMS-Geräte-ID prüfen: meist `s0` (unter **Telefonie → SMS**)
4. In SecureSend unter **Einstellungen → Verbindungen → SMS Gateway → Hinzufügen**: Felder ausfüllen

---

## E-Mail (SMTP) einrichten

Unter **Einstellungen → Verbindungen → E-Mail (SMTP)**:

| Feld | Beispiel |
|------|---------|
| SMTP-Host | `mail.firma.de` |
| SMTP-Port | `587` (STARTTLS) · `465` (SSL) · `25` (kein TLS) |
| Verschlüsselung | `STARTTLS` / `SSL/TLS` / `Kein TLS` |
| Benutzername | `absender@firma.de` (leer lassen wenn kein Auth) |
| Passwort | — |
| Absender E-Mail | `absender@firma.de` |
| Absender Name | `Max Mustermann – Firma GmbH` |

---

## Workflow

1. App starten: `python app.py`
2. **http://localhost:5001** im Browser öffnen
3. Empfänger aus dem Adressbuch wählen oder manuell eingeben
4. Sicherheitsstufe wählen (Standard: **Sicher**)
5. Nachricht im WYSIWYG-Editor schreiben (oder Datei anhängen)
6. **Sicher übermitteln** klicken

Was passiert im Hintergrund:
- Nachricht/Datei wird beim konfigurierten Cloudspeicher hochgeladen
- Passwortgeschützter Freigabe-Link wird erstellt (Standard: 14 Tage gültig)
- Empfänger erhält **E-Mail mit dem Link** (ohne Passwort)
- Empfänger erhält **SMS mit dem Passwort** (Zwei-Kanal-Prinzip)

---

## Autostart

### macOS — LaunchAgent

Datei `~/Library/LaunchAgents/com.beseco.securesend.plist` anlegen:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.beseco.securesend</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Pfad/zu/beseco-securesend/app.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>WorkingDirectory</key><string>/Pfad/zu/beseco-securesend</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.beseco.securesend.plist
```

### Windows — Autostart-Ordner

Batch-Datei im Autostart-Ordner (`shell:startup`) ablegen:

```bat
@echo off
cd /d C:\beseco-securesend
python app.py
```

Oder als Windows-Dienst via [NSSM](https://nssm.cc).

---

## Technologie-Stack

| Bereich | Technologie |
|---------|-------------|
| Backend | Python 3, Flask |
| Datenbank | SQLite (stdlib `sqlite3`) |
| Cloud-Upload | Nextcloud WebDAV + OCS API, Microsoft Graph (MSAL) |
| E-Mail | smtplib (none / STARTTLS / SSL) |
| SMS | sipgate REST API |
| Editor | Toast UI Editor (WYSIWYG Markdown) |
| Frontend | Vanilla JS, Tailwind CSS (CDN) |
| PDF-Verschlüsselung | fpdf2 + pypdf (optional) |

---

## Datenschutz & Sicherheit

- Die App läuft **ausschließlich lokal** — kein öffentlich erreichbarer Server
- Passwörter/PINs werden **nicht in der History gespeichert**
- Nachrichten-Inhalte werden **nicht protokolliert**
- Verbindungsdaten (Tokens, Passwörter) liegen in `config.json` bzw. `data.db` auf dem lokalen Rechner
- Freigabe-Links laufen nach konfigurierbarer Frist automatisch ab

---

*v1.2 · Beseco IT Systems · Florian Beubl · Freising*
