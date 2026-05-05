"""Virtueller CloudProvider „SecureSend Storage“ + Kontingent-Helfer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.hosted_storage import HOSTED_SERVICE_NAME
from models.organization import Organization
from models.reseller import Reseller
from models.shared import CloudProvider

if TYPE_CHECKING:
    pass

DEFAULT_STORAGE_QUOTA_BYTES = 5 * 1024 * 1024 * 1024


def default_org_storage_settings() -> dict:
    return {
        "storage_quota_bytes": DEFAULT_STORAGE_QUOTA_BYTES,
        "storage_used_bytes": 0,
        "storage_preference": "securesend_cloud",
    }


def merge_org_settings_with_storage_defaults(existing: dict | None) -> dict:
    base = default_org_storage_settings()
    if not existing:
        return dict(base)
    out = {**base, **existing}
    for k, v in base.items():
        if k not in existing:
            out[k] = v
    return out


async def resolve_storage_quota_bytes(db: AsyncSession, org: Organization) -> int:
    """Effektives Kontingent in Bytes (Org > Tier > Reseller-Default > 5GB)."""
    sj = org.settings_json or {}
    if sj.get("storage_quota_bytes") is not None:
        try:
            return max(0, int(sj["storage_quota_bytes"]))
        except (TypeError, ValueError):
            pass

    tier_id = sj.get("storage_tier_id")
    if org.reseller_id:
        rr = await db.execute(select(Reseller).where(Reseller.id == org.reseller_id))
        reseller = rr.scalar_one_or_none()
        if reseller and reseller.settings_json:
            rj = reseller.settings_json
            if tier_id:
                for t in rj.get("storage_tiers") or []:
                    if isinstance(t, dict) and t.get("id") == tier_id:
                        gb = t.get("quota_gb")
                        if gb is not None:
                            try:
                                return max(0, int(float(gb) * 1024 * 1024 * 1024))
                            except (TypeError, ValueError):
                                break
            dg = rj.get("default_org_quota_gb")
            if dg is not None:
                try:
                    return max(0, int(float(dg) * 1024 * 1024 * 1024))
                except (TypeError, ValueError):
                    pass

    merged = merge_org_settings_with_storage_defaults(sj)
    return int(merged.get("storage_quota_bytes", DEFAULT_STORAGE_QUOTA_BYTES))


async def ensure_hosted_cloud_provider(db: AsyncSession, org_id: str) -> None:
    """Legt SecureSend-Hosted-Anbieter an, falls aktiviert und noch nicht vorhanden."""
    if not settings.SECURESEND_STORAGE_ENABLED:
        return

    existing = await db.execute(
        select(CloudProvider).where(
            CloudProvider.org_id == org_id,
            CloudProvider.service == HOSTED_SERVICE_NAME,
        )
    )
    prov_existing = existing.scalar_one_or_none()
    if prov_existing:
        if prov_existing.name != "SecureSend Storage":
            prov_existing.name = "SecureSend Storage"
        return

    r_all = await db.execute(
        select(CloudProvider).where(
            CloudProvider.org_id == org_id,
            CloudProvider.is_active == True,  # noqa: E712
        )
    )
    others = [p for p in r_all.scalars().all() if p.service != HOSTED_SERVICE_NAME]
    is_default = len(others) == 0

    prov = CloudProvider(
        org_id=org_id,
        name="SecureSend Storage",
        service=HOSTED_SERVICE_NAME,
        config_json={},
        is_default=is_default,
        is_active=True,
    )
    db.add(prov)
    await db.flush()


def is_hosted_provider_row(p: CloudProvider) -> bool:
    return p.service == HOSTED_SERVICE_NAME


async def resolve_send_cloud_provider(
    db: AsyncSession,
    org_id: str,
    org_settings: dict,
    provider_id: Optional[str],
) -> CloudProvider:
    """Wählt den Speicher für /send gemäß storage_preference und Formular."""
    await ensure_hosted_cloud_provider(db, org_id)

    r = await db.execute(
        select(CloudProvider).where(
            CloudProvider.org_id == org_id,
            CloudProvider.is_active == True,  # noqa: E712
        )
    )
    all_p = list(r.scalars().all())
    hosted = next((p for p in all_p if p.service == HOSTED_SERVICE_NAME), None)
    customer = [p for p in all_p if p.service != HOSTED_SERVICE_NAME]

    if provider_id:
        sel = next((p for p in all_p if p.id == provider_id), None)
        if not sel:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=404, detail="Cloud-Anbieter nicht gefunden")
        return sel

    pref = (org_settings.get("storage_preference") or "securesend_cloud").strip()

    if pref == "securesend_cloud":
        if not hosted:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail="SecureSend Storage ist nicht aktiviert oder nicht angelegt",
            )
        return hosted

    if pref == "customer_cloud":
        d = next((p for p in customer if p.is_default), None)
        if d:
            return d
        if hosted:
            return hosted
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail="Kein Kunden-Cloud-Anbieter konfiguriert",
        )

    # user_choice: Standard-Anbieter (irgendein is_default), sonst Hosted
    d = next((p for p in all_p if p.is_default), None)
    if d:
        return d
    if hosted:
        return hosted
    from fastapi import HTTPException, status

    raise HTTPException(
        status_code=status.HTTP_424_FAILED_DEPENDENCY,
        detail="Kein Standard-Cloud-Anbieter für diese Organisation konfiguriert",
    )
