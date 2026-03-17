"""
cloud/routers/templates.py — Message template endpoints.

GET    /templates        → list org + personal templates for current user
POST   /templates        → create template (personal or org-level)
DELETE /templates/{id}   → delete template (owner only)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import org_user_required
from models.shared import MsgTemplate
from models.user import User, UserRole
from schemas.shared import MsgTemplateCreate, MsgTemplateRead

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[MsgTemplateRead])
async def list_templates(
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> list[MsgTemplateRead]:
    """Return org-level templates + personal templates for the current user."""
    result = await db.execute(
        select(MsgTemplate)
        .where(
            or_(
                MsgTemplate.org_id == current_user.org_id,
                MsgTemplate.user_id == current_user.id,
            )
        )
        .order_by(MsgTemplate.name)
    )
    return [MsgTemplateRead.model_validate(t) for t in result.scalars().all()]


@router.post("", response_model=MsgTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: MsgTemplateCreate,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> MsgTemplateRead:
    """
    Create a template.
    - personal: set user_id = current_user.id, org_id = None
    - org-level: set org_id = current_user.org_id, user_id = None
                 (only org_admin+ may create org templates)
    """
    is_org = body.org_id is not None

    if is_org:
        if current_user.role not in (UserRole.org_admin, UserRole.reseller_admin, UserRole.superadmin):
            raise HTTPException(status_code=403, detail="Nur Org-Admins dürfen Org-Vorlagen anlegen")
        org_id = current_user.org_id
        user_id = None
    else:
        org_id = None
        user_id = current_user.id

    t = MsgTemplate(
        org_id=org_id,
        user_id=user_id,
        name=body.name,
        content=body.content,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return MsgTemplateRead.model_validate(t)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(MsgTemplate).where(MsgTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    # Personal template: only owner may delete
    if t.user_id and t.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    # Org template: only org_admin+ may delete
    if t.org_id:
        if current_user.role not in (UserRole.org_admin, UserRole.reseller_admin, UserRole.superadmin):
            raise HTTPException(status_code=403, detail="Nur Org-Admins dürfen Org-Vorlagen löschen")

    await db.delete(t)
    await db.commit()
