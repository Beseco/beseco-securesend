# SecureSend Cloud – Sicherheitsstufen

Diese Dokumentation beschreibt den **aktuellen Produktstatus** von SecureSend Cloud.

## Aktueller Stand (Web-App)

Die Web-App unterstützt produktiv:
- **Stufe 1**
- **Stufe 2**
- **Stufe 3**

**Stufe 4** ist derzeit als Option sichtbar, aber in der Web-App noch nicht produktiv.
Wenn Stufe 4 in der Web-App ausgewählt wird, verarbeitet das Backend den Versand als **Stufe 3** und protokolliert dies im Audit.

## Matrix: Web heute vs. Add-in später

| Stufe | Zweck | Web-App heute | Outlook-Add-in (Roadmap) |
|---|---|---|---|
| Stufe 1 | Sicherer Link | ✅ produktiv | ✅ |
| Stufe 2 | Link + Gastkonto | ✅ produktiv | ✅ |
| Stufe 3 | E2E Dateien + Gastkonto | ✅ produktiv | ✅ |
| Stufe 4 | E2E Dateien + Nachrichtentext + Gastkonto | ⚠️ sichtbar, wird als Stufe 3 verarbeitet | 🚧 geplant (produktive Zielumsetzung) |

## Details je Stufe

### Stufe 1
- Sicherer Link ohne Pflicht-Login
- Optionales Passwort über separaten Kanal (z. B. SMS/Telefon)

### Stufe 2
- Sicherer Link mit Gastkonto
- Optionales Passwort über separaten Kanal

### Stufe 3
- Ende-zu-Ende-Verschlüsselung für Dateien
- Gastkonto erforderlich
- Entschlüsselung im Empfänger-Browser

### Stufe 4
- Zielbild: E2E für Dateien und Nachrichtentext
- Aktuell produktiv nur für den Add-in-Weg vorgesehen
- In der Web-App derzeit Add-in-Hinweis + Downgrade auf Stufe 3

## Konfiguration (Administratoren)

- `allowed_security_levels` kann Stufe 4 enthalten, damit die Option sichtbar bleibt.
- `default_security_level` kann auf Stufe 4 stehen.
- Für Web-Sendungen gilt trotzdem: **effektive Verarbeitung als Stufe 3**, bis der Add-in-Flow freigegeben ist.

## Hinweise zur älteren Dokumentation

Frühere Beschreibungen mit einem 6-Stufen-Modell (`Normal`, `Standard`, `Secure`, `Extended`, `Advanced`, `Maximal`) sind veraltet und wurden durch das aktuelle 4-Stufen-Modell ersetzt.

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
3. **Speicherort** wählen: Kunden-Cloud (z. B. Nextcloud/OneDrive) oder **SecureSend Storage** (gehostet, `securesend_hosted`), gesteuert über die Einstellung **Speicherpräferenz** (`securesend_cloud` / `customer_cloud` / `user_choice`). Details und Betrieb: [Umsetzungsplan-SecureSend-Storage.md](Umsetzungsplan-SecureSend-Storage.md).

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