"""
cloud/schemas/reseller.py — Reseller request/response schemas.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class ResellerCreate(BaseModel):
    name: str
    slug: str
    contact_email: EmailStr
    is_active: bool = True


class ResellerUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class ResellerRead(BaseModel):
    id: str
    name: str
    slug: str
    contact_email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
