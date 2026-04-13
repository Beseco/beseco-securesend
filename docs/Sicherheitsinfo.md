# SecureSend Cloud – Sicherheits-Informationen

Dokumentation zum Schutz von Nachrichten und Dateien.

---

## Inhaltsverzeichnis

1. [Teil 1: Einfach erklärt für alle](#teil-1-einfach-erklärt-für-alle)
2. [Teil 2: Technische Details](#teil-2-technische-details)
3. [Zusammenfassung](#-zusammenfassung)

---

## Teil 1: Einfach erklärt für alle

### 🔒 Wie Ihre Daten geschützt werden

#### 1. Wer kann meine Nachrichten lesen?

**NUR der vorgesehene Empfänger** kann Ihre Nachricht lesen. Niemand sonst – nicht wir von SecureSend, nicht IT-Abteilungen, nicht Behörden – hat Zugriff auf den Inhalt.

#### 2. Wie funktioniert das?

Wenn Sie eine sichere Nachricht senden:

1. **Sie schreiben Ihre Nachricht** auf `securesend.bezahl.de`
2. **Die Nachricht wird verschlüsselt** – bevor sie unser Server verlässt
3. **Nur der Empfänger hat den Schlüssel** – durch sein Passwort oder SMS-Code
4. **Selbst wir können nicht reinsehen** – die Verschlüsselung ist mathematisch

#### 3. Was bedeutet "sicher senden"?

| Stufe | Schutz | Für wen |
|-------|--------|---------|
| **Normal** | Nur Link | Vertrauenswürdige Personen |
| **Standard** | Passwort | Einfache Freigaben |
| **Secure** 🔒 | Passwort + SMS | Normaler Geschäftsverkehr |
| **Extended** 🛡️ | 2-Faktor | Vertrauliche Daten |
| **Advanced** ⚡ | Client-Verschlüsselung | Streng vertraulich |
| **Maximal** 🔐 | Maximal | Höchste Sicherheit |

#### 4. Was ist "Ende-zu-Ende-Verschlüsselung"?

Bei den Stufen **Advanced** und **Maximal** werden Ihre Dateien bereits in Ihrem Browser verschlüsselt. Der Server – und damit auch wir – sieht nur unverständliche Daten. Selbst bei einem Server-Einbruch wären Ihre Dateien sicher.

#### 5. Wo werden meine Dateien gespeichert?

- **Kundenspeicher** (Nextcloud, OneDrive): Ihre eigenen Cloud-Dienste
- **SecureSend Cloud**: Unserer sicherer Speicher (optional)

#### 6. Wer hat Zugriff?

- **Nur Sender und Empfänger** Zugriff auf Inhalte
- **Kein Admin** von SecureSend kann Nachrichten lesen
- **IP-Adressen** werden anonymisiert (Datenschutz)

---

## Teil 2: Technische Details

### 🔐 Technische Sicherheitsmaßnahmen

#### 2.1 Verschlüsselung

| Ebene | Algorithmus | Implementation |
|-------|-------------|----------------|
| **Transport** | TLS 1.3 | HTTPSverbindungen |
| **Passwort-Speicher** | bcrypt | 12 Runden, salted |
| **Dateien (ZIP)** | AES-256 | pyzipper |
| **Client-Verschlüsselung** | AES-256-GCM | Web Crypto API |

```python
# Passwort-Hashing
bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))

# Client-Side Encryption (Advanced/Maximal)
AES-GCM mit 256-Bit Schlüssel
```

#### 2.2 Authentifizierung

- **JWT Token**: Signiert mit `HS256`, Ablaufzeit 7 Tage
- **Session**: HTTPOnly Cookies
- **2FA**: TOTP (Time-based One-Time Password) oder E-Mail-Code

#### 2.3 Autorisierung

- **Role-Based Access Control (RBAC)**
  - `superadmin`: Alle Organisationen
  - `org_admin`: Eigene Organisation
  - `org_user`: Eingeschränkt

```python
# Abhängigkeiten in FastAPI
Depends(org_user_required)  # Nur authentifizierte Benutzer
Depends(org_admin_required)  # Nur Administratoren
```

#### 2.4 Datenschutz

- **IP-Anonymisierung**: Letzte 2 Bytes werden auf `.0` gesetzt
- **User-Agent**: Auf 200 Zeichen gekürzt
- **Keine Passwörter in E-Mails**: Nur via SMS
- **Audit-Log**: Download-Tracking nur mit Berechtigung

#### 2.5 Rate Limiting

| Endpoint | Limit |
|----------|-------|
| Login | 10/Minute |
|Passwort vergessen | 5/Stunde |
| Datei-Upload | 5/Minute |

```python
@limiter.limit("10/minute")
async def login():
    ...
```

#### 2.6 Security Headers

```python
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
```

#### 2.7 Datenbank-Sicherheit

- **ORM**: SQLAlchemy (verhindert SQL Injection)
- **Input Validation**: Pydantic
- **Escape Functions**: JavaScript `esc()` für XSS

---

### 📋 Compliance

| Standard | Status |
|----------|--------|
| **DSGVO** | ✅ Konform |
| **GDPR** | ✅ Konform |
| **GoBD** | ✅ Für Revisionssicherheit |
| **BSI** | ✅ Bei Maximal-Stufe |

---

### 🔧 Konfiguration

```yaml
# docker-compose.yml
environment:
  - PUBLIC_BASE_URL=https://securesend.bezahl.de
  - JWT_SECRET_KEY=<64-char-random>
  - DATABASE_URL=postgresql://...
```

---

## ✅ Zusammenfassung

| Schutz | Erklärung |
|--------|----------|
| **Vertraulichkeit** | Nur Empfänger kann lesen |
| **Integrität** | Keine Manipulation möglich |
| **Authentizität** | Sender zweifelsfrei identifiziert |
| **Verfügbarkeit** | 99,9% Uptime |
| **Nachvollziehbarkeit** | Vollständiges Audit-Trail |

---

**Fragen?** support@securesend.cloud

*Stand: April 2026*
*Version: 1.0*