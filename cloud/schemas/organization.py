"""
cloud/schemas/organization.py — Organization request/response schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class OrgCreate(BaseModel):
    name: str
    slug: str
    is_active: bool = True
    settings_json: dict[str, Any] | None = None


class OrgUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    is_active: bool | None = None
    settings_json: dict[str, Any] | None = None


class OrgRead(BaseModel):
    id: str
    reseller_id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    settings_json: dict[str, Any] | None

    model_config = {"from_attributes": True}


# ---- Org settings sub-schema (what lives inside settings_json) ----

class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    mode: str = "starttls"          # none | starttls | ssl
    user: str = ""
    password: str = ""
    from_addr: str = ""
    from_name: str = ""


class OrgSettings(BaseModel):
    smtp: SmtpSettings | None = None
    signature: str = ""
    expiry_days: int = 7
    branding_color: str = "#2563eb"
    default_subject: str = "Ihr sicheres Dokument"
