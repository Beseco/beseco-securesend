"""Reicht serverseitige Hosted-Storage-Settings in die storage-cfg ein."""

from __future__ import annotations

from typing import Any

from config import settings

from core.hosted_storage import HOSTED_SERVICE_NAME


def is_hosted_service(service: str | None) -> bool:
    return service == HOSTED_SERVICE_NAME


def merge_hosted_storage_cfg(
    cfg: dict[str, Any],
    org_id: str,
    *,
    quota_used: int | None = None,
    quota_total: int | None = None,
) -> dict[str, Any]:
    """Kopie von cfg mit Feldern für core.hosted_storage / get_provider_status."""
    out = dict(cfg)
    if out.get("service") != HOSTED_SERVICE_NAME:
        return out
    out["_org_id"] = org_id
    out["_storage_root"] = settings.SECURESEND_STORAGE_ROOT
    out["hosted_backend"] = settings.SECURESEND_STORAGE_BACKEND
    out["_s3_endpoint"] = settings.SECURESEND_S3_ENDPOINT
    out["_s3_bucket"] = settings.SECURESEND_S3_BUCKET
    out["_s3_access_key"] = settings.SECURESEND_S3_ACCESS_KEY
    out["_s3_secret_key"] = settings.SECURESEND_S3_SECRET_KEY
    out["_s3_region"] = settings.SECURESEND_S3_REGION
    if quota_used is not None:
        out["_hosted_quota_used"] = quota_used
    if quota_total is not None:
        out["_hosted_quota_total"] = quota_total
    return out
