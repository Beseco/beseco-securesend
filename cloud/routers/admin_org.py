"""
cloud/routers/admin_org.py — Organisation admin endpoints (org_admin+).
# Fixes: first_name/last_name gespeichert, Welcome-E-Mail beim Anlegen

Users:
  GET    /admin/org/users          → list users in org
  POST   /admin/org/users          → create user
  PUT    /admin/org/users/{id}     → update user
  DELETE /admin/org/users/{id}     → delete user

Cloud Providers:
  GET    /admin/org/providers      → list org providers
  POST   /admin/org/providers      → create provider
  PUT    /admin/org/providers/{id} → update provider
  DELETE /admin/org/providers/{id} → delete provider

Org Settings:
  GET    /admin/org/settings       → get org settings_json
  PUT    /admin/org/settings       → update org settings_json
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import hash_password, org_admin_required
from hosted_cfg import merge_hosted_storage_cfg
from models.organization import Organization
from models.shared import CloudProvider, SmsGateway
from models.user import User, UserRole
from services.hosted_provider import (
    ensure_hosted_cloud_provider,
    merge_org_settings_with_storage_defaults,
    resolve_storage_quota_bytes,
)
from core.smtp_config import get_env_smtp_cfg

from core.hosted_storage import HOSTED_SERVICE_NAME
from schemas.shared import (
    CloudProviderCreate, CloudProviderRead, CloudProviderUpdate,
    SmsGatewayCreate, SmsGatewayRead, SmsGatewayUpdate,
)
from schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/admin/org", tags=["admin-org"])


# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve_org_id(current_user: User, org_id: Optional[str] = None) -> str:
    """
    For superadmin: use explicit org_id param.
    For org_admin/reseller_admin: use their own org_id.
    """
    if current_user.role == UserRole.superadmin:
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="org_id query parameter required for superadmin",
            )
        return org_id
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires an organisation context",
        )
    return current_user.org_id


# ── Users ──────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserRead])
async def list_org_users(
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[UserRead]:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(User)
        .where(User.org_id == org_id)
        .order_by(User.email)
    )
    users = result.scalars().all()
    return [UserRead.model_validate(u) for u in users]


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_org_user(
    body: UserCreate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    org_id = _resolve_org_id(current_user, org_id)

    # Prevent privilege escalation
    from dependencies import _ROLE_RANK

    if _ROLE_RANK.get(body.role, 0) > _ROLE_RANK.get(current_user.role, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create a user with a higher role than your own",
        )

    # Check for duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        org_id=org_id,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=body.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Welcome-E-Mail senden (wenn SMTP konfiguriert)
    try:
        import asyncio
        from core.email import send_email  # type: ignore[import]
        from models.organization import Organization
        from models.reseller import Reseller

        org_result = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_result.scalar_one_or_none()
        if org:
            smtp_cfg = (org.settings_json or {}).get("smtp")
            if not smtp_cfg:
                res = await db.execute(select(Reseller).where(Reseller.id == org.reseller_id))
                reseller = res.scalar_one_or_none()
                if reseller and reseller.settings_json:
                    smtp_cfg = reseller.settings_json.get("smtp")
            if not smtp_cfg:
                smtp_cfg = get_env_smtp_cfg()
            if smtp_cfg:
                display_name = ""
                if body.first_name or body.last_name:
                    display_name = f"{body.first_name or ''} {body.last_name or ''}".strip()
                greeting = f"Hallo {display_name}," if display_name else "Hallo,"
                html_body = (
                    f"<p>{greeting}</p>"
                    f"<p>Ihr Konto bei <strong>{org.name} – SecureSend Cloud</strong> wurde erstellt.</p>"
                    f"<p><strong>E-Mail:</strong> {body.email}<br/>"
                    f"<strong>Passwort:</strong> {body.password}</p>"
                    f"<p>Bitte melden Sie sich unter <a href='https://securesend.io/ui/login'>SecureSend Cloud</a> an "
                    f"und ändern Sie Ihr Passwort nach dem ersten Login.</p>"
                    f"<p style='color:#64748b;font-size:0.85em;'>SecureSend Cloud – Sicheres Senden</p>"
                )
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: send_email(smtp_cfg, body.email, f"Ihr neues Konto – {org.name} SecureSend", html_body),
                )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Welcome-E-Mail fehlgeschlagen für %s: %s", body.email, exc)

    return UserRead.model_validate(user)


@router.put("/users/{user_id}", response_model=UserRead)
async def update_org_user(
    user_id: str,
    body: UserUpdate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == org_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.email is not None:
        user.email = body.email
    if body.first_name is not None:
        user.first_name = body.first_name
    if body.last_name is not None:
        user.last_name = body.last_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        from dependencies import _ROLE_RANK
        if _ROLE_RANK.get(body.role, 0) > _ROLE_RANK.get(current_user.role, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot assign a role higher than your own",
            )
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_user(
    user_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    org_id = _resolve_org_id(current_user, org_id)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == org_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()


# ── Cloud Providers ────────────────────────────────────────────────────────────

@router.get("/providers", response_model=list[CloudProviderRead])
async def list_providers(
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[CloudProviderRead]:
    org_id = _resolve_org_id(current_user, org_id)
    await ensure_hosted_cloud_provider(db, org_id)
    await db.commit()
    result = await db.execute(
        select(CloudProvider)
        .where(CloudProvider.org_id == org_id)
        .order_by(CloudProvider.name)
    )
    providers = result.scalars().all()
    return [CloudProviderRead.model_validate(p) for p in providers]


@router.post("/providers", response_model=CloudProviderRead, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: CloudProviderCreate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> CloudProviderRead:
    org_id = _resolve_org_id(current_user, org_id)
    if body.service == HOSTED_SERVICE_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SecureSend Storage wird automatisch angelegt und kann nicht manuell erstellt werden.",
        )

    # If this is the new default, clear existing defaults first
    if body.is_default:
        existing = await db.execute(
            select(CloudProvider).where(
                CloudProvider.org_id == org_id,
                CloudProvider.is_default == True,  # noqa: E712
            )
        )
        for p in existing.scalars().all():
            p.is_default = False

    provider = CloudProvider(
        org_id=org_id,
        user_id=body.user_id,
        name=body.name,
        service=body.service,
        config_json=body.config_json,
        is_default=body.is_default,
        is_active=body.is_active,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return CloudProviderRead.model_validate(provider)


@router.put("/providers/{provider_id}", response_model=CloudProviderRead)
async def update_provider(
    provider_id: str,
    body: CloudProviderUpdate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> CloudProviderRead:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.org_id == org_id,
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.service == HOSTED_SERVICE_NAME:
        patch = body.model_dump(exclude_none=True)
        if patch.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SecureSend Storage kann nicht deaktiviert werden.",
            )
        if patch.get("service") and patch["service"] != HOSTED_SERVICE_NAME:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Der Diensttyp von SecureSend Storage kann nicht geändert werden.",
            )

    if body.is_default is True:
        existing = await db.execute(
            select(CloudProvider).where(
                CloudProvider.org_id == org_id,
                CloudProvider.is_default == True,  # noqa: E712
                CloudProvider.id != provider_id,
            )
        )
        for p in existing.scalars().all():
            p.is_default = False

    update_data = body.model_dump(exclude_none=True)
    if provider.service == HOSTED_SERVICE_NAME:
        update_data.pop("name", None)

    for field, value in update_data.items():
        setattr(provider, field, value)

    await db.commit()
    await db.refresh(provider)
    return CloudProviderRead.model_validate(provider)


@router.get("/providers/{provider_id}/status")
async def provider_status(
    provider_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Prüft Verbindung + Quota für einen Cloud-Anbieter."""
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(CloudProvider).where(CloudProvider.id == provider_id, CloudProvider.org_id == org_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    cfg = dict(provider.config_json or {})
    cfg["service"] = provider.service
    if provider.service == HOSTED_SERVICE_NAME:
        org_ent = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org_o = org_ent.scalar_one_or_none()
        merged = merge_org_settings_with_storage_defaults(
            org_o.settings_json if org_o else None
        )
        used = int(merged.get("storage_used_bytes", 0))
        quota = await resolve_storage_quota_bytes(db, org_o) if org_o else 0
        cfg = merge_hosted_storage_cfg(
            cfg, org_id, quota_used=used, quota_total=quota
        )
    import asyncio
    try:
        from core.storage import get_provider_status  # type: ignore[import]
        status_data = await asyncio.get_running_loop().run_in_executor(None, get_provider_status, cfg)
        return status_data
    except Exception as exc:
        return {"ok": False, "service": provider.service, "display_name": None, "quota": None, "error": str(exc)}


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.org_id == org_id,
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if provider.service == HOSTED_SERVICE_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Der integrierte SecureSend Storage kann nicht gelöscht werden.",
        )
    await db.delete(provider)
    await db.commit()


# ── SMS Gateways ───────────────────────────────────────────────────────────────

@router.get("/gateways", response_model=list[SmsGatewayRead])
async def list_gateways(
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[SmsGatewayRead]:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(SmsGateway).where(SmsGateway.org_id == org_id).order_by(SmsGateway.name)
    )
    return [SmsGatewayRead.model_validate(g) for g in result.scalars().all()]


@router.post("/gateways", response_model=SmsGatewayRead, status_code=status.HTTP_201_CREATED)
async def create_gateway(
    body: SmsGatewayCreate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> SmsGatewayRead:
    org_id = _resolve_org_id(current_user, org_id)
    if body.is_default:
        existing = await db.execute(
            select(SmsGateway).where(SmsGateway.org_id == org_id, SmsGateway.is_default == True)  # noqa: E712
        )
        for g in existing.scalars().all():
            g.is_default = False
    gateway = SmsGateway(
        org_id=org_id,
        name=body.name,
        service=body.service,
        config_json=body.config_json,
        is_default=body.is_default,
        is_active=body.is_active,
    )
    db.add(gateway)
    await db.commit()
    await db.refresh(gateway)
    return SmsGatewayRead.model_validate(gateway)


@router.put("/gateways/{gateway_id}", response_model=SmsGatewayRead)
async def update_gateway(
    gateway_id: str,
    body: SmsGatewayUpdate,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> SmsGatewayRead:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(SmsGateway).where(SmsGateway.id == gateway_id, SmsGateway.org_id == org_id)
    )
    gateway = result.scalar_one_or_none()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")
    if body.is_default is True:
        existing = await db.execute(
            select(SmsGateway).where(
                SmsGateway.org_id == org_id,
                SmsGateway.is_default == True,  # noqa: E712
                SmsGateway.id != gateway_id,
            )
        )
        for g in existing.scalars().all():
            g.is_default = False
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(gateway, field, value)
    await db.commit()
    await db.refresh(gateway)
    return SmsGatewayRead.model_validate(gateway)


@router.get("/gateways/{gateway_id}/status")
async def gateway_status(
    gateway_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Prüft sipgate-Credentials, liefert Kontostand + EVN."""
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(SmsGateway).where(SmsGateway.id == gateway_id, SmsGateway.org_id == org_id)
    )
    gateway = result.scalar_one_or_none()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")
    cfg = dict(gateway.config_json or {})
    import asyncio
    try:
        from core.sms import get_sipgate_status  # type: ignore[import]
        status_data = await asyncio.get_running_loop().run_in_executor(None, get_sipgate_status, cfg)
        return status_data
    except Exception as exc:
        return {"ok": False, "balance": None, "history": [], "error": str(exc)}


@router.delete("/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gateway(
    gateway_id: str,
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(
        select(SmsGateway).where(SmsGateway.id == gateway_id, SmsGateway.org_id == org_id)
    )
    gateway = result.scalar_one_or_none()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")
    await db.delete(gateway)
    await db.commit()


# ── Org Settings ───────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_org_settings(
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    data = dict(org.settings_json or {})
    data["org_slug"] = org.slug   # expose slug so the UI can build the registration link
    return data


@router.post("/smtp/test")
async def test_org_smtp(
    body: dict,
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a test e-mail using the supplied SMTP config.

    Body: { smtp: {...}, test_email: "..." }
    Returns: { ok: true } or { ok: false, error: "..." }
    """
    import asyncio
    from core.email import send_email  # type: ignore[import]

    smtp_cfg: dict = body.get("smtp") or {}
    test_email: str = (body.get("test_email") or "").strip()

    if not smtp_cfg.get("host"):
        raise HTTPException(status_code=400, detail="SMTP-Host fehlt")
    if not test_email or "@" not in test_email:
        raise HTTPException(status_code=400, detail="Ungültige Ziel-E-Mail-Adresse")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            send_email,
            smtp_cfg,
            test_email,
            "SecureSend – SMTP-Test erfolgreich",
            """<html><body style="font-family:Arial,sans-serif;color:#1e293b;max-width:600px;margin:0 auto;padding:2rem;">
  <h2 style="color:#1a56db;">✅ SMTP-Test erfolgreich</h2>
  <p>Diese E-Mail bestätigt, dass Ihre SMTP-Konfiguration in SecureSend Cloud korrekt eingerichtet ist.</p>
  <p style="font-size:0.875rem;color:#64748b;">SecureSend Cloud – Sicheres Senden</p>
</body></html>""",
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.put("/settings")
async def update_org_settings(
    body: dict[str, Any],
    org_id: Optional[str] = Query(None),
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _resolve_org_id(current_user, org_id)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    # Merge (do not replace outright so callers can patch individual keys)
    current_settings = dict(org.settings_json or {})
    current_settings.update(body)
    org.settings_json = current_settings

    await db.commit()
    await db.refresh(org)
    return org.settings_json or {}
