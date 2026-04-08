"""
cloud/schemas/organization.py — Organization request/response schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class OrgCreate(BaseModel):
    name: str
    slug: str
    is_active: bool = True
    settings_json: Optional[dict[str, Any]] = None
    reseller_id: Optional[str] = None  # required for superadmin


class OrgUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None
    settings_json: Optional[dict[str, Any]] = None


class OrgRead(BaseModel):
    id: str
    reseller_id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    settings_json: Optional[dict[str, Any]]

    model_config = {"from_attributes": True}


# ---- Org settings sub-schema (what lives inside settings_json) ----


class SmtpSettings(BaseModel):
    host: str = ""
    port: int = 587
    mode: str = "starttls"  # none | starttls | ssl
    user: str = ""
    password: str = ""
    from_addr: str = ""
    from_name: str = ""


class OrgSettings(BaseModel):
    smtp: Optional[SmtpSettings] = None
    signature: str = ""
    expiry_days: int = 7
    branding_color: str = "#2563eb"
    default_subject: str = "Ihr sicheres Dokument"

    # Security levels configuration
    allowed_security_levels: list[str] = [
        "secure",
        "extended",
    ]  # default: only secure and extended
    default_security_level: str = "secure"
    storage_preference: str = "customer_cloud"  # "customer_cloud" or "securesend_cloud"
