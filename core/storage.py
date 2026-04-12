"""
core/storage.py — Cloud-Storage Upload & Share-Link
Unterstützte Anbieter: Nextcloud, ownCloud, OneDrive, Dropbox, HiDrive, Synology Drive, MinIO

Alle Funktionen erhalten die Konfiguration als `cfg: dict`.
Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import base64
import json as _json
import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

try:
    import msal as _msal

    _MSAL_AVAILABLE = True
except ImportError:
    _MSAL_AVAILABLE = False

try:
    import boto3 as _boto3

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

# Interner Cache: (nc_url, nc_user) → interne User-ID (z.B. UUID bei LDAP)
_nc_user_id_cache: dict[tuple, str] = {}


def _service_is_nextcloud_family(service: str) -> bool:
    """Nextcloud und ownCloud (OCS + WebDAV unter /remote.php/dav/files/…)."""
    return service in ("nextcloud", "owncloud")


def _expect_json_response(
    resp: requests.Response, step: str, *, hint: str = ""
) -> Any:
    """Parst JSON; bei leerer oder HTML-Antwort verständliche RuntimeError-Meldung."""
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError(
            f"{step}: Leere HTTP-Antwort ({resp.status_code}) von {resp.url}. {hint}".strip()
        )
    try:
        return resp.json()
    except ValueError:
        preview = text[:350].replace("\n", " ")
        raise RuntimeError(
            f"{step}: Kein JSON ({resp.status_code}). {hint} "
            f"Antwortbeginn: {preview!r}"
        ) from None


def _xml_local_tag(tag: str) -> str:
    if not tag:
        return ""
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def _ocs_cloud_user_payload_from_xml(text: str) -> dict:
    """Wandelt OCS-XML (cloud/user) in ein JSON-ähnliches { \"ocs\": { \"meta\", \"data\" } }."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text.strip())
    if _xml_local_tag(root.tag) != "ocs":
        ocs_el = None
        for el in root.iter():
            if _xml_local_tag(el.tag) == "ocs":
                ocs_el = el
                break
        if ocs_el is None:
            raise ValueError("Kein <ocs>-Element")
        root = ocs_el
    meta: dict[str, str] = {}
    data: dict[str, str] = {}
    for child in root:
        ln = _xml_local_tag(child.tag)
        if ln == "meta":
            for m in child:
                meta[_xml_local_tag(m.tag)] = (m.text or "").strip()
        elif ln == "data":
            for d in child:
                data[_xml_local_tag(d.tag)] = (d.text or "").strip()
    return {"ocs": {"meta": meta, "data": data}}


def _parse_ocs_cloud_user_response(
    resp: requests.Response, step: str, *, hint: str = ""
) -> dict:
    """JSON oder OCS-XML von /ocs/v2.php/cloud/user."""
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError(
            f"{step}: Leere HTTP-Antwort ({resp.status_code}) von {resp.url}. {hint}".strip()
        )
    if text.startswith("<") or text.startswith("<?xml"):
        try:
            return _ocs_cloud_user_payload_from_xml(text)
        except Exception as exc:
            preview = text[:350].replace("\n", " ")
            raise RuntimeError(
                f"{step}: OCS-XML nicht lesbar ({resp.status_code}). {hint} "
                f"Antwortbeginn: {preview!r} ({exc})"
            ) from exc
    try:
        return resp.json()
    except ValueError:
        preview = text[:350].replace("\n", " ")
        raise RuntimeError(
            f"{step}: Weder JSON noch erkanntes XML ({resp.status_code}). {hint} "
            f"Antwortbeginn: {preview!r}"
        ) from None


def _ocs_meta_failure_message(meta: dict) -> str | None:
    """Liefert eine Nutzer-Meldung, wenn OCS meta einen Fehler meldet."""
    if not meta:
        return None
    status = (meta.get("status") or "").strip().lower()
    raw_code = meta.get("statuscode")
    try:
        code = int(raw_code) if raw_code not in (None, "") else None
    except (TypeError, ValueError):
        code = None
    msg = (meta.get("message") or "").strip() or "Unbekannter OCS-Fehler"

    if status == "failure":
        extra = ""
        if code == 997:
            extra = (
                " Üblich: falsches Passwort, oder es wird ein App-Passwort benötigt "
                "(Kontoeinstellungen / Sicherheit), nicht das normale Web-Login-Passwort; "
                "bei 2FA ohne App-Passwort schlägt die API fehl."
            )
        return f"{msg} (OCS {code or '—'}){extra}"

    if status and status != "ok":
        return f"{msg} (OCS status={status!r}, code={code or raw_code})"

    if code is not None and code not in (100, 200):
        return f"{msg} (OCS-Statuscode {code})"

    return None


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
        resp = requests.request(
            "MKCOL", url, auth=(cfg["user"], cfg["password"]), timeout=15
        )
        if resp.status_code not in (201, 405, 409):
            resp.raise_for_status()


def upload_to_nextcloud(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str = "text/markdown; charset=utf-8",
    subfolder: str = "",
) -> str:
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


def create_nextcloud_share_link(
    cfg: dict, file_path: str, password: Optional[str], days: int
) -> str:
    """Erstellt Share via OCS API, gibt die öffentliche URL zurück.
    Wird password=None übergeben, wird kein Passwortschutz gesetzt."""
    base = cfg["url"].rstrip("/")
    api_url = f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    expiry_str = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    data: dict = {
        "path": f"/{file_path}",
        "shareType": 3,
        "permissions": 1,
        "expireDate": expiry_str,
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
        raise RuntimeError(
            "msal ist nicht installiert. Bitte 'pip install msal' ausführen."
        )
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


def upload_to_onedrive(
    cfg: dict,
    token: str,
    filename: str,
    content: bytes,
    content_type: str = "text/markdown; charset=utf-8",
    subfolder: str = "",
) -> str:
    """Lädt Datei hoch, gibt die Item-ID zurück."""
    base_folder = cfg.get("folder", "SecureSend")
    path = (
        f"{base_folder}/{subfolder}/{filename}"
        if subfolder
        else f"{base_folder}/{filename}"
    )
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


def create_onedrive_share_link(
    cfg: dict, token: str, item_id: str, password: Optional[str], days: int
) -> str:
    """Erstellt Freigabe-Link; optional mit Passwortschutz."""
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    url = (
        f"https://graph.microsoft.com/v1.0/users/{cfg['user']}"
        f"/drive/items/{item_id}/createLink"
    )
    payload: dict = {
        "type": "view",
        "scope": "anonymous",
        "expirationDateTime": expiry,
    }
    if password:
        payload["password"] = password
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["link"]["webUrl"]


# ── Dropbox ──────────────────────────────────────────────────────────────────


def _dropbox_oauth_error_message(resp: requests.Response) -> str:
    """Liest Dropbox-Fehler aus der OAuth-Antwort (meist JSON mit error / error_description)."""
    try:
        data = resp.json()
        err = data.get("error")
        desc = data.get("error_description") or data.get("user_message")
        if err and desc:
            return f"{err}: {desc}"
        if err:
            return str(err)
        if desc:
            return str(desc)
    except Exception:
        pass
    text = (resp.text or "").strip()
    if text:
        return text[:500]
    return resp.reason or f"HTTP {resp.status_code}"


def _dropbox_token(cfg: dict) -> str:
    """Holt ein frisches Dropbox Access-Token via Refresh-Token.

    Erwartet in cfg: app_key, app_secret, refresh_token (alle zum selben App-Eintrag
    in der Dropbox App Console). Der Wert im Feld refresh_token muss ein echter
    OAuth-Refresh-Token sein (Flow mit token_access_type=offline), nicht der
    kurzlebige access_token.
    """
    app_key = (cfg.get("app_key") or "").strip()
    app_secret = (cfg.get("app_secret") or "").strip()
    refresh = (cfg.get("refresh_token") or "").strip()

    if refresh and app_key and app_secret:
        resp = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
            auth=(app_key, app_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Dropbox OAuth ({resp.status_code}): {_dropbox_oauth_error_message(resp)}"
            )
        try:
            return resp.json()["access_token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Dropbox OAuth: unerwartete Antwort: {resp.text!r}") from exc

    # Fallback: direkt gespeicherter Access-Token (z. B. kurzlebig aus der Konsole)
    token = (cfg.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(
            "Dropbox: Bitte App-Key, App-Secret und Refresh-Token ausfüllen, "
            "oder einen access_token hinterlegen (Fallback)."
        )
    return token


def _dropbox_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload_to_dropbox(
    cfg: dict, token: str, filename: str, content: bytes, subfolder: str = ""
) -> str:
    """Lädt Datei zu Dropbox hoch. Gibt den Dropbox-Pfad zurück."""
    base = cfg.get("folder", "/SecureSend").rstrip("/")
    path = f"{base}/{subfolder}/{filename}" if subfolder else f"{base}/{filename}"

    resp = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            **_dropbox_headers(token),
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": _json.dumps(
                {
                    "path": path,
                    "mode": "add",
                    "autorename": True,
                }
            ),
        },
        data=content,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["path_display"]


def create_dropbox_share_link(cfg: dict, token: str, path: str, days: int) -> str:
    """Erstellt einen Dropbox-Freigabe-Link (kein Passwortschutz via API)."""
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    resp = requests.post(
        "https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings",
        headers={**_dropbox_headers(token), "Content-Type": "application/json"},
        json={
            "path": path,
            "settings": {"requested_visibility": "public", "expires": expiry},
        },
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


def upload_to_hidrive(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    subfolder: str = "",
) -> str:
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


def create_hidrive_share_link(
    cfg: dict, file_path: str, password: Optional[str], days: int
) -> str:
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


def _synology_parse_json(resp: requests.Response, step: str) -> dict:
    """Parst Synology-WebAPI-Antwort; bei HTML oder leerer Antwort klare Fehlermeldung."""
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError(
            f"Synology ({step}): Leere HTTP-Antwort ({resp.status_code}) von {resp.url}. "
            "Prüfen Sie die NAS-URL (üblich: https://…:5001 für DSM HTTPS)."
        )
    try:
        return resp.json()
    except ValueError:
        preview = text[:350].replace("\n", " ")
        raise RuntimeError(
            f"Synology ({step}): Kein JSON ({resp.status_code}) — oft falsche URL, "
            "Reverse-Proxy oder Anmeldeseite statt Web-API. "
            f"Antwort beginnt mit: {preview!r}"
        ) from None


def _syno_login(cfg: dict) -> str:
    """Meldet sich bei Synology an und gibt die Session-ID (sid) zurück."""
    base = (cfg.get("url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Synology: url fehlt")
    resp = requests.get(
        f"{base}/webapi/auth.cgi",
        params={
            "api": "SYNO.API.Auth",
            "method": "login",
            "version": "3",
            "account": (cfg.get("username") or cfg.get("user") or "").strip(),
            "passwd": (cfg.get("password") or "").strip(),
            "format": "sid",
        },
        verify=cfg.get("verify_ssl", True),
        timeout=15,
    )
    resp.raise_for_status()
    data = _synology_parse_json(resp, "Login")
    if not data.get("success"):
        raise RuntimeError(f"Synology Login fehlgeschlagen: {data.get('error', {})}")
    sid = (data.get("data") or {}).get("sid")
    if not sid:
        raise RuntimeError(f"Synology Login: keine sid in Antwort: {data!r}")
    return sid


def _syno_webdav_url(cfg: dict, path: str) -> str:
    base = (cfg.get("url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Synology: url fehlt")
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


def upload_to_synology(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    subfolder: str = "",
) -> str:
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


def create_synology_share_link(
    cfg: dict, sid: str, file_path: str, password: Optional[str], days: int
) -> str:
    """Erstellt einen Synology FileStation Freigabe-Link."""
    base = (cfg.get("url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("Synology: url fehlt")
    # Absoluter Pfad für FileStation (/homes/{user}/... oder /home/...)
    folder_base = cfg.get("folder", "SecureSend")
    abs_path = f"/{folder_base}/{file_path.lstrip('/')}"

    expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    params: dict = {
        "api": "SYNO.FileStation.Sharing",
        "method": "create",
        "version": "3",
        "_sid": sid,
        "path": abs_path,
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
    data = _synology_parse_json(resp, "Freigabe erstellen")
    if not data.get("success"):
        raise RuntimeError(f"Synology Share fehlgeschlagen: {data.get('error', {})}")
    links = data.get("data", {}).get("links", [])
    if not links:
        raise RuntimeError("Synology: kein Link in der Antwort")
    link_id = links[0].get("id", "")
    return f"{base}/sharing/{link_id}"


# ── MinIO / S3 Storage ─────────────────────────────────────────────────────────


def _get_s3_client(cfg: dict):
    """Erstellt einen boto3 S3-Client für MinIO oder AWS S3."""
    if not _BOTO3_AVAILABLE:
        raise RuntimeError(
            "boto3 ist nicht installiert. Bitte 'pip install boto3' ausführen."
        )

    import boto3
    from botocore.config import Config

    endpoint = cfg.get("endpoint", "")
    region = cfg.get("region", "us-east-1")
    access_key = cfg.get("access_key", "")
    secret_key = cfg.get("secret_key", "")
    use_ssl = cfg.get("use_ssl", True)

    # MinIO can use path-style or virtual-hosted style
    s3_kwargs = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region,
    }

    # Only add endpoint_url if MinIO (not AWS S3)
    if endpoint:
        s3_kwargs["endpoint_url"] = endpoint

    # Configure signature version for MinIO (S3v4)
    if endpoint:  # MinIO
        s3_kwargs["config"] = Config(signature_version="s3v4")

    return boto3.client("s3", **s3_kwargs)


def upload_to_minio(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    subfolder: str = "",
) -> str:
    """Lädt Datei zu MinIO/S3 hoch. Gibt den Object Key zurück."""
    s3 = _get_s3_client(cfg)
    bucket = cfg.get("bucket", "securesend")
    base_folder = cfg.get("folder", "SecureSend")
    key = (
        f"{base_folder}/{subfolder}/{filename}"
        if subfolder
        else f"{base_folder}/{filename}"
    )

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=content_type,
    )
    return key


def create_minio_share_link(
    cfg: dict,
    object_key: str,
    password: Optional[str],
    days: int,
) -> str:
    """Erstellt einen vorgefertigten Share-Link für MinIO/S3 (via Presigned URL)."""
    s3 = _get_s3_client(cfg)
    bucket = cfg.get("bucket", "securesend")
    base_url = cfg.get("base_url", "")  # Optional: public base URL for the bucket

    # Generate presigned URL
    expiry_seconds = days * 24 * 60 * 60
    presigned_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key},
        ExpiresIn=expiry_seconds,
    )

    # If base_url is provided, we can also create a public link (if bucket is public)
    # Otherwise, return the presigned URL
    if base_url and not password:
        # For public buckets, construct direct link
        return f"{base_url.rstrip('/')}/{object_key}"

    return presigned_url


def get_minio_status(cfg: dict) -> dict:
    """Prüft MinIO-Verbindung und liefert Bucket-Informationen."""
    result = {
        "ok": False,
        "service": "minio",
        "display_name": None,
        "quota": None,
        "error": None,
    }

    try:
        s3 = _get_s3_client(cfg)
        bucket = cfg.get("bucket", "securesend")

        # Check bucket existence
        s3.head_bucket(Bucket=bucket)

        result["display_name"] = f"MinIO: {bucket}"
        result["ok"] = True

        # Try to get bucket encryption status
        try:
            enc = s3.get_bucket_encryption(Bucket=bucket)
            result["encryption"] = enc.get("ServerSideEncryptionRules", [{}])[0].get(
                "SSEAlgorithm"
            )
        except Exception:
            pass

    except Exception as e:
        result["error"] = str(e)

    return result


# ── Dispatcher ───────────────────────────────────────────────────────────────


def upload_and_share(
    cfg: dict,
    filename: str,
    content: bytes,
    password: str,
    days: int,
    content_type: str = "text/markdown; charset=utf-8",
    subfolder: str = "",
) -> str:
    """Lädt Datei hoch und gibt passwortgeschützten Link zurück.

    cfg muss enthalten:
      - service: "nextcloud" | "owncloud" | "onedrive"
      - Nextcloud/ownCloud: url, user, password, folder
      - OneDrive:  client_id, client_secret, tenant_id, user, folder
    """
    service = cfg.get("service", "nextcloud")

    if _service_is_nextcloud_family(service):
        file_path = upload_to_nextcloud(
            cfg, filename, content, content_type=content_type, subfolder=subfolder
        )
        return create_nextcloud_share_link(cfg, file_path, password, days)

    elif service == "onedrive":
        token = get_graph_token(cfg)
        item_id = upload_to_onedrive(
            cfg,
            token,
            filename,
            content,
            content_type=content_type,
            subfolder=subfolder,
        )
        return create_onedrive_share_link(cfg, token, item_id, password, days)

    elif service == "dropbox":
        token = _dropbox_token(cfg)
        file_path = upload_to_dropbox(
            cfg, token, filename, content, subfolder=subfolder
        )
        return create_dropbox_share_link(cfg, token, file_path, days)

    elif service == "hidrive":
        file_path = upload_to_hidrive(
            cfg, filename, content, content_type=content_type, subfolder=subfolder
        )
        return create_hidrive_share_link(cfg, file_path, password, days)

    elif service == "synology":
        file_path = upload_to_synology(
            cfg, filename, content, content_type=content_type, subfolder=subfolder
        )
        sid = _syno_login(cfg)
        return create_synology_share_link(cfg, sid, file_path, password, days)

    elif service == "minio":
        file_path = upload_to_minio(
            cfg, filename, content, content_type=content_type, subfolder=subfolder
        )
        return create_minio_share_link(cfg, file_path, password, days)

    elif service == "securesend_hosted":
        from core.hosted_storage import HOSTED_SHARE_PLACEHOLDER, hosted_upload_single

        base_folder = cfg.get("folder", "SecureSend")
        rel_sub = (
            f"{base_folder}/{subfolder}".strip("/") if subfolder else base_folder
        )
        hosted_upload_single(cfg, filename, content, content_type, rel_sub)
        return HOSTED_SHARE_PLACEHOLDER

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

    if _service_is_nextcloud_family(service):
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
            upload_to_onedrive(
                cfg,
                token,
                filename,
                content,
                content_type=content_type,
                subfolder=folder_path,
            )
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
        return create_dropbox_share_link(
            cfg, token, f"{cfg.get('folder', '/SecureSend')}/{folder_path}", days
        )

    elif service == "hidrive":
        for filename, content, content_type in files:
            upload_to_hidrive(
                cfg, filename, content, content_type=content_type, subfolder=folder_path
            )
        return create_hidrive_share_link(cfg, folder_path, password, days)

    elif service == "synology":
        for filename, content, content_type in files:
            upload_to_synology(
                cfg, filename, content, content_type=content_type, subfolder=folder_path
            )
        sid = _syno_login(cfg)
        return create_synology_share_link(cfg, sid, folder_path, password, days)

    elif service == "securesend_hosted":
        from core.hosted_storage import HOSTED_SHARE_PLACEHOLDER, hosted_upload_folder

        hosted_upload_folder(cfg, files, folder_path)
        return HOSTED_SHARE_PLACEHOLDER

    else:
        raise ValueError(f"Unbekannter Storage-Service: {service!r}")


def download_cloud_file(cfg: dict, folder_path: str, filename: str) -> bytes:
    """Lädt eine Datei aus einem Ordner, den z. B. upload_files_and_share_folder angelegt hat.

    folder_path: Relativer Pfad wie bei Upload (z. B. ``SecureSend/<user_id>/<timestamp>``).
    filename:    Dateiname innerhalb dieses Ordners.

    Unterstützte Dienste: nextcloud, owncloud, onedrive, dropbox, hidrive, synology — analog zu
    ``upload_files_and_share_folder``.
    """
    service = cfg.get("service", "nextcloud")
    fp = folder_path.strip().strip("/")
    rel = f"{fp}/{filename}" if fp else filename

    if service == "securesend_hosted":
        from core.hosted_storage import hosted_download

        return hosted_download(cfg, folder_path, filename)

    if _service_is_nextcloud_family(service):
        url = _nc_webdav_url(cfg, rel)
        resp = requests.get(
            url, auth=(cfg["user"], cfg["password"]), timeout=120
        )
        resp.raise_for_status()
        return resp.content

    if service == "onedrive":
        token = get_graph_token(cfg)
        graph_path = f"/users/{cfg['user']}/drive/root:/{rel}:/content"
        url = f"https://graph.microsoft.com/v1.0{graph_path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content

    if service == "dropbox":
        token = _dropbox_token(cfg)
        base = cfg.get("folder", "/SecureSend").rstrip("/")
        dbx_path = f"{base}/{rel}" if fp else f"{base}/{filename}"
        resp = requests.post(
            "https://content.dropboxapi.com/2/files/download",
            headers={
                **_dropbox_headers(token),
                "Dropbox-API-Arg": _json.dumps({"path": dbx_path}),
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content

    if service == "hidrive":
        base = cfg.get("folder", "SecureSend")
        inner = f"{base}/{fp}" if fp else base
        full_path = f"{inner}/{filename}"
        url = _hidrive_webdav_url(cfg, full_path)
        resp = requests.get(url, auth=_hidrive_auth(cfg), timeout=120)
        resp.raise_for_status()
        return resp.content

    if service == "synology":
        base = cfg.get("folder", "SecureSend")
        inner = f"{base}/{fp}" if fp else base
        full_path = f"{inner}/{filename}"
        url = _syno_webdav_url(cfg, full_path)
        resp = requests.get(
            url,
            auth=_syno_auth(cfg),
            verify=cfg.get("verify_ssl", True),
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content

    raise ValueError(
        f"Download aus dem Speicher {service!r} wird für E2E-Versand nicht unterstützt "
        f"(kein Ordner-Upload in upload_files_and_share_folder)."
    )


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
    result: dict = {
        "ok": False,
        "service": service,
        "display_name": None,
        "quota": None,
        "error": None,
    }

    if _service_is_nextcloud_family(service):
        nc_hint = (
            "URL muss die Basis der Installation sein (inkl. ggf. /owncloud oder /nextcloud). "
            "OCS-Endpunkt /ocs/v2.php/cloud/user muss erreichbar sein — kein Proxy mit HTML-Login davor."
        )
        try:
            base = (cfg.get("url") or "").strip().rstrip("/")
            if not base:
                result["error"] = "Server-URL fehlt"
                return result
            auth = (
                (cfg.get("user") or "").strip(),
                (cfg.get("password") or "").strip(),
            )

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
            payload = _parse_ocs_cloud_user_response(
                r,
                "Nextcloud/ownCloud (OCS cloud/user)",
                hint=nc_hint,
            )
            ocs = payload.get("ocs")
            if not isinstance(ocs, dict):
                result["error"] = (
                    f"Nextcloud/ownCloud: unerwartete Antwort (kein OCS). {nc_hint}"
                )
                return result
            meta = ocs.get("meta") if isinstance(ocs.get("meta"), dict) else {}
            fail_msg = _ocs_meta_failure_message(meta)
            if fail_msg:
                result["error"] = f"Nextcloud/ownCloud: {fail_msg}"
                return result
            data = ocs.get("data")
            if not isinstance(data, dict):
                prev = repr(payload)[:400]
                result["error"] = (
                    f"Nextcloud/ownCloud OCS: keine Nutzerdaten in der Antwort: {prev}"
                )
                return result
            result["display_name"] = (
                data.get("display-name") or data.get("displayname") or data.get("id")
            )

            # Quota via WebDAV PROPFIND
            uid = data.get("id", quote(cfg.get("user", ""), safe=""))
            webdav_url = f"{base}/remote.php/dav/files/{uid}/"
            propfind_body = (
                '<?xml version="1.0"?>'
                '<d:propfind xmlns:d="DAV:">'
                "<d:prop><d:quota-available-bytes/><d:quota-used-bytes/></d:prop>"
                "</d:propfind>"
            )
            rq = requests.request(
                "PROPFIND",
                webdav_url,
                auth=auth,
                data=propfind_body,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                timeout=10,
            )
            if rq.ok:
                import xml.etree.ElementTree as ET

                try:
                    root = ET.fromstring(rq.text or "")
                except ET.ParseError:
                    pass
                else:
                    ns = {"d": "DAV:"}
                    avail = root.findtext(".//d:quota-available-bytes", namespaces=ns)
                    used = root.findtext(".//d:quota-used-bytes", namespaces=ns)
                    avail_i = (
                        int(avail) if avail and avail.lstrip("-").isdigit() else None
                    )
                    used_i = int(used) if used and used.lstrip("-").isdigit() else None
                    if used_i is not None:
                        total = (
                            (used_i + avail_i)
                            if (avail_i is not None and avail_i >= 0)
                            else None
                        )
                        result["quota"] = {
                            "used": used_i,
                            "available": avail_i
                            if avail_i is not None and avail_i >= 0
                            else None,
                            "total": total,
                        }
            result["ok"] = True

        except requests.RequestException as e:
            result["error"] = f"Verbindungsfehler: {e}"
        except RuntimeError as e:
            result["error"] = str(e)

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
            result["display_name"] = (
                d.get("owner", {}).get("user", {}).get("displayName")
            )
            quota = d.get("quota", {})
            used = quota.get("used")
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
                    "available": (total - used)
                    if (total and used is not None)
                    else None,
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
            result["display_name"] = (
                d.get("alias") or d.get("account") or d.get("email")
            )
            # Quota via /api/user/quota
            rq = requests.get(
                "https://my.hidrive.com/api/user/quota",
                headers={"Authorization": f"Basic {creds}"},
                timeout=10,
            )
            if rq.ok:
                qd = rq.json()
                used = qd.get("used_quota")
                total = qd.get("quota")
                result["quota"] = {
                    "used": used,
                    "available": (total - used)
                    if (total and used is not None)
                    else None,
                    "total": total,
                }
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    elif service == "synology":
        try:
            sid = _syno_login(cfg)
            base = (cfg.get("url") or "").strip().rstrip("/")
            r = requests.get(
                f"{base}/webapi/entry.cgi",
                params={
                    "api": "SYNO.FileStation.Info",
                    "method": "get",
                    "version": "2",
                    "_sid": sid,
                },
                verify=cfg.get("verify_ssl", True),
                timeout=10,
            )
            r.raise_for_status()
            d = _synology_parse_json(r, "FileStation.Info")
            if d.get("success"):
                info = d.get("data", {})
                result["display_name"] = info.get("hostname") or cfg.get("url")
                # Quota via SYNO.Core.System.Utilization oder WebDAV PROPFIND
                result["quota"] = None  # Optional: via weiterer API-Aufruf
            else:
                err = d.get("error", {})
                result["error"] = f"Synology API: {err}"
                return result
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    elif service == "securesend_hosted":
        from core.hosted_storage import hosted_check_connectivity

        result["display_name"] = "SecureSend Storage"
        used = cfg.get("_hosted_quota_used")
        total = cfg.get("_hosted_quota_total")
        if used is not None and total is not None:
            try:
                u, t = int(used), int(total)
                result["quota"] = {
                    "used": u,
                    "total": t,
                    "available": max(0, t - u),
                }
            except (TypeError, ValueError):
                pass
        try:
            hosted_check_connectivity(cfg)
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    elif service == "minio":
        return get_minio_status(cfg)

    else:
        result["error"] = f"Unbekannter Service: {service!r}"

    return result
