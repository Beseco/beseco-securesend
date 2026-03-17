"""
core/storage.py — Cloud-Storage Upload & Share-Link (Nextcloud + OneDrive)

Alle Funktionen erhalten die Konfiguration als `cfg: dict`.
Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    import msal as _msal
    _MSAL_AVAILABLE = True
except ImportError:
    _MSAL_AVAILABLE = False

# Interner Cache: (nc_url, nc_user) → interne User-ID (z.B. UUID bei LDAP)
_nc_user_id_cache: dict[tuple, str] = {}


# ── Nextcloud ────────────────────────────────────────────────────────────────

def _nc_user_id(cfg: dict) -> str:
    """Gibt die interne Nextcloud-User-ID zurück (per OCS-API, wird gecacht)."""
    key = (cfg.get("url", ""), cfg.get("user", ""))
    if key in _nc_user_id_cache:
        return _nc_user_id_cache[key]
    try:
        resp = requests.get(
            f"{cfg['url'].rstrip('/')}/ocs/v2.php/cloud/user",
            auth=(cfg["user"], cfg["password"]),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        uid = resp.json()["ocs"]["data"]["id"]
        _nc_user_id_cache[key] = uid
        return uid
    except Exception:
        return quote(cfg.get("user", ""), safe="")


def _nc_webdav_url(cfg: dict, path: str) -> str:
    base = cfg["url"].rstrip("/")
    return f"{base}/remote.php/dav/files/{_nc_user_id(cfg)}/{path.lstrip('/')}"


def nc_ensure_folder(cfg: dict, folder_path: str):
    """Stellt sicher, dass der Ordner (und ggf. Unterordner) existiert."""
    parts = folder_path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        url = _nc_webdav_url(cfg, current)
        resp = requests.request("MKCOL", url,
                                auth=(cfg["user"], cfg["password"]), timeout=15)
        if resp.status_code not in (201, 405, 409):
            resp.raise_for_status()


def upload_to_nextcloud(cfg: dict, filename: str, content: bytes,
                        content_type: str = "text/markdown; charset=utf-8",
                        subfolder: str = "") -> str:
    """Lädt Datei hoch, gibt den Datei-Pfad zurück."""
    base_folder = cfg.get("folder", "SecureSend")
    folder_path = f"{base_folder}/{subfolder}" if subfolder else base_folder
    nc_ensure_folder(cfg, folder_path)
    path = f"{folder_path}/{filename}"
    url = _nc_webdav_url(cfg, path)
    resp = requests.put(
        url,
        auth=(cfg["user"], cfg["password"]),
        data=content,
        headers={"Content-Type": content_type},
        timeout=30,
    )
    resp.raise_for_status()
    return path


def create_nextcloud_share_link(cfg: dict, file_path: str, password: str, days: int) -> str:
    """Erstellt Share via OCS API, gibt die öffentliche URL zurück."""
    base = cfg["url"].rstrip("/")
    api_url = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    expiry_str = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    resp = requests.post(
        api_url,
        auth=(cfg["user"], cfg["password"]),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        data={
            "path":        f"/{file_path}",
            "shareType":   3,
            "permissions": 1,
            "password":    password,
            "expireDate":  expiry_str,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["ocs"]["data"]["url"]
    except (KeyError, TypeError):
        raise RuntimeError(f"Nextcloud Share-URL nicht gefunden: {data}")


# ── OneDrive ─────────────────────────────────────────────────────────────────

def get_graph_token(cfg: dict) -> str:
    """Holt ein Microsoft Graph Access Token via MSAL."""
    if not _MSAL_AVAILABLE:
        raise RuntimeError("msal ist nicht installiert. Bitte 'pip install msal' ausführen.")
    authority = f"https://login.microsoftonline.com/{cfg['tenant_id']}"
    app_msal = _msal.ConfidentialClientApplication(
        cfg["client_id"],
        authority=authority,
        client_credential=cfg["client_secret"],
    )
    result = app_msal.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"MSAL Token-Fehler: {result.get('error_description')}")
    return result["access_token"]


def upload_to_onedrive(cfg: dict, token: str, filename: str, content: bytes,
                       content_type: str = "text/markdown; charset=utf-8",
                       subfolder: str = "") -> str:
    """Lädt Datei hoch, gibt die Item-ID zurück."""
    base_folder = cfg.get("folder", "SecureSend")
    path = f"{base_folder}/{subfolder}/{filename}" if subfolder else f"{base_folder}/{filename}"
    url = (
        f"https://graph.microsoft.com/v1.0/users/{cfg['user']}"
        f"/drive/root:/{path}:/content"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    }
    resp = requests.put(url, headers=headers, data=content, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def create_onedrive_share_link(cfg: dict, token: str, item_id: str,
                               password: str, days: int) -> str:
    """Erstellt passwortgeschützten Freigabe-Link."""
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://graph.microsoft.com/v1.0/users/{cfg['user']}"
        f"/drive/items/{item_id}/createLink"
    )
    payload = {
        "type": "view",
        "scope": "anonymous",
        "password": password,
        "expirationDateTime": expiry,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["link"]["webUrl"]


# ── Dispatcher ───────────────────────────────────────────────────────────────

def upload_and_share(cfg: dict, filename: str, content: bytes,
                     password: str, days: int,
                     content_type: str = "text/markdown; charset=utf-8",
                     subfolder: str = "") -> str:
    """Lädt Datei hoch und gibt passwortgeschützten Link zurück.

    cfg muss enthalten:
      - service: "nextcloud" | "onedrive"
      - Nextcloud: url, user, password, folder
      - OneDrive:  client_id, client_secret, tenant_id, user, folder
    """
    service = cfg.get("service", "nextcloud")

    if service == "nextcloud":
        file_path = upload_to_nextcloud(cfg, filename, content,
                                        content_type=content_type, subfolder=subfolder)
        return create_nextcloud_share_link(cfg, file_path, password, days)

    elif service == "onedrive":
        token = get_graph_token(cfg)
        item_id = upload_to_onedrive(cfg, token, filename, content,
                                     content_type=content_type, subfolder=subfolder)
        return create_onedrive_share_link(cfg, token, item_id, password, days)

    else:
        raise ValueError(f"Unbekannter Storage-Service: {service!r}")


# ── Multi-File Upload + Folder-Share ─────────────────────────────────────────

def upload_files_and_share_folder(
    cfg: dict,
    files: list[tuple[str, bytes, str]],
    folder_path: str,
    password: str,
    days: int,
) -> str:
    """Lädt mehrere Dateien in einen Ordner hoch und gibt einen Freigabe-Link auf den Ordner zurück.

    Args:
        cfg:         Provider-Konfiguration (inkl. service)
        files:       Liste von (filename, content, content_type)
        folder_path: Zielordner-Pfad (relativ zum Upload-Basisordner)
        password:    Passwort für den Share-Link
        days:        Ablauftage
    """
    service = cfg.get("service", "nextcloud")

    if service == "nextcloud":
        nc_ensure_folder(cfg, folder_path)
        for filename, content, content_type in files:
            url = _nc_webdav_url(cfg, f"{folder_path}/{filename}")
            resp = requests.put(
                url,
                auth=(cfg["user"], cfg["password"]),
                data=content,
                headers={"Content-Type": content_type},
                timeout=60,
            )
            resp.raise_for_status()
        return create_nextcloud_share_link(cfg, folder_path, password, days)

    elif service == "onedrive":
        token = get_graph_token(cfg)
        for filename, content, content_type in files:
            upload_to_onedrive(cfg, token, filename, content,
                               content_type=content_type, subfolder=folder_path)
        # OneDrive: Ordner-Share-Link erstellen
        base = cfg.get("url", "https://graph.microsoft.com/v1.0")
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        item_id = r.json()["id"]
        return create_onedrive_share_link(cfg, token, item_id, password, days)

    else:
        raise ValueError(f"Unbekannter Storage-Service: {service!r}")


# ── Status & Quota ────────────────────────────────────────────────────────────

def get_provider_status(cfg: dict) -> dict:
    """Prüft Verbindung und liefert Quota-Informationen.

    Rückgabe:
      {
        "ok": bool,
        "service": str,
        "display_name": str | None,
        "quota": {"used": int, "available": int, "total": int} | None,
        "error": str | None,
      }
    """
    service = cfg.get("service", "nextcloud")
    result: dict = {"ok": False, "service": service, "display_name": None, "quota": None, "error": None}

    if service == "nextcloud":
        try:
            base = cfg["url"].rstrip("/")
            auth = (cfg.get("user", ""), cfg.get("password", ""))

            # Verbindung + User-Info via OCS
            r = requests.get(
                f"{base}/ocs/v2.php/cloud/user",
                auth=auth,
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                timeout=10,
            )
            if r.status_code == 401:
                result["error"] = "Ungültige Zugangsdaten (401)"
                return result
            r.raise_for_status()
            data = r.json()["ocs"]["data"]
            result["display_name"] = data.get("display-name") or data.get("displayname") or data.get("id")

            # Quota via WebDAV PROPFIND
            uid = data.get("id", quote(cfg.get("user", ""), safe=""))
            webdav_url = f"{base}/remote.php/dav/files/{uid}/"
            propfind_body = (
                '<?xml version="1.0"?>'
                '<d:propfind xmlns:d="DAV:">'
                '<d:prop><d:quota-available-bytes/><d:quota-used-bytes/></d:prop>'
                '</d:propfind>'
            )
            rq = requests.request(
                "PROPFIND", webdav_url,
                auth=auth,
                data=propfind_body,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                timeout=10,
            )
            if rq.ok:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(rq.text)
                ns = {"d": "DAV:"}
                avail = root.findtext(".//d:quota-available-bytes", namespaces=ns)
                used  = root.findtext(".//d:quota-used-bytes",      namespaces=ns)
                avail_i = int(avail) if avail and avail.lstrip("-").isdigit() else None
                used_i  = int(used)  if used  and used.lstrip("-").isdigit()  else None
                if used_i is not None:
                    total = (used_i + avail_i) if (avail_i is not None and avail_i >= 0) else None
                    result["quota"] = {
                        "used":      used_i,
                        "available": avail_i if avail_i is not None and avail_i >= 0 else None,
                        "total":     total,
                    }
            result["ok"] = True

        except requests.RequestException as e:
            result["error"] = f"Verbindungsfehler: {e}"

    elif service == "onedrive":
        try:
            token = get_graph_token(cfg)
            r = requests.get(
                "https://graph.microsoft.com/v1.0/me/drive",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            result["display_name"] = d.get("owner", {}).get("user", {}).get("displayName")
            quota = d.get("quota", {})
            used  = quota.get("used")
            total = quota.get("total")
            avail = quota.get("remaining")
            if used is not None:
                result["quota"] = {"used": used, "available": avail, "total": total}
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    else:
        result["error"] = f"Unbekannter Service: {service!r}"

    return result
