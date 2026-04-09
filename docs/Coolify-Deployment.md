# Coolify Deployment Anleitung

## Option 1: Git-Repository (Empfohlen)

### Schritt 1: Git-Repository vorbereiten
1. Git-Repository erstellen (GitHub/GitLab/selbstgehostet)
2. Code pushen:
```bash
git remote add origin <your-repo>
git push -u origin dev
```

### Schritt 2: Coolify App erstellen
1. **Coolify Dashboard** → **Create New App**
2. **Repository** auswählen
3. **Branch**: `dev`
4. **Build Pack**: `Dockerfile` (oder `docker-compose`)

### Schritt 3: Umgebungsvariablen setzen

In Coolify unter **Environment Variables**:

```env
# Datenbank
DATABASE_URL=postgresql://user:password@host:5432/securesend

# Sicherheit
SECRET_KEY=<64-zeichen-random-string>
PUBLIC_BASE_URL=https://deine-domain.de

# SMTP (für E-Mails)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your@email.de
SMTP_PASSWORD=yourpassword

# SMS (sipgate)
SIPGATE_TOKEN_ID=token-xxxxx
SIPGATE_DEVICE_ID=s0
```

### Schritt 4: Docker Compose konfigurieren

Coolify erkennt `docker-compose.yml` automatisch.

**Wichtig**: `docker-compose.yml` anpassen:
```yaml
services:
  securesend:
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - SECRET_KEY=${SECRET_KEY}
      # ... andere Variablen
```

---

## Option 2: Docker Image (Manuel)

### Schritt 1: Image bauen
```bash
docker build -t your-registry/securesend:latest .
docker push your-registry/securesend:latest
```

### Schritt 2: In Coolify als "Docker Image" deployen
- **Image**: `your-registry/securesend:latest`
- **Registry Credentials**: Falls privat

---

## Empfohlene Coolify Settings

| Setting | Wert |
|---------|------|
| **Build Pack** | Docker |
| **HTTP Port** | 8001 |
| **Health Check** | `/health` |
| **Timeout** | 60s |
| **Pre-deploy** | `docker compose down` |

---

## Datenbank

### Option A: Coolify PostgreSQL (Einfach)
1. **Create New Resource** → **Database** → **PostgreSQL**
2. URL kopieren → in Environment Variables

### Option B: Externe Datenbank
Beliebigen PostgreSQL-Host verwenden.

---

## Domain

1. **Domains** → **Add Domain**
2. SSL wird automatisch via Let's Encrypt

---

## Troubleshooting

### Fehler: "Database connection failed"
→ `DATABASE_URL` prüfen

### Fehler: "SECRET_KEY not set"
→ 64-Char random key generieren:
```bash
openssl rand -hex 32
```

### Fehler: "Container restart loop"
→ Logs prüfen in Coolify

---

## Schnellstart (GitHub → Coolify)

```bash
# 1. Code zu GitHub pushen
git add .
git commit -m "Coolify ready"
git push origin dev

# 2. In Coolify
# Create App → GitHub → wähle Repo → Branch: dev
# Environment Variables setzen
# Deploy klicken
```

---

Brauchst du Hilfe bei einem bestimmten Schritt?