# SecureSend Cloud – Sicherheitsstufen

Diese Dokumentation erklärt die 6 Sicherheitsstufen von SecureSend Cloud.

---

## Übersicht

| Stufe | Icon | Verschlüsselung | Passwort | SMS | 2FA | Client-Verschlüsselung |
|------|------|---------------|----------|-----|-----|---------------------|
| Normal | 📧 | TLS | ❌ | ❌ | ❌ | ❌ |
| Standard | 🔑 | TLS | ✓ (selbst) | ❌ | ❌ | ❌ |
| Secure | 🔒 | TLS | ✓ | ✓ | ❌ | ❌ |
| Extended | 🛡️ | TLS | ✓ | ✓ | ✓ | ❌ |
| Advanced | ⚡ | TLS + E2E | ✓ | ✓ | ✓ | ✓ |
| Maximal | 🔐 | TLS + E2E | ✓ | ✓ | ✓ | ✓ + Video |

---

## Detailierte Erklärung

### 🔵 Stufe 1: Normal 📧

**Einsatzbereich:**
- Interne Abstimmungen
- Unkritische Dokumente
- Informationen, die keinen Schutz benötigen

**Technische Details:**
- Transport-Verschlüsselung: TLS
- Keine Authentifizierung erforderlich
- Keine Protokollierung des Empfängers

**Vorteile:**
- 🚀 Schnellster Zugang
- 👥 Kein Aufwand für Empfänger

**Nachteile:**
- ⚠️ Kein Nachweis über Empfang
- ⚠️ Kein Schutz bei Weiterleitung

---

### 🟢 Stufe 2: Standard 🔑

**Einsatzbereich:**
- Einfache Freigaben
- Interne Dokumente mit Zugriffskontrolle
- Wenn kein Handy verfügbar

**Technische Details:**
- Transport-Verschlüsselung: TLS
- Passwort: Manuell vereinbart (nicht automatisch)
- Keine SMS

**Vorteile:**
- 🔒 Einfache Zugriffskontrolle
- 📞 Kein Handy nötig

**Nachteile:**
- ⚠️ Passwort muss separat übermittelt werden
- ⚠️ Kein SMS-Beleg

---

### 🟡 Stufe 3: Secure 🔒 (EMPFOHLEN)

**Einsatzbereich:**
- Standard-Geschäftskommunikation
- Verträge und Vereinbarungen
- Kunden- und Partnernachrichten

**Technische Details:**
- Transport-Verschlüsselung: TLS
- Passwort: Automatisch generiert + per SMS
- Einmal-Passwort (One-Time-Password)

**Vorteile:**
- 📱 Sicherer Nachweis (SMS an Handy)
- 🔐 Automatische Generierung
- 📊 Protokollierung

**Empfehlung:**
> Für die meisten Geschäftsfälle ist **Secure** die beste Wahl.

---

### 🟠 Stufe 4: Extended 🛡️

**Einsatzbereich:**
- Vertrauliche Daten
- Personalakten, Gehaltsdaten
- Strategische Informationen

**Technische Details:**
- Transport-Verschlüsselung: TLS
- Passwort: Per SMS
- Zusätzlicher 2FA (E-Mail oder App)

**Vorteile:**
- 🛡️ Doppelte Absicherung
- 📧 2FA für hohe Sicherheit

**Anwendung:**
- Wenn Unternehmen 2FA vorschreibt
- Bei besonders sensiblen Daten

---

### 🔴 Stufe 5: Advanced ⚡

**Einsatzbereich:**
- Streng vertrauliche Informationen
- Geistiges Eigentum
- Fusionen & Übernahmen

**Technische Details:**
- Transport-Verschlüsselung: TLS
- **End-to-End-Verschlüsselung** (im Browser)
- Dateien werden **auf dem Client verschlüsselt**
- Erst Entschlüsselung im Browser des Empfängers

**Vorteile:**
- 🔐 Selbst bei Cloud-Kompromittierung sicher
- 🖥️ Server sieht keine Dateiinhalte
- 📱 Mobile-fähig

**Technische Implementation:**
```
[Sender-Browser] → AES-256-GCM verschlüsselt → [Cloud] → [Empfänger-Browser] → entschlüsselt
        ↑                              ↑
    Client-seitig              Client-seitig
```

---

### ⚫ Stufe 6: Maximal 🔐

**Einsatzbereich:**
- Höchste Sicherheitsanforderungen
- Regulatorisch geschützte Daten
- Behördliche Anforderungen

**Technische Details:**
- Alles aus Advanced
- Zusätzliche Verifikation (z.B. Video-Call)
- Audit-Protokollierung
- Zeitlich begrenzter Zugriff

**Zusätzliche Funktionen:**
- 📹 Video-Verifikation möglich
- ⏰ Zeitlich begrenzte Links
- 📋 Vollständiges Audit-Trail

---

## Vergleichstabelle

| Feature | Normal | Standard | Secure | Extended | Advanced | Maximal |
|---------|--------|----------|--------|----------|----------|--------|
| TLS | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Passwort | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| SMS-Code | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |
| 2FA | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Client-Verschl. | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Video-Verify | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Audit Trail | Basis | Basis | Basis | Vollst. | Vollst. | Vollst. + |
| Zeitbegrenzung | ❌ | ❌ | ❌ | Optional | Optional | ✅ |

---

## Für wen welche Stufe?

### Privatpersonen
- **Normal** oder **Standard**: Für private Fotos, einfache Dokumente

### Kleinunternehmen
- **Standard**: Für alltägliche Kommunikation
- **Secure**: Für Kundenanfragen

### Mittelstand
- **Secure** (Standard): Für die meiste Kommunikation
- **Extended**: Für vertrauliche Daten
- **Advanced**: Für Verträge, Angebote

### Großunternehmen / Behörden
- **Secure** oder **Extended**: Standard
- **Advanced**: Streng vertraulich
- **Maximal**: Regulatorisch geschützt

---

## Technische Details

### Verschlüsselung

**Transport (TLS):**
- Alle Verbindungen sind TLS-verschlüsselt
- Zertifikat auf dem Server

**End-to-End (Advanced/Maximal):**
- AES-256-GCM im Browser
- Schlüssel wird nie übertragen
- Nur Sender und Empfänger können entschlüsseln

### Passwort-Flow

```
Secure/Extended/Advanced/Maximal:
┌──────────────────┐     ┌──────────────────┐
│   Sender        │     │  SecureSend     │
│  erstellt      │────▶│  Server        │
│  Nachricht     │     │  (speichert)    │
└──────────────────┘     └──────────────────┘
                                    │
                  ┌─────────────────┘
                  ▼
         ┌────────────────────┐
         │  E-Mail an      │
         │  Empfänger     │
         │  (Link)       │
         └────────────────────┘
                  │
                  ▼
         ┌────────────────────┐
         │  Empfänger        │
         │  öffnet Link    │
         └────────────────────┘
                  │
    OTP wird per SMS gesendet
                  │
                  ▼
         ┌────────────────────┐
         │  Passwort + OTP   │
         │  eingeben       │────▶ Dashboard
         └────────────────────┘
```

### Compliance

| Anforderung | Normal | Standard | Secure | Extended | Advanced | Maximal |
|-----------|--------|----------|--------|----------|--------|--------|
| DSGVO | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅ |
| GoBD | ❌ | ❌ | ⚠️ | ✅ | ✅ | ✅ |
| BSI | ❌ | ❌ | ❌ | ⚠️ | ✅ | ✅ |
| GDPR | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅ |

---

## Konfiguration (für Administratoren)

In der Admin-Oberfläche können Sie:

1. **Erlaubte Stufen** festlegen
2. **Standard-Stufe** definieren
3. **Speicherort** wählen (Nextcloud/OneDrive/SecureSend)

```
 организации → Einstellungen → Sicherheitsstufen
 ├── ☑ Normal
 ├── ☑ Standard  
 ├── ☑ Secure (Standard)
 ├── ☑ Extended
 ├── ☑ Advanced
 └── ☐ Maximal
```

---

*Version: 1.0*
*Datum: April 2026*