# SecureSend Gast-Portal – Anleitung für Empfänger

Willkommen bei SecureSend Cloud! Diese Anleitung erklärt Ihnen alle Funktionen des Empfänger-Portals.

---

## Inhaltsverzeichnis

1. [Was ist SecureSend?](#1-was-ist-securesend)
2. [Sicherheitsstufen im Überblick](#2-sicherheitsstufen-im-überblick)
3. [Erstes Anmelden](#3-erstes-anmelden)
4. [Passwort eingeben](#4-passwort-eingeben)
5. [Konto erstellen](#5-konto-erstellen)
6. [Dashboard nutzen](#6-dashboard-nutzen)
7. [Dateien herunterladen](#7-dateien-herunterladen)
8. [Dateien zurücksenden](#8-dateien-zurücksenden)
9. [Zwei-Faktor-Authentifizierung (2FA)](#9-zwei-faktor-authentifizierung-2fa)
10. [Passwort vergessen](#10-passwort-vergessen)
11. [Sicherheitstipps](#11-sicherheitstipps)

---

## 1. Was ist SecureSend?

SecureSend Cloud ist ein sicherer Dienst zum Senden und Empfangen von:
- 📧 **Nachrichten** (vertrauliche Inhalte)
- 📎 **Dateien** (Dokumente, Bilder, etc.)

Im Gegensatz zu normalen E-Mails bietet SecureSend:
- 🔒 **Verschlüsselung** (je nach Stufe)
- 📱 **SMS-Passwort** (kein offenes Passwort nötig)
- 👤 **2FA** (Zwei-Faktor-Authentifizierung)
- 📊 **Download-Tracking** (Sie sehen, wann etwas heruntergeladen wurde)

---

## 2. Sicherheitsstufen im Überblick

SecureSend nutzt aktuell ein **4-Stufen-Modell**:

| Stufe | Icon | Bedeutung |
|---|---|---|
| **Stufe 1** | 📧 | Sicherer Link |
| **Stufe 2** | 🔑 | Sicherer Link + Gast-Login |
| **Stufe 3** | 🔐 | E2E-Dateien + Gast-Login |
| **Stufe 4** | 🏢 | E2E-Dateien + Text + Gast-Login (derzeit Add-in-only) |

### Wichtiger Produktstatus

- In der Web-App sind Stufe 1–3 produktiv.
- Wenn ein Sender im Web Stufe 4 auswählt, wird der Versand aktuell als Stufe 3 verarbeitet.
- Die produktive Stufe-4-Umsetzung ist für den Outlook-Add-in-Flow vorgesehen.

---

## 3. Erstes Anmelden

Wenn Sie einen SecureSend-Link erhalten, gehen Sie so vor:

### Schritt 1: Link öffnen
Klicken Sie auf den Link in Ihrer E-Mail.

### Schritt 2: Sicherheitsstufe erkennen
Oben auf der Seite sehen Sie die zugehörige Stufe:
- 📧 = Stufe 1
- 🔑 = Stufe 2
- 🔐 = Stufe 3
- 🏢 = Stufe 4 (Add-in-Zielbild)

### Schritt 3: Passwort eingeben (falls erforderlich)
Geben Sie Ihr Passwort ein und klicken Sie auf "Zugang".

---

## 4. Passwort eingeben

Je nach Sicherheitsstufe:

### Fall A: Normal (kein Passwort)
→ Keine Eingabe nötig, Sie kommen direkt zum Dashboard

### Fall B: Standard (selbst gewähltes Passwort)
```
Passwort: [____________]  ← Geben Sie Ihr Passwort ein
                [Zugang]
```

### Fall C: Secure und höher (SMS-Code)
```
Passwort: [____________]  ← Geben Sie den SMS-Code ein
                [Zugang]
```

> 💡 **Tipp**: Der SMS-Code wird automatisch an Ihre Handynummer gesendet.
> Falls Sie keine SMS erhalten, wenden Sie sich an den Absender.

---

## 5. Konto erstellen

Bei Ihrem **ersten Login** werden Sie aufgefordert, ein Konto zu erstellen:

```
┌────────────────────────────────────────┐
│ 🔐 Konto erstellen                     │
├────────────────────────────────────────┤
│ E-Mail: [max@mustermann.de]            │
│                                          │
│ Passwort: [___________________]         │
│ Bestätigen: [___________________]      │
│                                          │
│ [Konto erstellen]                       │
└────────────────────────────────────────┘
```

### Warum ein Konto?
- ✅ **Schnellerer Zugang** bei zukünftigen Nachrichten
- ✅ **2FA** aktivieren für mehr Sicherheit
- ✅ **Dateien zurücksenden** an den Absender

---

## 6. Dashboard nutzen

Nach dem Anmelden sehen Sie das Dashboard:

```
┌─────────────────────────────────────────────────┐
│ 👤 Willkommen, Max Mustermann                   │
├─────────────────────────────────────────────────┤
│ 📧 Betreff: Vertragsentwurf                      │
│ Von: Florian Seubert (florian@firma.de)         │
│Datum: 08.04.2026                               │
├─────────────────────────────────────────────────┤
│ 📝 Nachricht                                  │
│──────────────────────────────────────────────│
│ Hallo Max,                                     │
│ anbei der neue Vertragsentwurf.               │
│ Bitte umプール Stellungnahme bis Freitag.         │
│ LG Florian                                    │
├─────────────────────────────────────────────────┤
│ 📎 Dateien (2)                                │
│──────────────────────────────────────────────│
│ 📄 vertrag.pdf          ⬇️ Download    1.2 MB  │
│ 📊 preisliste.xlsx      ⬇️ Download    500 KB │
├─────────────────────────────────────────────────┤
│ 📤 Eigene Dateien senden                     │
│──────────────────────────────────────────────│
│ [📤 Dateien senden]                          │
└─────────────────────────────────────────────────┘
```

### Dashboard-Bereiche

| Bereich | Erklärung |
|---------|----------|
| **Nachricht** | Der eigentliche Inhalt vom Absender |
| **Dateien** | Angehängte Dokumente zum Herunterladen |
| **Absender** | Wer Ihnen die Nachricht geschickt hat |
| **Dateien senden** | Senden Sie Dateien zurück |

---

## 7. Dateien herunterladen

### So laden Sie Dateien herunter:

1. **Datei finden** in der Dateiliste
2. **auf "⬇️ Download"** klicken
3. Datei wird heruntergeladen

### Unterstützte Dateitypen

| Typ | Formate |
|-----|--------|
| 🖼️ Bilder | JPG, PNG, GIF, WEBP, BMP |
| 📄 Dokumente | PDF, DOCX, XLSX, PPTX |
| 📝 Text | TXT, RTF |
| 📦 Archive | ZIP, 7Z, RAR |

### Download-Tracking

Sie können sehen, wie oft eine Datei heruntergeladen wurde:
- Oben rechts: "X Downloads"

---

## 8. Dateien zurücksenden

Sie können dem Absender auch Dateien schicken:

1. Klicken Sie auf **"📤 Eigene Dateien senden"**
2. Wählen Sie eine oder mehrere Dateien
3. Fügen Sie optional eine Nachricht hinzu
4. Klicken Sie auf "Absenden"

Der Absender erhält eine Benachrichtigung per E-Mail.

---

## 9. Zwei-Faktor-Authentifizierung (2FA)

2FA bietet **zusätzlichen Schutz**:

### Optionen

| Methode | Erklärung |
|---------|----------|
| 📱 **Authenticator App** | Google Authenticator, Authy, etc. |
| ✉️ **E-Mail** | Code per E-Mail |

### 2FA aktivieren

1. Gehen Sie zu **"Registrierung"** (bei Kontoerstellung)
2. Wählen Sie **"Authenticator App"** oder **"E-Mail"**
3. Folgen Sie den Anweisungen

### 2FA verwenden

Bei jedem Login werden Sie nach dem 2FA-Code gefragt:
```
Code: [______]
[Bestätigen]
```

---

## 10. Passwort vergessen

Wenn Sie Ihr Passwort vergessen haben:

### Option A: E-Mail-Reset

1. Klicken Sie auf **"Passwort vergessen"**
2. Wählen Sie **"E-Mail"**
3. Sie erhalten einen Link per E-Mail
4. Klicken Sie auf den Link
5. Geben Sie Ihr neues Passwort ein

### Option B: SMS-PIN

1. Klicken Sie auf **"Passwort vergessen"**
2. Wählen Sie **"SMS"**
3. Sie erhalten eine PIN per SMS
4. Geben Sie die PIN ein
5. Erstellen Sie ein neues Passwort

---

## 11. Sicherheitstipps

### ✅ Empfehlungen

- 🔐 **Starkes Passwort** verwenden (min. 12 Zeichen)
- 📱 **2FA aktivieren** bei vertraulichen Daten
- 🔒 **Passwort merken** und nicht aufschreiben
- 📧 **Link nicht weitergeben**

### ❌ Vermeiden

- Passwort an Dritte weitergeben
- Gleiche Passwörter für mehrere Dienste
- Links öffnen, die Sie nicht erwartet haben
- Unbekannte Dateien ohne Virencheck öffnen

---

## Technischer Support

Bei Fragen oder Problemen:

- 📧 **E-Mail**: support@securesend.cloud
- 📞 **Telefon**: [Kontaktnummer einfügen]
- 🌐 **Web**: https://securesend.cloud

---

## Zusammenfassung

| Aktion | Schritt |
|--------|---------|
| Link öffnen | E-Mail-Link anklicken |
| Passwort | Eingeben + "Zugang" |
| Konto erstellen | (einmalig) E-Mail + Passwort |
| Dateien ansehen | Im Dashboard scrollen |
| Dateien laden | Auf "Download" klicken |
| Dateien senden | "Eigene Dateien senden" |
| 2FA aktivieren | In den Kontoeinstellungen |

---

**Vielen Dank für die Nutzung von SecureSend Cloud!**

*Version: 1.0*
*Datum: April 2026*