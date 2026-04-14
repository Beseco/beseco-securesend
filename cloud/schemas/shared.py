"""
cloud/schemas/shared.py — Schemas for CloudProvider, SmsGateway, Contact,
History, MsgTemplate, EmailTemplate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


# ── CloudProvider ─────────────────────────────────────────────────────────────

class CloudProviderCreate(BaseModel):
    name: str
    service: str                        # "nextcloud" | "owncloud" | "onedrive" | …
    config_json: Optional[dict[str, Any]] = None
    is_default: bool = False
    is_active: bool = True
    user_id: Optional[str] = None          # omit for org-level


class CloudProviderUpdate(BaseModel):
    name: Optional[str] = None
    service: Optional[str] = None
    config_json: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class CloudProviderRead(BaseModel):
    id: str
    org_id: Optional[str]
    user_id: Optional[str]
    name: str
    service: str
    config_json: Optional[dict[str, Any]] = None
    is_default: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CloudProviderSendOption(BaseModel):
    """Minimal provider info for the send UI (no secrets in config_json)."""

    id: str
    name: str
    service: str
    is_default: bool
    is_active: bool

    model_config = {"from_attributes": True}


# ── SmsGateway ───────────────────────────────────────────────────────────────

class SmsGatewayCreate(BaseModel):
    name: str
    service: str = "sipgate"
    config_json: Optional[dict[str, Any]] = None
    is_default: bool = False
    is_active: bool = True


class SmsGatewayUpdate(BaseModel):
    name: Optional[str] = None
    service: Optional[str] = None
    config_json: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class SmsGatewayRead(BaseModel):
    id: str
    org_id: str
    name: str
    service: str
    config_json: Optional[dict[str, Any]] = None
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
    company: Optional[str] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None


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
    org_id: Optional[str] = None
    user_id: Optional[str] = None


class MsgTemplateUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None


class MsgTemplateRead(BaseModel):
    id: str
    org_id: Optional[str]
    user_id: Optional[str]
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
    name: Optional[str] = None
    html_body: Optional[str] = None
    is_default: Optional[bool] = None


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
    security_level: str = "level2"
    send_sms: bool = False
    send_email: bool = True
    provider_id: Optional[str] = None      # override default org provider


class SendResponse(BaseModel):
    share_url: str
    filename: str
    provider: str
    expiry_days: int
    history_id: str
    recipient_has_guest_account: bool = False
    requested_security_level: Optional[str] = None
    effective_security_level: Optional[str] = None
    level_downgrade_notice: Optional[str] = None
