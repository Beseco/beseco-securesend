# 🔒 Beseco SecureSend

Lokales Web-Tool für sichere Kunden-Kommunikation:
- Markdown-Editor mit Live-Vorschau
- Upload nach OneDrive (beubl.de) als passwortgeschützte Freigabe
- E-Mail mit Link via SMTP (smtp-gw01 / Brevo)
- SMS mit Passwort via sipgate

---

## Installation

```bash
cd beseco-sender
pip install -r requirements.txt
cp .env.example .env
# .env befüllen (siehe unten)
python app.py
# → http://localhost:5001
```

---

## .env befüllen

### 1. Azure App-Registrierung (einmalig, ~5 Minuten)

1. https://portal.azure.com → **Azure Active Directory** → **App-Registrierungen** → **Neue Registrierung**
   - Name: `BesecoSecureSend`
   - Unterstützte Kontotypen: *Nur Konten in diesem Organisationsverzeichnis*
   - Redirect URI: leer lassen

2. Nach Erstellung notieren:
   - **Anwendungs-ID (Client-ID)** → `AZURE_CLIENT_ID`
   - **Verzeichnis-ID (Tenant-ID)** → `AZURE_TENANT_ID`

3. **Zertifikate & Geheimnisse** → Neuer geheimer Clientschlüssel
   - Beschreibung: `BesecoSecureSend`
   - Ablauf: 24 Monate
   - Den generierten Wert sofort kopieren → `AZURE_CLIENT_SECRET`

4. **API-Berechtigungen** → Berechtigung hinzufügen → Microsoft Graph → **Anwendungsberechtigungen**:
   - `Files.ReadWrite.All`
   - `Mail.Send` *(falls du E-Mail über Graph statt SMTP senden willst)*
   - → **Administratorzustimmung erteilen** (blauer Button)

> ⚠️ Client Credentials Flow (App-Permission) erlaubt Zugriff auf den OneDrive
> des Users `ONEDRIVE_USER`. Für Einzelpersonen-Tenants ist das in Ordnung.

---

### 2. sipgate API-Token

1. https://app.sipgate.com → **Persönliche Zugangsdaten** → **Token**
2. **Token hinzufügen**:
   - Beschreibung: `BesecoSecureSend`
   - Rechte: `sessions:sms:write`
3. Token-ID + Token notieren → `SIPGATE_TOKEN_ID` / `SIPGATE_TOKEN`
4. SMS-Geräte-ID prüfen: meist `s0` – im sipgate-Portal unter **Telefonie** → **SMS** nachschauen

---

### 3. OneDrive-Ordner

Beim ersten Aufruf wird automatisch der Ordner `SecureSend` in deinem OneDrive angelegt.
Du kannst den Ordnernamen in `app.py` Zeile `ONEDRIVE_FOLDER` ändern.

---

## Workflow

1. `python app.py` starten
2. http://localhost:5001 im Browser öffnen
3. Markdown-Nachricht schreiben (Live-Vorschau rechts)
4. E-Mail und Mobilnummer des Kunden eingeben
5. **Sicher übermitteln** klicken

Was passiert:
- Datei wird als `.md` in OneDrive hochgeladen
- Passwortgeschützter Freigabe-Link wird erstellt (14 Tage gültig)
- Kunde bekommt E-Mail mit dem Link (aber OHNE Passwort)
- Kunde bekommt SMS mit dem Passwort (zwei-Kanal-Zustellung)

---

## Autostart (optional, Windows)

Shortcut in Autostart-Ordner (`shell:startup`) anlegen:
```bat
@echo off
cd /d C:\beseco-sender
python app.py
```

Oder als Windows-Service via NSSM.
