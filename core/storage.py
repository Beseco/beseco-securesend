"""
core/storage.py — Cloud-Storage Upload & Share-Link
Unterstützte Anbieter: Nextcloud, OneDrive, Dropbox, HiDrive, Synology Drive

Alle Funktionen erhalten die Konfiguration als `cfg: dict`.
Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import base64
import json as _json
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional
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


def create_nextcloud_share_link(cfg: dict, file_path: str, password: Optional[str], days: int) -> str:
    """Erstellt Share via OCS API, gibt die öffentliche URL zurück.
    Wird password=None übergeben, wird kein Passwortschutz gesetzt."""
    base = cfg["url"].rstrip("/")
    api_url = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    expiry_str = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    data: dict = {
        "path":        f"/{file_path}",
        "shareType":   3,
        "permissions": 1,
        "expireDate":  expiry_str,
    }
    if password:
        data["password"] = password

    resp = requests.post(
        api_url,
        auth=(cfg["user"], cfg["password"]),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        data=data,
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


# ── Dropbox ──────────────────────────────────────────────────────────────────

def _dropbox_token(cfg: dict) -> str:
    """Holt ein frisches Dropbox Access-Token via Refresh-Token."""
    app_key    = cfg.get("app_key", "")
    app_secret = cfg.get("app_secret", "")
    refresh    = cfg.get("refresh_token", "")

    if refresh and app_key and app_secret:
        resp = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            auth=(app_key, app_secret),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    # Fallback: direkt gespeicherter (long-lived) Access-Token
    token = cfg.get("access_token", "")
    if not token:
        raise RuntimeError("Dropbox: kein access_token oder refresh_token konfiguriert")
    return token


def _dropbox_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload_to_dropbox(cfg: dict, token: str, filename: str, content: bytes,
                      subfolder: str = "") -> str:
    """Lädt Datei zu Dropbox hoch. Gibt den Dropbox-Pfad zurück."""
    base = cfg.get("folder", "/SecureSend").rstrip("/")
    path = f"{base}/{subfolder}/{filename}" if subfolder else f"{base}/{filename}"

    resp = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            **_dropbox_headers(token),
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": _json.dumps({
                "path": path,
                "mode": "add",
                "autorename": True,
            }),
        },
        data=content,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["path_display"]


def create_dropbox_share_link(cfg: dict, token: str, path: str, days: int) -> str:
    """Erstellt einen Dropbox-Freigabe-Link (kein Passwortschutz via API)."""
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.post(
        "https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings",
        headers={**_dropbox_headers(token), "Content-Type": "application/json"},
        json={"path": path, "settings": {"requested_visibility": "public", "expires": expiry}},
        timeout=30,
    )
    if resp.status_code == 409:
        # Link existiert bereits → abrufen
        r2 = requests.post(
            "https://api.dropboxapi.com/2/sharing/list_shared_links",
            headers={**_dropbox_headers(token), "Content-Type": "application/json"},
            json={"path": path, "direct_only": True},
            timeout=15,
        )
        r2.raise_for_status()
        links = r2.json().get("links", [])
        if links:
            # dl=0 → dl=1 für Direktdownload
            url = links[0]["url"]
            return url.replace("?dl=0", "?dl=1")
    resp.raise_for_status()
    url = resp.json()["url"]
    return url.replace("?dl=0", "?dl=1")


# ── HiDrive ───────────────────────────────────────────────────────────────────

def _hidrive_webdav_url(cfg: dict, path: str) -> str:
    username = cfg.get("username") or cfg.get("user", "")
    return f"https://webdav.hidrive.strato.com/users/{quote(username, safe='')}/{path.lstrip('/')}"


def _hidrive_auth(cfg: dict):
    """Basic-Auth-Tupel für HiDrive."""
    return (cfg.get("username") or cfg.get("user", ""), cfg.get("password", ""))


def hidrive_ensure_folder(cfg: dict, folder_path: str):
    """Stellt sicher, dass der Ordner auf HiDrive existiert (MKCOL)."""
    parts = folder_path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        url = _hidrive_webdav_url(cfg, current)
        resp = requests.request("MKCOL", url, auth=_hidrive_auth(cfg), timeout=15)
        if resp.status_code not in (201, 405, 409):
            resp.raise_for_status()


def upload_to_hidrive(cfg: dict, filename: str, content: bytes,
                      content_type: str = "application/octet-stream",
                      subfolder: str = "") -> str:
    """Lädt Datei zu HiDrive via WebDAV hoch. Gibt den Pfad zurück."""
    base = cfg.get("folder", "SecureSend")
    folder = f"{base}/{subfolder}" if subfolder else base
    hidrive_ensure_folder(cfg, folder)
    path = f"{folder}/{filename}"
    url = _hidrive_webdav_url(cfg, path)
    resp = requests.put(
        url,
        auth=_hidrive_auth(cfg),
        data=content,
        headers={"Content-Type": content_type},
        timeout=60,
    )
    resp.raise_for_status()
    return path


def create_hidrive_share_link(cfg: dict, file_path: str, password: Optional[str], days: int) -> str:
    """Erstellt einen HiDrive-Freigabe-Link via REST-API."""
    username = cfg.get("username") or cfg.get("user", "")
    full_path = f"/users/{username}/{file_path.lstrip('/')}"
    creds = base64.b64encode(f"{username}:{cfg.get('password', '')}".encode()).decode()

    payload: dict = {
        "path": full_path,
        "maxcount": -1,
        "ttl": days * 86400,
    }
    if password:
        payload["pid"] = password

    resp = requests.post(
        "https://my.hidrive.com/api/link",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    sharekey = resp.json().get("sharekey", "")
    if not sharekey:
        raise RuntimeError(f"HiDrive: kein sharekey in Antwort: {resp.text[:200]}")
    return f"https://my.hidrive.com/share/{sharekey}"


# ── Synology Drive ────────────────────────────────────────────────────────────

def _syno_login(cfg: dict) -> str:
    """Meldet sich bei Synology an und gibt die Session-ID (sid) zurück."""
    base = cfg["url"].rstrip("/")
    resp = requests.get(
        f"{base}/webapi/auth.cgi",
        params={
            "api":     "SYNO.API.Auth",
            "method":  "login",
            "version": "3",
            "account": cfg.get("username") or cfg.get("user", ""),
            "passwd":  cfg.get("password", ""),
            "format":  "sid",
        },
        verify=cfg.get("verify_ssl", True),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Synology Login fehlgeschlagen: {data.get('error', {})}")
    return data["data"]["sid"]


def _syno_webdav_url(cfg: dict, path: str) -> str:
    base = cfg["url"].rstrip("/")
    return f"{base}/webdav/{path.lstrip('/')}"


def _syno_auth(cfg: dict):
    return (cfg.get("username") or cfg.get("user", ""), cfg.get("password", ""))


def synology_ensure_folder(cfg: dict, folder_path: str):
    """Erstellt den Zielordner via WebDAV (MKCOL)."""
    verify = cfg.get("verify_ssl", True)
    parts = folder_path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        url = _syno_webdav_url(cfg, current)
        resp = requests.request(
            "MKCOL", url, auth=_syno_auth(cfg), verify=verify, timeout=15
        )
        if resp.status_code not in (201, 405, 409):
            resp.raise_for_status()


def upload_to_synology(cfg: dict, filename: str, content: bytes,
                       content_type: str = "application/octet-stream",
                       subfolder: str = "") -> str:
    """Lädt Datei zu Synology Drive via WebDAV hoch. Gibt den Pfad zurück."""
    base = cfg.get("folder", "SecureSend")
    folder = f"{base}/{subfolder}" if subfolder else base
    synology_ensure_folder(cfg, folder)
    path = f"{folder}/{filename}"
    url = _syno_webdav_url(cfg, path)
    resp = requests.put(
        url,
        auth=_syno_auth(cfg),
        data=content,
        headers={"Content-Type": content_type},
        verify=cfg.get("verify_ssl", True),
        timeout=60,
    )
    resp.raise_for_status()
    return path


def create_synology_share_link(cfg: dict, sid: str, file_path: str,
                                password: Optional[str], days: int) -> str:
    """Erstellt einen Synology FileStation Freigabe-Link."""
    base = cfg["url"].rstrip("/")
    # Absoluter Pfad für FileStation (/homes/{user}/... oder /home/...)
    folder_base = cfg.get("folder", "SecureSend")
    abs_path = f"/{folder_base}/{file_path.lstrip('/')}"

    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    params: dict = {
        "api":          "SYNO.FileStation.Sharing",
        "method":       "create",
        "version":      "3",
        "_sid":         sid,
        "path":         abs_path,
        "date_expired": expiry,
    }
    if password:
        params["password"] = password

    resp = requests.get(
        f"{base}/webapi/entry.cgi",
        params=params,
        verify=cfg.get("verify_ssl", True),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Synology Share fehlgeschlagen: {data.get('error', {})}")
    links = data.get("data", {}).get("links", [])
    if not links:
        raise RuntimeError("Synology: kein Link in der Antwort")
    link_id = links[0].get("id", "")
    return f"{base}/sharing/{link_id}"


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

    elif service == "dropbox":
        token = _dropbox_token(cfg)
        file_path = upload_to_dropbox(cfg, token, filename, content, subfolder=subfolder)
        return create_dropbox_share_link(cfg, token, file_path, days)

    elif service == "hidrive":
        file_path = upload_to_hidrive(cfg, filename, content,
                                      content_type=content_type, subfolder=subfolder)
        return create_hidrive_share_link(cfg, file_path, password, days)

    elif service == "synology":
        file_path = upload_to_synology(cfg, filename, content,
                                       content_type=content_type, subfolder=subfolder)
        sid = _syno_login(cfg)
        return create_synology_share_link(cfg, sid, file_path, password, days)

    else:
        raise ValueError(f"Unbekannter Storage-Service: {service!r}")


# ── Multi-File Upload + Folder-Share ─────────────────────────────────────────

def upload_files_and_share_folder(
    cfg: dict,
    files: list[tuple[str, bytes, str]],
    folder_path: str,
    password: Optional[str],
    days: int,
) -> str:
    """Lädt mehrere Dateien in einen Ordner hoch und gibt einen Freigabe-Link auf den Ordner zurück.

    Args:
        cfg:         Provider-Konfiguration (inkl. service)
        files:       Liste von (filename, content, content_type)
        folder_path: Zielordner-Pfad (relativ zum Upload-Basisordner)
        password:    Passwort für den Share-Link (None = kein Passwortschutz)
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
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        item_id = r.json()["id"]
        return create_onedrive_share_link(cfg, token, item_id, password, days)

    elif service == "dropbox":
        token = _dropbox_token(cfg)
        for filename, content, content_type in files:
            upload_to_dropbox(cfg, token, filename, content, subfolder=folder_path)
        return create_dropbox_share_link(cfg, token,
                                         f"{cfg.get('folder', '/SecureSend')}/{folder_path}", days)

    elif service == "hidrive":
        for filename, content, content_type in files:
            upload_to_hidrive(cfg, filename, content,
                              content_type=content_type, subfolder=folder_path)
        return create_hidrive_share_link(cfg, folder_path, password, days)

    elif service == "synology":
        for filename, content, content_type in files:
            upload_to_synology(cfg, filename, content,
                               content_type=content_type, subfolder=folder_path)
        sid = _syno_login(cfg)
        return create_synology_share_link(cfg, sid, folder_path, password, days)

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

    elif service == "dropbox":
        try:
            token = _dropbox_token(cfg)
            r = requests.post(
                "https://api.dropboxapi.com/2/users/get_current_account",
                headers={**_dropbox_headers(token), "Content-Type": "application/json"},
                data="null",
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            result["display_name"] = d.get("name", {}).get("display_name")
            # Quota
            rq = requests.post(
                "https://api.dropboxapi.com/2/users/get_space_usage",
                headers={**_dropbox_headers(token), "Content-Type": "application/json"},
                data="null",
                timeout=10,
            )
            if rq.ok:
                qd = rq.json()
                used = qd.get("used")
                alloc = qd.get("allocation", {})
                total = alloc.get("allocated")
                result["quota"] = {
                    "used": used,
                    "available": (total - used) if (total and used is not None) else None,
                    "total": total,
                }
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    elif service == "hidrive":
        try:
            creds = base64.b64encode(
                f"{cfg.get('username') or cfg.get('user', '')}:{cfg.get('password', '')}".encode()
            ).decode()
            r = requests.get(
                "https://my.hidrive.com/api/user",
                headers={"Authorization": f"Basic {creds}"},
                timeout=10,
            )
            if r.status_code == 401:
                result["error"] = "Ungültige Zugangsdaten (401)"
                return result
            r.raise_for_status()
            d = r.json()
            result["display_name"] = d.get("alias") or d.get("account") or d.get("email")
            # Quota via /api/user/quota
            rq = requests.get(
                "https://my.hidrive.com/api/user/quota",
                headers={"Authorization": f"Basic {creds}"},
                timeout=10,
            )
            if rq.ok:
                qd = rq.json()
                used  = qd.get("used_quota")
                total = qd.get("quota")
                result["quota"] = {
                    "used": used,
                    "available": (total - used) if (total and used is not None) else None,
                    "total": total,
                }
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    elif service == "synology":
        try:
            sid = _syno_login(cfg)
            base = cfg["url"].rstrip("/")
            r = requests.get(
                f"{base}/webapi/entry.cgi",
                params={"api": "SYNO.FileStation.Info", "method": "get", "version": "2", "_sid": sid},
                verify=cfg.get("verify_ssl", True),
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            if d.get("success"):
                info = d.get("data", {})
                result["display_name"] = info.get("hostname") or cfg.get("url")
                # Quota via SYNO.Core.System.Utilization oder WebDAV PROPFIND
                result["quota"] = None  # Optional: via weiterer API-Aufruf
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    else:
        result["error"] = f"Unbekannter Service: {service!r}"

    return result
