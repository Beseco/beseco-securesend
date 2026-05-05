# SecureSend vs. FTAPI SecuMails — Funktionsvergleich

**Hinweis:** Dieser Text vergleicht **Beseco SecureSend Cloud** (Stand: Codebasis dieses Repositories) mit **FTAPI SecuMails**, basierend auf der **öffentlichen Produktbeschreibung** unter [https://www.ftapi.com/plattform/secumails](https://www.ftapi.com/plattform/secumails). Es handelt sich **nicht** um eine unabhängige Produktprüfung, **keinen** Rechtsrat und **keine** Zertifizierungsaussage. Marketingangaben (z. B. Dateigrößen) sind von FTAPI zu verifizieren.

---

## Inhaltsverzeichnis

1. [Kurzfassung](#kurzfassung)
2. [Zielgruppen und Einsatzmodell](#zielgruppen-und-einsatzmodell)
3. [Funktionsmatrix](#funktionsmatrix)
4. [Wo SecureSend bereits stark oder anders stark ist](#wo-securesend-bereits-stark-oder-anders-stark-ist)
5. [Lücken und Optimierungspotenzial (priorisiert)](#lücken-und-optimierungspotenzial-priorisiert)
6. [Fazit](#fazit)
7. [Literatur / Quellen (extern)](#literatur--quellen-extern)

---

## Kurzfassung

| | FTAPI SecuMails (laut Website) | SecureSend Cloud |
|---|-------------------------------|------------------|
| **Positionierung** | Plattform für **sichere E-Mail-Kommunikation** mit Fokus auf **Outlook** und Compliance (DSGVO, NIS-2, TISAX-Erwähnung u. a.) | **Self-hosted** Webanwendung für **sichere Zustellung von Dateien und Nachrichten** über **passwortgeschützte Cloud-Links**, ergänzt um **SMS** (Zweikanal) |
| **Typische Nutzung** | Versand aus **Outlook** oder **Browser**; große „Anhänge“ über Server/Link | Browser-UI **Senden**, **Empfangen** (Upload-Anfragen), **Verlauf**; Speicherung in **Kunden-Cloud** (z. B. Nextcloud, OneDrive) oder **SecureSend Hosted Storage** |
| **Ziel** | „Mit einem Klick“ mehr Sicherheit in der **E-Mail-Kette** | Kontrolle über **Speicherort**, **Mandantenfähigkeit** (Reseller/Organisation) und **konfigurierbare Sicherheitsstufen** |

**Fazit vorweg:** SecureSend ist **kein 1:1-Ersatz** für SecuMails. SecuMails optimiert die **E-Mail-Arbeitsweise** (v. a. Outlook). SecureSend optimiert **sichere Datei-/Nachrichtenzustellung** mit **eigener oder gewählter Cloud** und stärkerer **technischer Souveränität beim Betrieb**.

---

## Zielgruppen und Einsatzmodell

| Aspekt | SecuMails (laut Website/FAQ) | SecureSend Cloud |
|--------|----------------------------|------------------|
| **Betriebsmodell** | SaaS der **FTAPI Software GmbH** („Made in Germany“, ISO-Zertifizierungen werden beworben) | **Eigene Instanz** (Docker, eigene oder gehostete Infrastruktur) |
| **Lizenz / Teilnahme** | FAQ: Für verschlüsselten Versand reicht **eine lizenzierte Seite** (Sender *oder* Empfänger) | **Kein** FTAPI-Vertrag: Betreiber stellt Instanz bereit; **Organisationen** und **Benutzer** werden in SecureSend verwaltet |
| **Mandanten** | Plattform mit Organisationen/Add-ons (Website) | Explizite Hierarchie: **Superadmin → Reseller → Organisation → Nutzer** (`org_admin` / `org_user`) |
| **Primärer Client** | **Outlook-Add-in** (klassisch + Web-Add-in) + **Weboberfläche** | **Webbrowser** (Jinja/Templates); **kein** natives Outlook-Add-in |

---

## Funktionsmatrix

Legende: „SecuMails“ = Aussagen aus der öffentlichen Produktseite / FAQ. „SecureSend“ = implementierter bzw. dokumentierter Stand dieses Projekts.

| Thema | SecuMails (laut Website) | SecureSend Cloud | Kurzkommentar |
|-------|-------------------------|------------------|---------------|
| **Versand aus Outlook** | COM-Add-in und Web-Add-in für **Windows, Mac, mobil & Web** | Nicht vorhanden; Nutzung über **Browser** | Größte UX-Differenz für Outlook-heavy Organisationen |
| **Weboberfläche** | Ja, barrierefreie & moderne UI (Werbetext) | Ja (`/ui/…`) | Beide adressieren Browser-Nutzung |
| **Verschlüsselung** | Standard: **Anhang** verschlüsselt; Stufe 4: auch **E-Mail-Klartext** E2E (laut FAQ) | Mehrere **Sicherheitsstufen** inkl. **ZIP-Verschlüsselung**, **Client-seitigem E2E** („advanced“ / „maximal“) für Hosted; Details: [Sicherheitsstufen.md](Sicherheitsstufen.md) | Unterschiedliche Stufenlogik; SecureSend stark auf **Datei/Ordner** und **getrennte Kanäle** ausgelegt |
| **Große Dateien** | Werbung: bis **100 GB**; Umgehung von Mailbox-Limits durch Ablage und Link in der Mail | Praktisches Limit über **`MAX_UPLOAD_SIZE_MB`** und Infrastruktur; kein vergleichbares Marketing-Limit | Technisch bei SecureSend **hochziehbar**, aber Chunking/Story nicht wie SecuMails vermarktet |
| **Empfänger ohne Konto** | „Kostenlose Gast-Accounts“, sicheres Antworten (Werbetext) | **Token-Links** (`/r/…`), **Registrierung** für Gastkonto, **Gast-Portal** (`/portal/…`), **Upload-Anfragen** ohne SecureSend-Konto | Analoge Idee „externe Person“, andere technische Umsetzung |
| **Einreichung durch Externe (Briefkasten)** | **SubmitBox** — Link, externe reichen ohne Registrierung ein | **Upload-Link / Upload-Anfrage** (siehe README Abschnitt 9) | Funktional verwandt |
| **Zwei-Kanal-Zustellung** | SMS-2FA als **Add-on** für Nutzerkonten (Website) | **Link per E-Mail + Passwort per SMS** als Kernfeature bei Stufen wie „sicher“ | SecureSend: **Zweikanal für Inhalte**, nicht nur für Login |
| **Rückruf / Widerruf** | „Dateifreigabe zurückziehen“ (Feature-Liste) | **Revoke** im Verlauf (Admin/Org-Kontext); **Ablauf** mit `expires_at` | Parallele; Details abhängig von Rolle |
| **Automatische Löschung** | „Richtlinie zur automatischen Löschung“ (Werbetext) | **Hintergrund-Job** nach Ablauf; **vollständige Speicher-Löschung** derzeit **fokussiert auf SecureSend Hosted** — andere Provider: DB-Eintrag/Link-Lebenszyklus vs. Objektlöschung differenzieren (siehe unten) | Lückenschließung: einheitliches **Lifecycle-Delete** für alle Backends |
| **Download-Protokoll / Audit** | „Detaillierte Downloadprotokolle“ | **Download-Logs** und Verlauf in der UI | Ähnliche Absicht |
| **Virenscanner** | **Premium-Virenscanner** als Plattform-Add-on | **ClamAV** optional (`CLAMAV_*` in Konfiguration) | Andere Integration/Tiefe |
| **SSO** | Add-on (ADFS, BundID, G-Suite, …) | **Nicht** als generisches Enterprise-SSO für SecureSend dokumentiert | Lücke für große IT-Abteilungen |
| **2FA für Anwendungsnutzer** | smsTAN, TOTP, E-Mail (Add-on) | **TOTP** u. a. für **App-Benutzer** (je nach Rollout im System) | Teilweise vergleichbar, nicht identisch abgedeckt |
| **Custom Design / Domain / SMTP** | Add-ons Custom Design, Custom Domain, Custom SMTP | **SMTP** pro Organisation/Reseller; **PUBLIC_BASE_URL**; kein ausgereiftes **White-Label-Marktplatz**-Paket wie FTAPI | Branding-Erweiterung möglich, weniger „Produktpaket“ |
| **S/MIME** | Eigenes Add-on, automatische Erkennung ob Empfänger S/MIME nutzt | **Nicht** Bestandteil von SecureSend | Klare strategische Lücke, falls Zielgruppe Zertifikat-Mail will |
| **Trust Networks** | Vertrauensnetzwerk zwischen Organisationen (Add-on) | **Nicht** vorhanden | Lücke für „vertrauenswürdige Partnerstrecke“ |
| **Integrationen** | Outlook im Fokus; weitere Plattform-Integrationen laut Menü | **REST/API** + Templates; Fokus **Cloud-Speicher-Connectoren** in `core/storage.py` | SecureSend: **Speicher-Vielfalt** (Nextcloud, OneDrive, Dropbox, …) statt Mail-Ökosystem |

---

## Wo SecureSend bereits stark oder anders stark ist

1. **Datenhoheit / Speicherwahl**  
   Dateien liegen in der **vom Kunden angebundenen Cloud** oder im **konfigurierbaren Hosted Storage** — nicht zwingend in einer einzigen Anbieter-Cloud wie bei einer klassischen SaaS-Dateiplattform.

2. **SMS als zweiter Kanal für Inhalte**  
   Kombination **E-Mail (Link) + SMS (Passwort)** ist für mehrere Stufen zentral — das unterscheidet SecureSend von rein mail-zentrierten Lösungen.

3. **Multi-Tenant für Dienstleister**  
   **Reseller- und Organisationsmodell** eignet sich für MSPs, Zentralverwaltungen und ähnliche Strukturen.

4. **Sicherheitsstufen-Portfolio**  
   Von „normal“ bis **clientseitischem E2E** mit Nachvollziehbarkeit im Verlauf — für Teams, die **unterschiedliche Risikoklassen** abbilden wollen (siehe [Sicherheitsstufen.md](Sicherheitsstufen.md)).

5. **Empfängerseite**  
   **Gast-Portal**, **Lesebestätigung** (`read_at` wo umgesetzt), **Upload-Anfragen** und **Kontakt-/Telefonnummer-Anfrage** adressieren denselben Problemkreis wie „externe sicher einbinden“ bei SecuMails — mit anderer UX.

---

## Lücken und Optimierungspotenzial (priorisiert)

### Hoch (wahrgenommene Nähe zu SecuMails / Markterwartung)

| Maßnahme | Begründung |
|----------|------------|
| **Outlook-Integration** (Add-in oder klar dokumentierter Workflow) | SecuMails wird primär über **Outlook** verkauft; ohne das bleibt der Medienbruch spürbar. |
| **Einheitliche Lifecycle-Löschung** für **alle** Cloud-Backends nach Ablauf | Wettbewerber versprechen „automatische Löschung“; technisch sollte SecureSend **überall** nachvollziehbar löschen oder die Abweichung **ehrlich in der Doku** begrenzen (derzeit Schwerpunkt **Hosted**). |
| **Optional: E-Mail an Absender bei Lesebestätigung** | Entspricht dem Wunsch nach **Nachweis/Transparenz** ohne Portal-Zwang. |

### Mittel

| Maßnahme | Begründung |
|----------|------------|
| **Große Dateien:** Chunked-Upload, Timeouts, klare **Max-Werte** in Betriebsdoku | SecuMails kommuniziert „sehr groß“ aktiv; SecureSend sollte Erwartungen **operational** klar machen. |
| **Barrierefreiheit / UX-Review** | SecuMails wirbt explizit damit; sinnvolle Verbesserung ohne Feature-Paradewort. |
| **Gast-Posteingang** ohne manuelles **Claim**, sofern datenschutzrechtlich gewollt | Reduziert Reibung gegenüber „alles im Postfach“-Feeling. |

### Niedrig / strategisch

| Maßnahme | Begründung |
|----------|------------|
| **S/MIME** | Nur wenn Zielgruppe **zertifikatsbasierte Mail** will; hoher Implementierungs- und Betriebsaufwand. |
| **Trust-Network-ähnliche Partnerverknüpfung** | Nur bei Bedarf für **regulierten B2B-Austausch** zwischen Organisationen. |
| **Kommerzielle Premium-Scanner-Ökosysteme** | ClamAV reicht vielen Betreibern; Enterprise erwartet ggf. andere SLAs. |

---

## Fazit

- **SecuMails** (laut [ftapi.com/plattform/secumails](https://www.ftapi.com/plattform/secumails)) ist eine **integrierte E-Mail-Sicherheitsplattform** mit **Outlook** als Schwerpunkt, starker **Compliance-Kommunikation** und einem **Add-on-Ökosystem** (SSO, S/MIME, Scanner, …).

- **SecureSend** ist eine **flexibel betreibbare Zustellungslösung** über **Cloud-Speicher und SMS**, mit **Mandantenfähigkeit** und **wählbaren Sicherheitsstufen** — ideal, wenn **Datenstandort**, **eigene Cloud** und **Zweikanal** wichtiger sind als **Outlook-in-App**.

- **Annäherung an das SecuMails-Zielbild** für SecureSend bedeutet vor allem: **Outlook**, **einheitliche Ablauf-/Lösch-Story über alle Speicher**, und **Empfänger- sowie Compliance-Kommunikation** in Doku und UI zu schärfen — nicht zwingend, jedes FTAPI-Add-on nachzubauen.

---

## Literatur / Quellen (extern)

- FTAPI SecuMails Produktseite: [https://www.ftapi.com/plattform/secumails](https://www.ftapi.com/plattform/secumails)

Intern im Repository:

- [README.md](../README.md) — Betrieb und Features
- [Sicherheitsstufen.md](Sicherheitsstufen.md)
- [Umsetzungsplan-SecureSend-Storage.md](Umsetzungsplan-SecureSend-Storage.md)
