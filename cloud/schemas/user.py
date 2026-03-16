"""
cloud/schemas/user.py — User request/response schemas.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr

from models.user import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: UserRole = UserRole.org_user
    is_active: bool = True


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserRead(BaseModel):
    id: str
    org_id: Optional[str]
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
