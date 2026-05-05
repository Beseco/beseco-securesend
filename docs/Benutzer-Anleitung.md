# SecureSend Cloud – Anleitung für Organisations-Benutzer

Willkommen bei SecureSend Cloud! Diese Anleitung erklärt alle Funktionen für Benutzer Ihrer Organisation.

---

## Inhaltsverzeichnis

1. [Erste Schritte](#1-erste-schritte)
2. [Anmelden](#2-anmelden)
3. [Nachricht senden](#3-nachricht-senden)
4. [Dateien senden](#4-dateien-senden)
5. [Sicherheitsstufen](#5-sicherheitsstufen)
6. [Kontakte verwalten](#6-kontakte-verwalten)
7. [Verlauf und Downloads](#7-verlauf-und-downloads)
8. [Dateien zurückrufen](#8-dateien-zurückrufen)
9. [Organisationseinstellungen](#9-organisationseinstellungen)
10. [Selbstregistrierung](#10-selbstregistrierung)

---

## 1. Erste Schritte

### Zugang erhalten
Sie erhalten eine E-Mail mit:
- 📧 **Link** zur Anmeldung
- 👤 **E-Mail-Adresse**
- 🔑 **Einmal-Passwort** (bei Erstlogin)

### Erstes Passwort setzen
1. Link in der E-Mail anklicken
2. Neues Passwort eingeben (min. 8 Zeichen)
3. Bestätigen
4. Angemeldet!

---

## 2. Anmelden

### Normale Anmeldung

1. **Öffnen**: `https://securesend.ihre-domain.de`
2. **E-Mail eingeben**
3. **Passwort eingeben**
4. **Anmelden** klicken

### Passwort ändern
- Klick auf **Schlüssel-Icon** unten links
- Altes Passwort eingeben
- Neues Passwort eingeben
- Bestätigen

---

## 3. Nachricht senden

### Schritt-für-Schritt

1. **Dashboard** → **Senden** klicken

2. **Empfänger wählen**
   - Aus Kontakten auswählen ODER
   - E-Mail + Name manuell eingeben

3. **Betreff** eingeben
   - Beispiel: "Vertragsentwurf"

4. **Nachricht verfassen**
   - Text-Editor nutzen
   - Markdown möglich
   - Dateien anhängen (optional)

5. **Sicherheitsstufe wählen** ⬇️

6. **Absenden** klicken

---

## 4. Dateien senden

### Option A: Direkt im Send-Formular
```
Dateien: [Datei auswählen]    oder    Dateien hierher ziehen
```

### Option B: Aus Kontakt
1. Kontakt öffnen
2. ** Nachricht senden** klicken
3. Dateien anhängen

### Unterstützte Dateitypen

| Typ | Formate |
|-----|--------|
| 🖼️ Bilder | JPG, PNG, GIF, WEBP |
| 📄 Dokumente | PDF, DOCX, XLSX, PPTX |
| 📝 Text | TXT, RTF |
| 📦 Archive | ZIP (max. 5GB) |

### Blockierte Dateitypen
```
❌ .exe  ❌ .bat  ❌ .cmd  ❌ .msi
❌ .ps1  ❌ .vbs  ❌ .jar  ❌ .scr
❌ .dll  ❌ .hta  ❌ .lnk  ❌ .js
```

---

## 5. Sicherheitsstufen

Beim Senden gibt es aktuell ein **4-Stufen-Modell**:

| Stufe | Icon | Beschreibung | Status in Web-App |
|---|---|---|---|
| **Stufe 1** | 📧 | Sicherer Link | ✅ produktiv |
| **Stufe 2** | 🔑 | Link + Gast-Login | ✅ produktiv |
| **Stufe 3** | 🔐 | E2E-Dateien + Gast-Login | ✅ produktiv |
| **Stufe 4** | 🏢 | E2E-Dateien + Text + Gast-Login | ⚠️ Add-in-only, Web verarbeitet als Stufe 3 |

### Praktischer Hinweis

- Wenn Sie in der Web-App **Stufe 4** wählen, zeigt das System einen Hinweis und verarbeitet den Versand derzeit als **Stufe 3**.
- Die produktive Stufe-4-Umsetzung ist für das Outlook-Add-in vorgesehen.

---

## 6. Kontakte verwalten

### Kontakt anlegen
1. **Kontakte** → klicken
2. **+ Neuer Kontakt** → klicken
3. Daten eingeben:
   - Vorname
   - Nachname
   - E-Mail-Adresse
   - Handy (für SMS-Passwort)
4. **Speichern**

### Kontakt bearbeiten
1. Kontakt in der Liste anklicken
2. **Bearbeiten** klicken
3. Änderungen eingeben
4. **Speichern**

### Kontakt löschen
1. Kontakt auswählen
2. **Löschen** klicken
3. Bestätigen

### VCF-Import/A-Export
- **Exportieren**: Kontakte → oben rechts → "Als vCard exportieren"
- **Importieren**: Kontakte → Importieren → Datei auswählen

---

## 7. Verlauf und Downloads

### Verlauf ansehen
1. **Verlauf** → klicken
2. Liste aller gesendeten Nachrichten
3. Filter nach Datum, Empfänger, Stufe

### Download-Logs
- Eintrag in der Liste anklicken
- **Download-Protokoll** zeigt:
  - Wann heruntergeladen
  - Von welcher IP
  - Wie oft

### Beispiel-Log:
```
📥 Downloads für "Angebot.pdf"
├── 08.04.2026 14:32 - max@beispiel.de (IP: 212....)
├── 08.04.2026 15:45 - anna@beispiel.de (IP: 212....)
└── Gesamt: 2 Downloads
```

---

## 8. Dateien zurückrufen

### Einen Link zurückrufen
1. **Verlauf** öffnen
2. Eintrag suchen
3. **Zurückrufen** klicken

### Was passiert?
- ⛔ Empfänger kann nicht mehr zugreifen
- 📝 im Verlauf als "zurückgerufen" markiert
- 📧 Empfänger erhält Info-Mail (optional)

### Wiederherstellen
- Selber Weg, aber **"Wiederherstellen"** klicken

---

## 9. Organisationseinstellungen

*Als Org-Admin verfügbar*

### Benutzer
- Benutzer anlegen
- Rollen zuweisen (org_user / org_admin)
- Deaktivieren

### Cloud-Speicher
- **Nextcloud** konfigurieren
- **OneDrive** konfigurieren
- **SecureSend Cloud** (eigener Speicher)

### SMS-Gateway
- **sipgate** anschließen
- Kontostand prüfen

### SMTP
- E-Mail-Server konfigurieren
- Absender-E-Mail festlegen

### Sicherheitsstufen
```
Erlaubte Stufen:
☑ Normal
☑ Standard  
☑ Secure (Standard)
☑ Extended
☑ Advanced
☐ Maximal

Standard-Stufe: Secure
```

---

## 10. Selbstregistrierung

*Als Org-Admin konfigurierbar*

### Aktivieren
1. **Organisation** → **Einstellungen**
2. **Selbstregistrierung**: An/Aus
3. **Domain-Einschränkung**: z.B. `@firma.de`

### Registrierungslink teilen
- Link kopieren
- Mitarbeitern geben

### Ablauf für Mitarbeiter
1. Link öffnen
2. Daten eingeben
3. Bestätigungs-E-Mail
4. Konto aktiv

---

## Kurzübersicht

| Aktion | Wo |
|--------|-----|
| Anmelden | /ui/login |
| Senden | /ui/send |
| Kontakte | /ui/contacts |
| Verlauf | /ui/history |
| Einstellungen | /ui/admin/org |

---

## Hilfe

**Support:**
- 📧 E-Mail: support@securesend.cloud
- 📞 Telefon: [Kontaktnummer]
- 🌐 Web: https://securesend.cloud

---

*Version: 1.0*
*Datum: April 2026*