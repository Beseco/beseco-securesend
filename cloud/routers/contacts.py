"""
cloud/routers/contacts.py — Contact CRUD + vCard import/export.

GET    /contacts           → list own contacts
POST   /contacts           → create contact
PUT    /contacts/{id}      → update contact
DELETE /contacts/{id}      → delete contact
GET    /contacts/export.vcf → download as vCard file
POST   /contacts/import    → upload vCard file, import contacts
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user, org_user_required
from models.shared import Contact
from models.user import User
from schemas.shared import ContactCreate, ContactRead, ContactUpdate

router = APIRouter(prefix="/contacts", tags=["contacts"])


async def _get_own_contact(
    contact_id: str, current_user: User, db: AsyncSession
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == contact_id,
            Contact.user_id == current_user.id,
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.get("", response_model=list[ContactRead])
async def list_contacts(
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> list[ContactRead]:
    result = await db.execute(
        select(Contact)
        .where(Contact.user_id == current_user.id)
        .order_by(Contact.last_name, Contact.first_name)
    )
    contacts = result.scalars().all()
    return [ContactRead.model_validate(c) for c in contacts]


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactCreate,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> ContactRead:
    contact = Contact(user_id=current_user.id, **body.model_dump())
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return ContactRead.model_validate(contact)


@router.get("/export.vcf")
async def export_vcf(
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return all user contacts as a vCard 3.0 file download."""
    result = await db.execute(
        select(Contact)
        .where(Contact.user_id == current_user.id)
        .order_by(Contact.last_name)
    )
    contacts = result.scalars().all()

    from core.vcf import contacts_to_vcf  # type: ignore[import]

    vcf_data = contacts_to_vcf(
        [
            {
                "first_name": c.first_name,
                "last_name": c.last_name,
                "company": c.company,
                "mobile": c.mobile,
                "email": c.email,
            }
            for c in contacts
        ]
    )
    return Response(
        content=vcf_data.encode("utf-8"),
        media_type="text/vcard",
        headers={"Content-Disposition": "attachment; filename=contacts.vcf"},
    )


@router.post("/import", response_model=list[ContactRead], status_code=status.HTTP_201_CREATED)
async def import_vcf(
    file: UploadFile = File(...),
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> list[ContactRead]:
    """Parse an uploaded vCard file and insert all contacts."""
    raw = await file.read()
    text = raw.decode("utf-8", errors="replace")

    from core.vcf import parse_vcf  # type: ignore[import]

    parsed = parse_vcf(text)
    new_contacts = []
    for c in parsed:
        contact = Contact(
            user_id=current_user.id,
            company=c.get("company", ""),
            last_name=c.get("last_name", ""),
            first_name=c.get("first_name", ""),
            mobile=c.get("mobile", ""),
            email=c.get("email", ""),
        )
        db.add(contact)
        new_contacts.append(contact)

    await db.commit()
    for c in new_contacts:
        await db.refresh(c)

    return [ContactRead.model_validate(c) for c in new_contacts]


@router.put("/{contact_id}", response_model=ContactRead)
async def update_contact(
    contact_id: str,
    body: ContactUpdate,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> ContactRead:
    contact = await _get_own_contact(contact_id, current_user, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(contact, field, value)
    await db.commit()
    await db.refresh(contact)
    return ContactRead.model_validate(contact)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str,
    current_user: User = Depends(org_user_required()),
    db: AsyncSession = Depends(get_db),
) -> None:
    contact = await _get_own_contact(contact_id, current_user, db)
    await db.delete(contact)
    await db.commit()
