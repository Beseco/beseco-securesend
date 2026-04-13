"""
cloud/routers/admin_reseller.py — Reseller admin endpoints (reseller_admin+).

Organisations in reseller:
  GET    /admin/reseller/orgs        → list orgs
  POST   /admin/reseller/orgs        → create org
  PUT    /admin/reseller/orgs/{id}   → update org
  DELETE /admin/reseller/orgs/{id}   → deactivate org (soft delete)

Reseller settings:
  GET    /admin/reseller/settings    → get reseller settings_json
  PUT    /admin/reseller/settings    → update reseller settings_json

Reseller management (superadmin only):
  GET    /admin/resellers            → list all resellers
  POST   /admin/resellers            → create reseller
  PUT    /admin/resellers/{id}       → update reseller
  DELETE /admin/resellers/{id}       → deactivate reseller
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import reseller_admin_required, superadmin_required
from models.organization import Organization
from models.reseller import Reseller
from models.user import User, UserRole
from schemas.organization import OrgCreate, OrgRead, OrgUpdate
from schemas.reseller import ResellerCreate, ResellerRead, ResellerUpdate
from services.audit import log_audit_event, mask_email, merge_actor_fields, redact_exception_message

router = APIRouter(tags=["admin-reseller"])


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_reseller_id(current_user: User) -> Optional[str]:
    """Returns reseller_id for reseller_admin, None for superadmin (all-access)."""
    return current_user.reseller_id if current_user.reseller_id else None


# ── Organisations ──────────────────────────────────────────────────────────────

@router.get("/admin/reseller/orgs", response_model=list[OrgRead])
async def list_reseller_orgs(
    reseller_id: Optional[str] = None,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[OrgRead]:
    # Superadmin may pass explicit reseller_id; reseller_admin uses their own
    if current_user.role == UserRole.superadmin:
        filter_reseller_id = reseller_id  # None = all (legacy), or specific
    else:
        filter_reseller_id = _get_reseller_id(current_user)
    q = select(Organization).order_by(Organization.name)
    if filter_reseller_id:
        q = q.where(Organization.reseller_id == filter_reseller_id)
    result = await db.execute(q)
    orgs = result.scalars().all()
    return [OrgRead.model_validate(o) for o in orgs]


@router.post(
    "/admin/reseller/orgs",
    response_model=OrgRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_reseller_org(
    body: OrgCreate,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> OrgRead:
    reseller_id = _get_reseller_id(current_user)
    # Superadmin must supply reseller_id in the request body
    if not reseller_id:
        if not body.reseller_id:
            raise HTTPException(status_code=400, detail="reseller_id required for superadmin")
        reseller_id = body.reseller_id

    # Slug uniqueness
    existing = await db.execute(select(Organization).where(Organization.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug already in use")

    from services.hosted_provider import (
        ensure_hosted_cloud_provider,
        merge_org_settings_with_storage_defaults,
    )

    merged_settings = merge_org_settings_with_storage_defaults(body.settings_json)
    org = Organization(
        reseller_id=reseller_id,
        name=body.name,
        slug=body.slug,
        is_active=body.is_active,
        settings_json=merged_settings,
    )
    db.add(org)
    await db.flush()
    await ensure_hosted_cloud_provider(db, org.id)
    await db.commit()
    await db.refresh(org)
    return OrgRead.model_validate(org)


@router.put("/admin/reseller/orgs/{org_id}", response_model=OrgRead)
async def update_reseller_org(
    org_id: str,
    body: OrgUpdate,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> OrgRead:
    reseller_id = _get_reseller_id(current_user)
    q = select(Organization).where(Organization.id == org_id)
    if reseller_id:
        q = q.where(Organization.reseller_id == reseller_id)
    result = await db.execute(q)
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    if body.slug is not None and body.slug != org.slug:
        clash = await db.execute(
            select(Organization).where(Organization.slug == body.slug)
        )
        if clash.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Slug already in use")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(org, field, value)

    await db.commit()
    await db.refresh(org)
    return OrgRead.model_validate(org)


@router.delete("/admin/reseller/orgs/{org_id}", response_model=OrgRead)
async def deactivate_reseller_org(
    org_id: str,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> OrgRead:
    """Soft-delete: sets is_active=False rather than removing the row."""
    reseller_id = _get_reseller_id(current_user)
    q = select(Organization).where(Organization.id == org_id)
    if reseller_id:
        q = q.where(Organization.reseller_id == reseller_id)
    result = await db.execute(q)
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    org.is_active = False
    await db.commit()
    await db.refresh(org)
    return OrgRead.model_validate(org)


# ── Reseller Settings ─────────────────────────────────────────────────────────

def _resolve_reseller_id(current_user: User, reseller_id: Optional[str]) -> str:
    """Returns reseller_id to use: from query param (superadmin) or from user."""
    if current_user.role == UserRole.superadmin:
        if not reseller_id:
            raise HTTPException(status_code=400, detail="reseller_id required for superadmin")
        return reseller_id
    if not current_user.reseller_id:
        raise HTTPException(status_code=403, detail="No reseller context")
    return current_user.reseller_id


@router.get("/admin/reseller/settings")
async def get_reseller_settings(
    reseller_id: Optional[str] = None,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rid = _resolve_reseller_id(current_user, reseller_id)
    result = await db.execute(select(Reseller).where(Reseller.id == rid))
    reseller = result.scalar_one_or_none()
    if not reseller:
        raise HTTPException(status_code=404, detail="Reseller not found")
    return reseller.settings_json or {}


@router.put("/admin/reseller/settings")
async def update_reseller_settings(
    body: dict,
    reseller_id: Optional[str] = None,
    current_user: User = Depends(reseller_admin_required()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rid = _resolve_reseller_id(current_user, reseller_id)
    result = await db.execute(select(Reseller).where(Reseller.id == rid))
    reseller = result.scalar_one_or_none()
    if not reseller:
        raise HTTPException(status_code=404, detail="Reseller not found")
    current = dict(reseller.settings_json or {})
    current.update(body)
    reseller.settings_json = current
    await log_audit_event(
        event_type="reseller_settings_updated",
        severity="info",
        status="success",
        target_type="reseller",
        target_id=rid,
        meta_json={
            "updated_keys": sorted(body.keys()),
            "smtp_touched": "smtp" in body,
        },
        **merge_actor_fields(current_user, org_id=None, reseller_id=rid),
        db=db,
        commit=False,
    )
    await db.commit()
    return reseller.settings_json or {}


# ── SMTP Test ─────────────────────────────────────────────────────────────────

@router.post("/admin/reseller/smtp/test")
async def test_reseller_smtp(
    body: dict,
    reseller_id: Optional[str] = None,
    current_user: User = Depends(reseller_admin_required()),
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
    rid = _resolve_reseller_id(current_user, reseller_id)

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
        await log_audit_event(
            event_type="smtp_test_success",
            severity="info",
            status="success",
            meta_json={
                "scope": "reseller",
                "test_recipient_masked": mask_email(test_email),
            },
            **merge_actor_fields(current_user, org_id=None, reseller_id=rid),
            db=db,
            commit=True,
        )
        return {"ok": True}
    except Exception as exc:
        await log_audit_event(
            event_type="smtp_test_failed",
            severity="warning",
            status="failure",
            error_code="smtp_test_exception",
            error_message_redacted=redact_exception_message(exc),
            meta_json={
                "scope": "reseller",
                "test_recipient_masked": mask_email(test_email),
            },
            **merge_actor_fields(current_user, org_id=None, reseller_id=rid),
            db=db,
            commit=True,
        )
        return {"ok": False, "error": str(exc)}


# ── Resellers (superadmin only) ────────────────────────────────────────────────

@router.get("/admin/resellers", response_model=list[ResellerRead])
async def list_resellers(
    current_user: User = Depends(superadmin_required()),
    db: AsyncSession = Depends(get_db),
) -> list[ResellerRead]:
    result = await db.execute(select(Reseller).order_by(Reseller.name))
    return [ResellerRead.model_validate(r) for r in result.scalars().all()]


@router.post(
    "/admin/resellers",
    response_model=ResellerRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_reseller(
    body: ResellerCreate,
    current_user: User = Depends(superadmin_required()),
    db: AsyncSession = Depends(get_db),
) -> ResellerRead:
    existing = await db.execute(select(Reseller).where(Reseller.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug already in use")

    reseller = Reseller(
        name=body.name,
        slug=body.slug,
        contact_email=body.contact_email,
        is_active=body.is_active,
    )
    db.add(reseller)
    await db.commit()
    await db.refresh(reseller)
    return ResellerRead.model_validate(reseller)


@router.put("/admin/resellers/{reseller_id}", response_model=ResellerRead)
async def update_reseller(
    reseller_id: str,
    body: ResellerUpdate,
    current_user: User = Depends(superadmin_required()),
    db: AsyncSession = Depends(get_db),
) -> ResellerRead:
    result = await db.execute(select(Reseller).where(Reseller.id == reseller_id))
    reseller = result.scalar_one_or_none()
    if not reseller:
        raise HTTPException(status_code=404, detail="Reseller not found")

    if body.slug is not None and body.slug != reseller.slug:
        clash = await db.execute(select(Reseller).where(Reseller.slug == body.slug))
        if clash.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Slug already in use")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(reseller, field, value)

    await db.commit()
    await db.refresh(reseller)
    return ResellerRead.model_validate(reseller)


@router.delete("/admin/resellers/{reseller_id}", response_model=ResellerRead)
async def deactivate_reseller(
    reseller_id: str,
    current_user: User = Depends(superadmin_required()),
    db: AsyncSession = Depends(get_db),
) -> ResellerRead:
    """Soft-delete: sets is_active=False."""
    result = await db.execute(select(Reseller).where(Reseller.id == reseller_id))
    reseller = result.scalar_one_or_none()
    if not reseller:
        raise HTTPException(status_code=404, detail="Reseller not found")

    reseller.is_active = False
    await db.commit()
    await db.refresh(reseller)
    return ResellerRead.model_validate(reseller)
