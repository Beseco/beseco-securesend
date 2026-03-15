# SecureSend – Projektplan & Roadmap

Beseco SecureSend ist ein lokal betriebenes Web-Tool (Flask + Vanilla-JS) für die sichere Übermittlung vertraulicher Nachrichten und Dateien an Kunden. Die Nachricht wird als Markdown-Datei bei einem Cloud-Speicherprovider abgelegt, mit einem zufälligen Passwort gesichert und per Zwei-Kanal-Prinzip zugestellt: der Link kommt per E-Mail, das Passwort kommt per SMS. Das Tool läuft ausschließlich auf dem eigenen Arbeitsrechner – es gibt keinen öffentlich erreichbaren Server.

---

## Aktueller Stand

| Bereich | Status |
|---|---|
| Markdown-Editor mit Live-Vorschau (marked.js) | Fertig |
| Upload nach OneDrive (Microsoft Graph, MSAL) | Fertig |
| Upload nach Nextcloud (WebDAV + OCS) | Fertig |
| Passwortgeschützte Freigabe-Links (beide Provider) | Fertig |
| E-Mail-Versand via SMTP (HTML-Template, none/starttls/ssl) | Fertig |
| SMS-Versand via sipgate REST API | Fertig |
| Provider-Auswahl (OneDrive / Nextcloud) als Toggle in der UI | Fertig |
| Einstellungsseite (`/settings`) mit Live-Save in `config.json` | Fertig |
| SMTP-Verbindungstest direkt aus den Einstellungen | Fertig |
| Zwei-Kanal-Zustellung (Link per Mail, Passwort per SMS) | Fertig |

**Technologiestack:** Python 3, Flask, MSAL (Azure), requests, smtplib, python-dotenv. Frontend: reines HTML/CSS/JS, keine Build-Pipeline nötig.

**Was noch fehlt:** Adressbuch, History, Datei-Upload, Passwortschutz für Einstellungen, SMS-Provider-Abstraktion, weitere Speicherprovider, Sender-Benachrichtigung, kundenbezogene Unterordner.

---

## Roadmap

### Phase 1 – Quick Wins (sofort umsetzbar, geringer Aufwand)

#### 1.1 Nur konfigurierte Speicherprovider in der UI anzeigen

**Beschreibung:** Aktuell werden in der UI immer beide Buttons (OneDrive, Nextcloud) angezeigt – unabhängig davon, ob die jeweiligen Zugangsdaten in `config.json` hinterlegt sind. Das führt zu Laufzeitfehlern, wenn ein Provider nicht konfiguriert ist.

**Technischer Ansatz:**
- Neuer Flask-Endpoint `GET /api/providers` gibt eine Liste der konfigurierten Provider zurück (z.B. `["onedrive", "nextcloud"]`).
- Logik in `app.py`: Ein Provider gilt als konfiguriert, wenn alle Pflichtfelder gesetzt sind (OneDrive: `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`; Nextcloud: `NC_URL`, `NC_USER`, `NC_PASSWORD`).
- Im Frontend wird `setProvider()`-Logik um einen Init-Aufruf ergänzt, der die Provider-Liste abruft und nur konfigurierte Buttons rendert.
- Ist nur ein Provider konfiguriert, wird dieser automatisch vorausgewählt (kein Toggle nötig).
- Sind gar keine Provider konfiguriert, erscheint ein Hinweisbanner mit Link zu den Einstellungen.

**Geschätzter Aufwand:** S (2–3 Stunden)

---

#### 1.2 Einstellungen per Passwort schützen

**Beschreibung:** `/settings` und `/settings/save` sind aktuell ungeschützt. Jeder mit lokalem Netzwerkzugang könnte Zugangsdaten lesen oder überschreiben.

**Technischer Ansatz:**
- In `config.json` ein neues Feld `SETTINGS_PASSWORD` (gehashter Wert, z.B. `werkzeug.security.generate_password_hash`).
- Bei Aufruf von `/settings`: Passwort-Eingabedialog (einfaches HTML-Modal). Das eingegebene Passwort wird per `POST /settings/auth` gegen den Hash geprüft; bei Erfolg wird ein Session-Cookie gesetzt (`flask.session`).
- Der Decorator `@require_settings_auth` prüft das Session-Cookie auf allen Settings-Routen.
- Der initiale Passwort-Setup erfolgt beim ersten Aufruf, wenn `SETTINGS_PASSWORD` noch leer ist.
- Alternative für noch einfachere Umsetzung: HTTP Basic Auth via `functools.wraps` und konstantem Passwort aus `config.json`.

**Geschätzter Aufwand:** S (3–4 Stunden)

---

#### 1.3 Sender-Benachrichtigung per E-Mail bei erfolgreichem Versand

**Beschreibung:** Florian (der Absender) erhält nach jedem erfolgreichen Versand eine kurze Bestätigungs-E-Mail mit Metadaten: an wen gesendet, welche Datei, welcher Provider, Ablaufdatum. Kein PIN, keine Nachrichteninhalte in der Mail.

**Technischer Ansatz:**
- Neues Feld `MAIL_NOTIFY` in `config.json` (E-Mail-Adresse des Senders, kann identisch mit `MAIL_FROM` sein).
- In `app.py`, Route `/send`: Nach erfolgreichem Durchlauf wird zusätzlich zu Kunden-Mail + SMS eine zweite `send_email()`-Anfrage an `MAIL_NOTIFY` abgesetzt.
- Separate `build_notify_email_html()`-Funktion erzeugt eine minimalistische Bestätigungsmail.
- Das Flag kann in den Einstellungen per Checkbox aktiviert/deaktiviert werden.

**Geschätzter Aufwand:** S (2 Stunden)

---

### Phase 2 – Core Features (mittlerer Aufwand)

#### 2.1 Adressbuch (Firma, Nachname, Vorname, Mobil, E-Mail)

**Beschreibung:** Statt E-Mail und Telefonnummer jedes Mal manuell einzutippen, können Kunden im Adressbuch gespeichert und per Klick in das Formular übernommen werden. Das Adressbuch hat einen vollständigen CRUD (Erstellen, Lesen, Aktualisieren, Löschen).

**Technischer Ansatz:**
- Datenhaltung in `addressbook.json` im App-Verzeichnis (kein Datenbankserver nötig).
- Datenstruktur pro Eintrag: `{ "id": "<uuid>", "company": "...", "last_name": "...", "first_name": "...", "mobile": "...", "email": "...", "created_at": "..." }`
- Flask-Routen:
  - `GET /api/contacts` – alle Kontakte als JSON
  - `POST /api/contacts` – neuen Kontakt anlegen
  - `PUT /api/contacts/<id>` – Kontakt bearbeiten
  - `DELETE /api/contacts/<id>` – Kontakt löschen
- In der Haupt-UI: Suchfeld "Kontakt wählen" mit Autocomplete (clientseitig gefiltert). Bei Auswahl werden E-Mail und Mobilnummer automatisch ins Formular übernommen.
- Eigene Adressbuch-Seite unter `/contacts` mit Tabelle, Suchfunktion, Inline-Bearbeitungsformular.
- Navigation zwischen Hauptseite, Adressbuch, Einstellungen und History als einfache Tabs im Header.

**Geschätzter Aufwand:** M (1–2 Tage)

---

#### 2.2 History (Provider, Link, Empfänger, Zeitstempel)

**Beschreibung:** Jeder erfolgreiche Versand wird in einer lokalen History gespeichert. So kann nachvollzogen werden, was wann an wen gesendet wurde. Aus Datenschutzgründen: kein PIN, keine Nachrichteninhalte.

**Technischer Ansatz:**
- Datenhaltung in `history.json` (append-only, neueste Einträge zuerst).
- Gespeicherte Felder pro Eintrag: `{ "id": "<uuid>", "timestamp": "ISO8601", "provider": "nextcloud|onedrive", "filename": "...", "share_url": "...", "recipient_email": "...", "recipient_name": "...", "expiry_days": 14 }`
- Explizit nicht gespeichert: PIN/Passwort, Nachrichteninhalt.
- In `app.py`, Route `/send`: Nach erfolgreichem Versand wird ein neuer Eintrag in `history.json` geschrieben (Hilfsfunktion `_write_history_entry()`).
- Neue Route `GET /api/history` liefert die Liste als JSON (optional: Paginierung via `?page=1&per_page=50`).
- Neue Seite `/history` mit sortier- und filterbarer Tabelle (Datum, Empfänger, Provider). Klick auf den Link öffnet die Freigabe in einem neuen Tab.
- Alte Einträge mit abgelaufenem Datum werden in der UI als "abgelaufen" markiert (client-seitige Datumsberechnung).

**Geschätzter Aufwand:** M (1 Tag)

---

#### 2.3 Datei-Upload (zusätzlich oder als Alternative zur Nachricht)

**Beschreibung:** Neben oder anstelle der Markdown-Nachricht soll es möglich sein, eine beliebige Datei (PDF, DOCX, XLSX, ZIP etc.) hochzuladen und diese dann sicher zu übermitteln.

**Technischer Ansatz:**
- Im Frontend: Upload-Bereich als Tab-Wechsel oder zweiter Modus ("Nachricht" / "Datei"). Drag-and-Drop-Zone oder klassischer File-Input.
- Maximale Dateigröße (z.B. 50 MB) wird client- und serverseitig geprüft.
- Backend: Route `/send` erhält entweder JSON (Nachrichteninhalt) oder `multipart/form-data` (Datei-Upload). Alternativ: neuer Endpoint `/send/file`.
- Der `Content-Type`-Header beim Upload an OneDrive/Nextcloud wird nicht mehr hart auf `text/markdown` gesetzt, sondern dynamisch bestimmt (über Python `mimetypes`-Modul).
- Die Datei wird unverändert hochgeladen (kein Re-Encoding), der Passwort-Link-Mechanismus bleibt identisch.
- UI zeigt Dateiname, Typ und Größe nach Auswahl an; der Markdown-Editor wird ausgeblendet, wenn eine Datei gewählt ist.
- Optional: Kombimodus – eine Markdown-Notiz als Begleittext + eine Datei werden gemeinsam in einem Unterordner abgelegt.

**Geschätzter Aufwand:** M (1–2 Tage)

---

#### 2.4 Kundenbezogene Unterordner beim Upload

**Beschreibung:** Statt alle Dateien flach in einem Ordner (z.B. `SecureSend/`) abzulegen, wird pro Kunde ein Unterordner angelegt: `SecureSend/Mueller_GmbH/` oder `SecureSend/Beubl_Florian/`. Das verbessert die Übersichtlichkeit im Cloud-Speicher erheblich.

**Technischer Ansatz:**
- Der Unterordnername wird aus dem Adressbucheintrag generiert: `{Nachname}_{Firma}` (bereinigt, nur alphanumerische Zeichen und `-`).
- Bei OneDrive: Der Upload-Pfad in `upload_to_onedrive()` wird von `{FOLDER}/{filename}` zu `{FOLDER}/{subfolder}/{filename}` erweitert. OneDrive Graph API legt den Unterordner beim ersten Upload automatisch an.
- Bei Nextcloud: Vor dem Upload wird per `MKCOL` der Unterordner angelegt (analog zur bestehenden `nc_ensure_folder()`-Funktion).
- Im Sendeformular: Wenn ein Kontakt aus dem Adressbuch gewählt wurde, wird der Unterordner automatisch vorbelegt. Manuell editierbar.
- Rückwärtskompatibel: Wenn kein Kontakt gewählt, wird wie bisher der Hauptordner verwendet.

**Geschätzter Aufwand:** S–M (3–4 Stunden, setzt Feature 2.1 voraus)

---

### Phase 3 – Architektur & Erweiterbarkeit (Provider-Abstraktion)

#### 3.1 SMS-Provider-Abstraktion (Sipgate + Twilio + 7Mobile + Vonage)

**Beschreibung:** Die SMS-Funktion ist aktuell hart auf sipgate verdrahtet. Eine abstrakte Schnittstelle erlaubt es, beliebige SMS-Provider zu verwenden – und zwischen ihnen zu wechseln, ohne Code zu ändern.

**Technischer Ansatz:**
- Abstrakte Basisklasse `SmsProvider` (Python ABC):
  ```python
  class SmsProvider(ABC):
      @abstractmethod
      def send(self, to_number: str, message: str) -> None: ...

      @abstractmethod
      def is_configured(self) -> bool: ...
  ```
- Konkrete Implementierungen in `providers/sms/`:
  - `sipgate.py` – bestehende Logik aus `app.py` extrahieren
  - `twilio.py` – Twilio REST API (`twilio`-Paket, oder direkt `requests`)
  - `seven_mobile.py` – 7Mobile (ehemals sms77) HTTP-API
  - `vonage.py` – Vonage (ehemals Nexmo) REST API
- Factory-Funktion `get_sms_provider(config: dict) -> SmsProvider` wählt anhand von `SMS_PROVIDER`-Konfigurationsfeld den richtigen Provider aus.
- In `config.json` neues Feld `SMS_PROVIDER: "sipgate|twilio|seven_mobile|vonage"` sowie die jeweiligen Credentials.
- Einstellungsseite: dynamische Sektionen je nach gewähltem SMS-Provider.
- Fehlermeldungen werden vereinheitlicht (jede Implementierung wirft eine gemeinsame `SmsError`-Exception).

**Geschätzter Aufwand:** M (1–2 Tage)

---

#### 3.2 Speicherprovider-Abstraktion

**Beschreibung:** Analog zu SMS-Providern: Eine gemeinsame Schnittstelle für alle Speicherprovider ermöglicht die einfache Ergänzung weiterer Cloud-Dienste, ohne die Kernlogik zu verändern.

**Technischer Ansatz:**
- Abstrakte Basisklasse `StorageProvider` (Python ABC):
  ```python
  class StorageProvider(ABC):
      @abstractmethod
      def upload(self, filename: str, content: bytes, subfolder: str = "") -> str: ...

      @abstractmethod
      def create_share_link(self, file_ref: str, password: str, days: int) -> str: ...

      @abstractmethod
      def is_configured(self) -> bool: ...

      @property
      @abstractmethod
      def display_name(self) -> str: ...
  ```
- Konkrete Implementierungen in `providers/storage/`:
  - `onedrive.py` – bestehende Logik extrahieren
  - `nextcloud.py` – bestehende Logik extrahieren
  - `dropbox.py` – Dropbox API v2
  - `s3.py` – AWS S3 / MinIO (boto3 + Pre-Signed URLs)
  - `sftp.py` – SFTP via paramiko; Share-Link via eigenem Download-Endpoint oder SFTP-zu-HTTP-Proxy
  - `google_drive.py` – Google Drive API v3
  - `box.py` – Box API
  - `webdav.py` – generisches WebDAV (deckt auch Hetzner Storage Box, Strato HiDrive usw. ab)
- `upload_and_share()` in `app.py` wird zur Factory: lädt die konfigurierte Provider-Klasse dynamisch.
- Registry-Mechanismus: `STORAGE_PROVIDERS = {"onedrive": OneDriveProvider, "nextcloud": NextcloudProvider, ...}` – neue Provider durch Eintrag in das Dict registrierbar.
- Für Provider ohne nativen passwortgeschützten Link (z.B. S3): optionaler lokaler Redirect-Endpoint, der das Passwort vor Weiterleitung prüft (nur für interne Nutzung).

**Geschätzter Aufwand:** L (3–5 Tage)

---

### Phase 4 – GitHub-Release & Community

#### 4.1 Überarbeitetes README und Projektdokumentation

**Beschreibung:** Das aktuelle README ist gut für Eigengebrauch, aber nicht für externe Nutzer ausgelegt. Für eine GitHub-Veröffentlichung braucht es eine vollständige, einladende Dokumentation.

**Technischer Ansatz:**
- README strukturieren: Badges (Python, License, Stars), Screenshot/Demo-GIF, Feature-Übersicht, Installation (3 Schritte), Konfigurationsreferenz aller Felder, Workflow-Beschreibung, FAQ, Contributing-Hinweise.
- `CHANGELOG.md` für Release-Notes.
- `CONTRIBUTING.md` mit Hinweisen für Pull Requests.
- `.env.example` mit allen möglichen Variablen (inklusive der neuen aus Phase 1–3), ausführlich kommentiert.
- Lizenz-Datei (MIT empfohlen, da kein Geschäftsmodell mit dem Tool).

**Geschätzter Aufwand:** S (1 Tag)

---

#### 4.2 Docker-Unterstützung

**Beschreibung:** Ein `Dockerfile` und eine `docker-compose.yml` ermöglichen das Starten der App ohne Python-Installation – wichtig für breiteren Einsatz.

**Technischer Ansatz:**
- `Dockerfile`: `python:3.12-slim`-Basis, `pip install -r requirements.txt`, `EXPOSE 5001`, `CMD ["python", "app.py"]`.
- `docker-compose.yml`: Volume-Mount für `config.json`, `addressbook.json`, `history.json` (Datenpersistenz außerhalb des Containers).
- `.dockerignore`: `.env`, `__pycache__`, `*.pyc` ausschließen.
- `config.json` als primäre Konfigurationsquelle (kein `.env` im Container nötig) – bereits heute schon der bevorzugte Weg.
- Healthcheck: `HEALTHCHECK CMD curl -f http://localhost:5001/ || exit 1`.

**Geschätzter Aufwand:** S (3–4 Stunden)

---

#### 4.3 Automatisierter Test-Suite

**Beschreibung:** Mindest-Testabdeckung für die kritischen Kernfunktionen: Passwortgenerierung, E-Mail-Aufbau, Provider-Logik (gemockt), Adressbuch-CRUD, History-Schreibfunktion.

**Technischer Ansatz:**
- `pytest` + `pytest-flask` für Route-Tests.
- `unittest.mock` / `responses`-Bibliothek zum Mocken externer APIs (sipgate, Graph API, Nextcloud).
- Verzeichnis `tests/`: `test_app.py`, `test_providers.py`, `test_addressbook.py`.
- GitHub Actions Workflow (`.github/workflows/test.yml`): Tests bei jedem Push auf `main`.

**Geschätzter Aufwand:** M (1–2 Tage)

---

## Ideen für weitere Features

Diese Features sind noch nicht im ursprünglichen Anforderungskatalog, würden das Tool aber deutlich aufwerten:

### Vorlagen-System (Templates)
Häufig verwendete Nachrichten (z.B. "Angebot", "Passwort-Reset", "Rechnung") als Vorlagen speichern und per Klick laden. Gespeichert in `templates.json`, verwaltet über eine eigene `/templates`-Seite.

### Ablauf-Monitoring & automatisches Aufräumen
Cron-Job (via `apscheduler`): Täglich prüfen, welche Freigaben abgelaufen sind, und diese beim Provider automatisch löschen. Optionaler Alert an den Absender vor Ablauf ("Ihr Link für Müller GmbH läuft in 2 Tagen ab").

### Mehrsprachigkeit (i18n)
E-Mail-Templates und SMS in der Sprache des Empfängers senden (Deutsch/Englisch als Start). Sprachfeld im Adressbuch, übersetzbare Strings in einer `translations/`-JSON-Datei.

### Audit-Log (erweitertes History)
Für DSGVO-Dokumentationszwecke: strukturiertes Log aller Aktionen (Versand, Löschung, Einstellungsänderungen) mit Timestamp. Export als CSV.

### Kurzlink-Integration
Den langen Cloud-Share-Link vor dem E-Mail-Versand durch einen internen Kurzlink ersetzen (einfacher Flask-Redirect auf `/r/<token>`). Vorteil: Klicks sind trackbar, Link sieht professioneller aus.

### Zwei-Faktor für die App selbst
Da die App lokal läuft und ggf. bei einem Kunden-Termin auf dem Notebook offen ist: TOTP (z.B. via `pyotp`) als zweiter Faktor für den App-Login, zusätzlich zum Einstellungspasswort.

### Dark Mode
Das UI ist bereits auf einem dunklen Hintergrund (`#0f172a`), das weiße Panel-Design kontrastiert stark. Ein vollständiger Dark Mode für die Panels (Tastatur-Shortcut `Cmd+Shift+D`) wäre ergonomisch bei langer Nutzung.

### Bulk-Versand
CSV-Import einer Empfängerliste: Die gleiche Datei/Nachricht an mehrere Empfänger mit je eigenem Passwort und eigenem Link senden. Jeder Versand wird einzeln in der History protokolliert.

---

## Technische Architektur-Empfehlungen

### Verzeichnisstruktur (Zielzustand nach Phase 3)

```
beseco_sicheres_senden/
├── app.py                    # Flask App, Routen
├── config.json               # Laufzeit-Konfiguration (gitignored)
├── addressbook.json          # Adressbuch-Daten (gitignored)
├── history.json              # Versand-History (gitignored)
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── providers/
│   ├── __init__.py
│   ├── base.py               # StorageProvider + SmsProvider ABCs
│   ├── storage/
│   │   ├── onedrive.py
│   │   ├── nextcloud.py
│   │   ├── dropbox.py
│   │   ├── s3.py
│   │   ├── webdav.py         # generisch (deckt Hetzner, Strato ab)
│   │   ├── google_drive.py
│   │   └── box.py
│   └── sms/
│       ├── sipgate.py
│       ├── twilio.py
│       ├── seven_mobile.py
│       └── vonage.py
├── templates/
│   ├── index.html
│   ├── settings.html
│   ├── contacts.html         # neu
│   └── history.html          # neu
├── static/
│   └── (CSS, Icons bei Bedarf)
└── tests/
    ├── test_app.py
    ├── test_providers.py
    └── test_addressbook.py
```

### Datenhaltung

Für ein lokales Tool ohne Mehrbenutzerbetrieb sind JSON-Dateien ausreichend und haben den Vorteil, dass sie einfach zu sichern, zu lesen und zu migrieren sind. Eine SQLite-Datenbank wäre erst dann sinnvoll, wenn:
- die History sehr groß wird (> 10.000 Einträge),
- mehrere Nutzer gleichzeitig die App verwenden,
- oder komplexe Abfragen (Filter, Sortierung) performant sein müssen.

Empfehlung: JSON für Phase 1–2 behalten, SQLite als optionale Migration in Phase 3 vorsehen (Migrationsskript `migrate_to_sqlite.py`).

### Konfigurationsprinzip beibehalten

Das bestehende Prinzip (`config.json` > Umgebungsvariable > Default) ist gut und sollte beibehalten werden. Es erlaubt sowohl Docker-Deployments (Umgebungsvariablen) als auch lokale Einrichtung (config.json via Einstellungsseite). Einzige Ergänzung: sensible Felder (Passwörter, Tokens) sollten in der Einstellungsseite nie im Klartext zurückgegeben werden (`"****"` stattdessen).

### Fehlerbehandlung verbessern

Aktuell wird im `/send`-Endpoint jede Exception mit `str(e)` zurückgegeben. Für Phase 2+ empfiehlt sich:
- Eigene Exception-Klassen (`ProviderError`, `SmsError`, `ConfigurationError`).
- Unterscheidung zwischen Konfigurationsfehlern (die dem Nutzer helfen, das Problem zu lösen) und transiente Fehler (Netzwerk-Timeout, API-Down).
- Retry-Mechanismus mit kurzem Backoff für transiente Fehler (z.B. 2 Versuche).

### Sicherheitshinweise für die Weiterentwicklung

- `config.json` enthält Klartext-Credentials und sollte nie in Git eingecheckt werden (`.gitignore` prüfen).
- Der Flask `secret_key` in `app.py` hat einen unsicheren Default (`"dev-secret-key"`). Beim ersten Start sollte automatisch ein sicherer Zufallswert generiert und in `config.json` gespeichert werden.
- Bei der Einstellungsseite mit Passwortschutz: Sessions sollten eine begrenzte Lebensdauer haben (z.B. 8 Stunden, `SESSION_LIFETIME` in Config).
- Das generierte Passwort (`generate_password()`) hat mit Länge 12 und dem verwendeten Alphabet ~74 Bit Entropie – das ist ausreichend sicher für den Anwendungsfall.

---

## Provider-Übersicht

### Speicherprovider

| Provider | Status | Freigabe-Link mit Passwort | Notizen |
|---|---|---|---|
| OneDrive (Microsoft 365) | Fertig | Nativ (Graph API) | Erfordert Azure App-Registrierung |
| Nextcloud | Fertig | Nativ (OCS Share API) | Funktioniert mit Self-Hosted und Managed |
| Dropbox | Geplant (Phase 3) | Nativ (Shared Link + Passwort per API) | Dropbox Business erforderlich für Passwort-Links |
| AWS S3 / MinIO | Geplant (Phase 3) | Pre-Signed URL (kein Passwort nativ) | Passwortschutz via lokalem Redirect-Endpoint |
| Hetzner Storage Box | Geplant (Phase 3) | Über generisches WebDAV | Kein nativer öffentlicher Link; URL manuell oder via Samba |
| Google Drive | Geplant (Phase 3) | Nativ (Drive API v3) | OAuth2-Einrichtung aufwendig |
| Box | Geplant (Phase 3) | Nativ (Box API, Shared Links) | Box Business/Enterprise für Passwort-Links |
| SFTP (generisch) | Geplant (Phase 3) | Kein nativer Link | Passwortschutz via lokalem Proxy-Endpoint |
| WebDAV (generisch) | Geplant (Phase 3) | Abhängig vom Server | Deckt Strato HiDrive, Hetzner, eigene Server ab |
| OneDrive Personal | Möglich | Nativ | Erfordert anderen Auth-Flow (Delegated, nicht App) |

### SMS-Provider

| Provider | Status | Besonderheiten | Kosten (ca.) |
|---|---|---|---|
| sipgate | Fertig | Token-basierte Auth, deutsche Nummern | ~0,055 €/SMS (DE) |
| Twilio | Geplant (Phase 3) | Sehr weit verbreitet, gute Doku | ~0,07 €/SMS (DE) |
| 7Mobile (sms77) | Geplant (Phase 3) | Deutsches Unternehmen, einfache HTTP-API | ~0,07 €/SMS (DE) |
| Vonage (Nexmo) | Geplant (Phase 3) | Großer internationaler Anbieter | ~0,07 €/SMS (DE) |
| Brevo (ex Sendinblue) | Idee | Kann auch SMS, wenn SMTP eh Brevo | Paketpreise |
| AWS SNS | Idee | Gut für hohe Volumen | Pay-per-use |

---

*Erstellt: März 2026 · Beseco IT Systems · Florian Beubl*
