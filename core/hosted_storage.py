"""
SecureSend Hosted Storage — lokales Dateisystem oder S3-kompatibler Speicher.

Der Cloud-Router reichert cfg mit _org_id, _storage_root, hosted_backend und ggf. S3-Feldern an.
Öffentlicher service-Name in der DB: securesend_hosted
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

HOSTED_SERVICE_NAME = "securesend_hosted"

# Platzhalter-URL bis send.py History mit /track/l/ aktualisiert
HOSTED_SHARE_PLACEHOLDER = "securesend://hosted"


def _safe_segment(name: str) -> str:
    base = PurePosixPath(name).name
    if not base or base != name or ".." in name:
        raise ValueError(f"Ungültiger Dateiname: {name!r}")
    return base


def _local_org_base(cfg: dict) -> Path:
    root = Path(cfg["_storage_root"]).resolve()
    org_id = cfg.get("_org_id") or ""
    if not org_id or "/" in org_id or org_id.startswith("."):
        raise ValueError("Ungültige Organisations-ID für Hosted Storage")
    base = root / org_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def hosted_upload_folder_local(
    cfg: dict, files: list[tuple[str, bytes, str]], folder_path: str
) -> None:
    rel = folder_path.strip().strip("/")
    if ".." in rel or rel.startswith("."):
        raise ValueError("Ungültiger Ordnerpfad")
    target = _local_org_base(cfg) / rel.replace("\\", "/")
    target.mkdir(parents=True, exist_ok=True)
    for fname, data, _ct in files:
        safe = _safe_segment(fname)
        (target / safe).write_bytes(data)


def hosted_download_local(cfg: dict, folder_path: str, filename: str) -> bytes:
    rel = folder_path.strip().strip("/")
    if ".." in rel or rel.startswith("."):
        raise ValueError("Ungültiger Ordnerpfad")
    safe = _safe_segment(filename)
    path = _local_org_base(cfg) / rel / safe
    path = path.resolve()
    org_base = _local_org_base(cfg).resolve()
    if not str(path).startswith(str(org_base)):
        raise ValueError("Pfad außerhalb des Org-Verzeichnisses")
    if not path.is_file():
        raise FileNotFoundError(safe)
    return path.read_bytes()


def hosted_upload_single_local(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str,
    subfolder: str,
) -> None:
    rel = subfolder.strip().strip("/") if subfolder else ""
    if ".." in rel or rel.startswith("."):
        raise ValueError("Ungültiger Unterordner")
    safe = _safe_segment(filename)
    folder = _local_org_base(cfg) / rel.replace("\\", "/")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / safe).write_bytes(content)


def _s3_client(cfg: dict):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:
        raise RuntimeError(
            "boto3 ist für S3/MinIO erforderlich. pip install boto3"
        ) from e

    endpoint = cfg.get("_s3_endpoint") or ""
    region = cfg.get("_s3_region") or "us-east-1"
    access_key = cfg.get("_s3_access_key") or ""
    secret_key = cfg.get("_s3_secret_key") or ""
    kwargs: dict = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region,
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["config"] = Config(signature_version="s3v4")
    return boto3.client("s3", **kwargs)


def _s3_key(cfg: dict, folder_path: str, filename: str) -> str:
    org_id = cfg.get("_org_id") or ""
    fp = folder_path.strip().strip("/")
    safe = _safe_segment(filename)
    if fp:
        return f"{org_id}/{fp}/{safe}"
    return f"{org_id}/{safe}"


def hosted_upload_folder_s3(
    cfg: dict, files: list[tuple[str, bytes, str]], folder_path: str
) -> None:
    bucket = cfg.get("_s3_bucket") or ""
    if not bucket:
        raise ValueError("S3-Bucket nicht konfiguriert (SECURESEND_S3_BUCKET)")
    s3 = _s3_client(cfg)
    for fname, data, ct in files:
        key = _s3_key(cfg, folder_path, fname)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=ct or "application/octet-stream",
        )


def hosted_download_s3(cfg: dict, folder_path: str, filename: str) -> bytes:
    bucket = cfg.get("_s3_bucket") or ""
    if not bucket:
        raise ValueError("S3-Bucket nicht konfiguriert")
    key = _s3_key(cfg, folder_path, filename)
    s3 = _s3_client(cfg)
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def hosted_upload_single_s3(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str,
    subfolder: str,
) -> None:
    bucket = cfg.get("_s3_bucket") or ""
    if not bucket:
        raise ValueError("S3-Bucket nicht konfiguriert")
    key = _s3_key(cfg, subfolder, filename)
    s3 = _s3_client(cfg)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )


def hosted_upload_folder(
    cfg: dict, files: list[tuple[str, bytes, str]], folder_path: str
) -> None:
    backend = (cfg.get("hosted_backend") or "local").lower()
    if backend == "s3":
        hosted_upload_folder_s3(cfg, files, folder_path)
    else:
        hosted_upload_folder_local(cfg, files, folder_path)


def hosted_download(cfg: dict, folder_path: str, filename: str) -> bytes:
    backend = (cfg.get("hosted_backend") or "local").lower()
    if backend == "s3":
        return hosted_download_s3(cfg, folder_path, filename)
    return hosted_download_local(cfg, folder_path, filename)


def hosted_upload_single(
    cfg: dict,
    filename: str,
    content: bytes,
    content_type: str,
    subfolder: str,
) -> None:
    backend = (cfg.get("hosted_backend") or "local").lower()
    if backend == "s3":
        hosted_upload_single_s3(cfg, filename, content, content_type, subfolder)
    else:
        hosted_upload_single_local(cfg, filename, content, content_type, subfolder)


def hosted_check_connectivity(cfg: dict) -> None:
    """Wirft bei Konfigurations-/Pfadfehlern."""
    backend = (cfg.get("hosted_backend") or "local").lower()
    if backend == "s3":
        bucket = cfg.get("_s3_bucket") or ""
        if not bucket:
            raise ValueError("SECURESEND_S3_BUCKET fehlt")
        _s3_client(cfg).head_bucket(Bucket=bucket)
        return
    root = Path(cfg.get("_storage_root", "")).resolve()
    if not root.is_dir():
        raise ValueError(f"SECURESEND_STORAGE_ROOT existiert nicht: {root}")


def hosted_presigned_url(cfg: dict, key: str, days: int) -> str:
    """Einzelfreigabe (Markdown o. ä.): Presigned GET."""
    bucket = cfg.get("_s3_bucket") or ""
    s3 = _s3_client(cfg)
    expiry = max(1, days) * 24 * 3600
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry,
    )
