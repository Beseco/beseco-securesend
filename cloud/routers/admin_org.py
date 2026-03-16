"""
cloud/routers/admin_org.py — Organisation admin endpoints (org_admin+).

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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import hash_password, org_admin_required
from models.organization import Organization
from models.shared import CloudProvider
from models.user import User
from schemas.shared import CloudProviderCreate, CloudProviderRead, CloudProviderUpdate
from schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/admin/org", tags=["admin-org"])


# ── Helper ─────────────────────────────────────────────────────────────────────

def _assert_has_org(current_user: User) -> str:
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires an organisation context",
        )
    return current_user.org_id


# ── Users ──────────────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserRead])
async def list_org_users(
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[UserRead]:
    org_id = _assert_has_org(current_user)
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
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    org_id = _assert_has_org(current_user)

    # Prevent privilege escalation
    from models.user import UserRole
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
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=body.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.put("/users/{user_id}", response_model=UserRead)
async def update_org_user(
    user_id: str,
    body: UserUpdate,
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    org_id = _assert_has_org(current_user)
    result = await db.execute(
        select(User).where(User.id == user_id, User.org_id == org_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.email is not None:
        user.email = body.email
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
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    org_id = _assert_has_org(current_user)
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
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[CloudProviderRead]:
    org_id = _assert_has_org(current_user)
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
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> CloudProviderRead:
    org_id = _assert_has_org(current_user)

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
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> CloudProviderRead:
    org_id = _assert_has_org(current_user)
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.org_id == org_id,
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

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

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(provider, field, value)

    await db.commit()
    await db.refresh(provider)
    return CloudProviderRead.model_validate(provider)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    org_id = _assert_has_org(current_user)
    result = await db.execute(
        select(CloudProvider).where(
            CloudProvider.id == provider_id,
            CloudProvider.org_id == org_id,
        )
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(provider)
    await db.commit()


# ── Org Settings ───────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_org_settings(
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _assert_has_org(current_user)
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org.settings_json or {}


@router.put("/settings")
async def update_org_settings(
    body: dict[str, Any],
    current_user: User = Depends(org_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = _assert_has_org(current_user)
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
