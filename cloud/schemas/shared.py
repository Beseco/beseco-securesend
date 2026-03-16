"""
cloud/schemas/shared.py — Schemas for CloudProvider, SmsGateway, Contact,
History, MsgTemplate, EmailTemplate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ── CloudProvider ─────────────────────────────────────────────────────────────

class CloudProviderCreate(BaseModel):
    name: str
    service: str                        # "nextcloud" | "onedrive"
    config_json: dict[str, Any] | None = None
    is_default: bool = False
    is_active: bool = True
    user_id: str | None = None          # omit for org-level


class CloudProviderUpdate(BaseModel):
    name: str | None = None
    service: str | None = None
    config_json: dict[str, Any] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class CloudProviderRead(BaseModel):
    id: str
    org_id: str | None
    user_id: str | None
    name: str
    service: str
    is_default: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── SmsGateway ───────────────────────────────────────────────────────────────

class SmsGatewayCreate(BaseModel):
    name: str
    service: str = "sipgate"
    config_json: dict[str, Any] | None = None
    is_default: bool = False
    is_active: bool = True


class SmsGatewayUpdate(BaseModel):
    name: str | None = None
    service: str | None = None
    config_json: dict[str, Any] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class SmsGatewayRead(BaseModel):
    id: str
    org_id: str
    name: str
    service: str
    is_default: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Contact ───────────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    company: str = ""
    last_name: str = ""
    first_name: str = ""
    mobile: str = ""
    email: str = ""


class ContactUpdate(BaseModel):
    company: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    mobile: str | None = None
    email: str | None = None


class ContactRead(BaseModel):
    id: str
    user_id: str
    company: str
    last_name: str
    first_name: str
    mobile: str
    email: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── History ───────────────────────────────────────────────────────────────────

class HistoryRead(BaseModel):
    id: str
    user_id: str
    to_email: str
    to_phone: str
    filename: str
    share_url: str
    provider: str
    expiry_days: int
    security_level: str
    ip_address: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── MsgTemplate ───────────────────────────────────────────────────────────────

class MsgTemplateCreate(BaseModel):
    name: str
    content: str
    org_id: str | None = None
    user_id: str | None = None


class MsgTemplateUpdate(BaseModel):
    name: str | None = None
    content: str | None = None


class MsgTemplateRead(BaseModel):
    id: str
    org_id: str | None
    user_id: str | None
    name: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── EmailTemplate ─────────────────────────────────────────────────────────────

class EmailTemplateCreate(BaseModel):
    name: str
    html_body: str
    is_default: bool = False


class EmailTemplateUpdate(BaseModel):
    name: str | None = None
    html_body: str | None = None
    is_default: bool | None = None


class EmailTemplateRead(BaseModel):
    id: str
    org_id: str
    name: str
    html_body: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Send ──────────────────────────────────────────────────────────────────────

class SendRequest(BaseModel):
    """Used when sending a plain text/markdown message (non-file mode)."""
    to_email: str
    to_phone: str = ""
    subject: str = "Ihr sicheres Dokument"
    message: str
    expiry_days: int = 7
    security_level: str = "standard"
    send_sms: bool = False
    send_email: bool = True
    provider_id: str | None = None      # override default org provider


class SendResponse(BaseModel):
    share_url: str
    filename: str
    provider: str
    expiry_days: int
    history_id: str
