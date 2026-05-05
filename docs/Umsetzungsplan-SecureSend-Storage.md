# Umsetzungsplan: SecureSend Storage (Ist-Stand & Betrieb)

Dieses Dokument beschreibt das Zielbild, die umgesetzten Phasen und den Betrieb des **gehosteten Speichers** („SecureSend Storage“). Technischer Anbieter-Typ in der Datenbank: `securesend_hosted` (ein Eintrag pro Organisation). Das tatsächliche Backend (lokales Dateisystem oder S3-kompatibel) wird **nur serverseitig** über Umgebungsvariablen gesteuert — keine Secrets im Org-`config_json`.

## Inhaltsverzeichnis

1. [Zielbild und Nicht-Ziele](#zielbild-und-nicht-ziele)
2. [Architektur (kurz)](#architektur-kurz)
3. [Umgebungsvariablen](#umgebungsvariablen)
4. [Phasen (Umsetzung)](#phasen-umsetzung)
5. [Betrieb](#betrieb)
6. [Risiken / Produktentscheidungen](#risiken--produktentscheidungen)
7. [Verwandte Doku](#verwandte-doku)

## Zielbild und Nicht-Ziele

**Ziel:** Mehrdatei-Upload, Ordner-Metapher, E2E-Abruf und alle Sicherheitsstufen können über den gehosteten Speicher laufen; Kontingent pro Organisation (Standard 5 GB) mit Anzeige in der Admin-Oberfläche; Reseller können Standard-Kontingent und benannte Stufen (inkl. `price_hint` als reine Anzeige) pflegen.

**Nicht-Ziele (v1):** Keine Anbindung an Zahlungs- oder Abrechnungssysteme. `price_hint` und Stufen sind **Konfiguration/Anzeige** nur.

## Architektur (kurz)

- **Speicher-Schicht:** `core/hosted_storage.py` — Upload/Download für `local` und `s3` (boto3).
- **Integration:** `core/storage.py` verzweigt für `service == securesend_hosted` in Upload- und Download-Pfaden.
- **Cloud:** `cloud/hosted_cfg.py` (`merge_hosted_storage_cfg`), `cloud/services/hosted_provider.py` (virtueller Provider, Quota-Auflösung, `resolve_send_cloud_provider` gemäß `storage_preference`).
- **Links:** Nach erfolgreichem Send wird `share_url` auf den Tracking-Link (`/track/l/...`) gesetzt, nicht auf direktes Fileserving.

## Umgebungsvariablen

| Variable | Bedeutung | Beispiel |
|----------|-----------|----------|
| `SECURESEND_STORAGE_ENABLED` | Gehosteten Speicher aktivieren | `true` |
| `SECURESEND_STORAGE_BACKEND` | `local` oder `s3` | `local` |
| `SECURESEND_STORAGE_ROOT` | Basisverzeichnis (nur `local`) | `/data/hosted` |
| `SECURESEND_S3_ENDPOINT` | S3/MinIO-Endpoint | `https://minio:9000` |
| `SECURESEND_S3_BUCKET` | Bucket-Name | `securesend` |
| `SECURESEND_S3_ACCESS_KEY` | Zugangsschlüssel | — |
| `SECURESEND_S3_SECRET_KEY` | Geheimer Schlüssel | — |
| `SECURESEND_S3_REGION` | Region | `us-east-1` |

Siehe auch `cloud/.env.example` und `docker-compose.yml`.

## Phasen (Umsetzung)

### Phase 1 — Lokaler gehosteter Speicher

- Mehrdatei-Upload und Download über Dateisystem unter `SECURESEND_STORAGE_ROOT/{org_id}/…`.
- Virtueller `CloudProvider` „SecureSend Storage“ (`securesend_hosted`), Send- und E2E/Tracking-Pfade angebunden.
- Beim Start legt die App bei `backend=local` das Wurzelverzeichnis an (`cloud/main.py`).

### Phase 2 — S3-kompatibel

- `SECURESEND_STORAGE_BACKEND=s3` mit denselben Außenfunktionen.
- Abhängigkeit: `boto3` in `cloud/requirements.txt` (Docker-Image installiert Requirements).

### Phase 3 — `storage_preference` und UI

- `storage_preference` in Org-Einstellungen: `securesend_cloud`, `customer_cloud`, `user_choice`.
- Send-Seite filtert die Anbieterliste entsprechend (`cloud/templates/send.html`).
- Auflösung in `send` / `public` / `guest` über `resolve_send_cloud_provider` bzw. gleiche Regeln.

### Phase 4 — Quota 5 GB, Nutzung, Admin

- `storage_quota_bytes`, `storage_used_bytes` in `organizations.settings_json` (Defaults beim Anlegen).
- Vor Upload: Prüfung `used + Größe <= quota`.
- Status/Quota für gehosteten Anbieter über `/admin/org/providers/{id}/status` und Karten in `admin_org.html`.

### Phase 5 — Reseller-Stufen

- In `reseller.settings_json`: `default_org_quota_gb`, `storage_tiers` (`id`, `name`, `quota_gb`, optional `price_hint`).
- Org: `storage_tier_id` oder explizites `storage_quota_bytes` (Priorität siehe `resolve_storage_quota_bytes` in `hosted_provider.py`).
- Pflege im Reseller-UI unter „Einstellungen“ (`admin_reseller_cp.html`).

## Betrieb

**Backups:** Bei `local` das Verzeichnis `SECURESEND_STORAGE_ROOT` in die Backup-Strategie einbeziehen (konsistent mit DB nach Möglichkeit).

**Isolation:** Pfade sind pro `org_id` getrennt; keine öffentlichen Datei-URLs ohne App (Zugriff über Tracking/E2E-Flow).

**Migration FS → S3:** Operatives Projekt: Objekte mit gleicher Key-Struktur in den Bucket legen, Backend auf `s3` umstellen, Altbestand validieren. Kein zwingender automatisierter Migrationsjob in dieser Ausbaustufe.

## Risiken / Produktentscheidungen

- **Öffentliche URLs:** Nur über signierte/Tracking-Pfade, kein anonymes Directory-Listing.
- **`storage_used_bytes`:** Wird bei Upload erhöht; Abgleich bei Löschung/Revoke kann später per Job nachgezogen werden.
- **Mehrere Kunden-Clouds:** Standard-Logik und `storage_preference` legen fest, wann gehosteter Speicher Standard ist.

## Verwandte Doku

- [`README.md`](../README.md) — Installation und Umgebung
- [`docs/Sicherheitsstufen.md`](Sicherheitsstufen.md) — Sicherheitsstufen (bei Bedarf um SecureSend Storage ergänzen)
