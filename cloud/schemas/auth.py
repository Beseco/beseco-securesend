"""
cloud/schemas/auth.py — Auth request/response schemas.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str
    org_id: Optional[str] = None
    reseller_id: Optional[str] = None
    is_active: bool


MeResponse.model_rebuild()
