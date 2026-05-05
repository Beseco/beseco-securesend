"""
Löscht abgelaufene History-Einträge und zugehörige Dateien (SecureSend Hosted).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hosted_cfg import merge_hosted_storage_cfg
from models.organization import Organization
from models.shared import CloudProvider, Guest, History
from models.user import User
from services.hosted_provider import (
    merge_org_settings_with_storage_defaults,
    resolve_storage_quota_bytes,
)

from core.hosted_storage import HOSTED_SERVICE_NAME, hosted_delete_folder_or_file

log = logging.getLogger("securesend.cleanup")


async def _delete_hosted_payload(session: AsyncSession, h: History) -> bool:
    """True wenn Speicher bereinigt oder nichts zu tun; False bei Fehler."""
    folder = h.storage_folder_path
    single_fn = h.storage_delete_filename
    if not folder and h.encrypted_files_json:
        folder = h.encrypted_files_json.get("folder_path")
        single_fn = None

    if not folder:
        log.debug("History %s: kein storage_folder_path, überspringe Storage-Löschung", h.id)
        return True

    user_result = await session.execute(select(User).where(User.id == h.user_id))
    user = user_result.scalar_one_or_none()
    if not user or not user.org_id:
        return True

    org_result = await session.execute(
        select(Organization).where(Organization.id == user.org_id)
    )
    org_ent = org_result.scalar_one_or_none()
    if not org_ent:
        return True

    pid = h.cloud_provider_id
    if h.encrypted_files_json and h.encrypted_files_json.get("provider_id"):
        pid = pid or h.encrypted_files_json.get("provider_id")

    provider = None
    if pid:
        pr = await session.execute(
            select(CloudProvider).where(
                CloudProvider.id == pid,
                CloudProvider.org_id == user.org_id,
            )
        )
        provider = pr.scalar_one_or_none()
    if not provider:
        pr2 = await session.execute(
            select(CloudProvider).where(
                CloudProvider.org_id == user.org_id,
                CloudProvider.is_active == True,  # noqa: E712
            )
        )
        cands = list(pr2.scalars().all())
        provider = next((p for p in cands if p.is_default), cands[0] if cands else None)

    if not provider or provider.service != HOSTED_SERVICE_NAME:
        log.info(
            "History %s: Provider %s — kein automatisches Löschen, nur DB-Eintrag",
            h.id,
            provider.service if provider else None,
        )
        return True

    org_settings_merged = merge_org_settings_with_storage_defaults(org_ent.settings_json)
    used_bytes = int(org_settings_merged.get("storage_used_bytes", 0))
    quota_bytes = await resolve_storage_quota_bytes(session, org_ent)

    cfg: dict = dict(provider.config_json or {})
    cfg["service"] = provider.service
    cfg = merge_hosted_storage_cfg(
        cfg,
        user.org_id,
        quota_used=used_bytes,
        quota_total=quota_bytes,
    )

    try:
        hosted_delete_folder_or_file(cfg, folder, single_fn)
    except Exception as exc:
        log.warning("Hosted-Löschung für History %s fehlgeschlagen: %s", h.id, exc)
        return False

    return True


async def purge_expired_history_batch(session: AsyncSession, limit: int = 40) -> int:
    """Verarbeitet bis zu ``limit`` abgelaufene Einträge. Gibt Anzahl gelöschter Zeilen zurück."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    result = await session.execute(
        select(History)
        .where(
            History.purged_at.is_(None),
            History.is_revoked == False,  # noqa: E712
            History.expires_at.is_not(None),
            History.expires_at < now,
        )
        .order_by(History.expires_at.asc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    deleted = 0
    for h in rows:
        ok = await _delete_hosted_payload(session, h)
        if not ok:
            continue
        await session.execute(
            update(Guest).where(Guest.history_id == h.id).values(history_id=None)
        )
        await session.delete(h)
        deleted += 1
    if deleted:
        await session.commit()
    return deleted
