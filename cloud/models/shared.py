"""
cloud/models/shared.py — Shared ORM models:
  CloudProvider, SmsGateway, Contact, History, MsgTemplate, EmailTemplate
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class CloudProvider(Base):
    """Cloud storage provider config (Nextcloud / OneDrive)."""

    __tablename__ = "cloud_providers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Either org-level or user-level (both nullable for flexibility)
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False)  # "nextcloud" | "onedrive"
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization",
        primaryjoin="CloudProvider.org_id == Organization.id",
        back_populates="cloud_providers",
    )
    owner_user: Mapped["User | None"] = relationship(  # noqa: F821
        "User",
        primaryjoin="CloudProvider.user_id == User.id",
        back_populates="cloud_providers",
    )

    def __repr__(self) -> str:
        return f"<CloudProvider id={self.id!r} service={self.service!r}>"


class SmsGateway(Base):
    """SMS gateway config (sipgate)."""

    __tablename__ = "sms_gateways"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False, default="sipgate")
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="sms_gateways"
    )

    def __repr__(self) -> str:
        return f"<SmsGateway id={self.id!r} service={self.service!r}>"


class Contact(Base):
    """User contact book entry."""

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    first_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mobile: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="contacts")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Contact id={self.id!r} last_name={self.last_name!r}>"


class History(Base):
    """Send history entry."""

    __tablename__ = "history"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    share_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    expiry_days: Mapped[int] = mapped_column(nullable=False, default=7)
    security_level: Mapped[str] = mapped_column(String(50), nullable=False, default="standard")
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="history")  # noqa: F821

    def __repr__(self) -> str:
        return f"<History id={self.id!r} filename={self.filename!r}>"


class MsgTemplate(Base):
    """SMS/message template (org-level or user-level)."""

    __tablename__ = "msg_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization",
        primaryjoin="MsgTemplate.org_id == Organization.id",
        back_populates="msg_templates",
    )
    owner_user: Mapped["User | None"] = relationship(  # noqa: F821
        "User",
        primaryjoin="MsgTemplate.user_id == User.id",
        back_populates="msg_templates",
    )

    def __repr__(self) -> str:
        return f"<MsgTemplate id={self.id!r} name={self.name!r}>"


class EmailTemplate(Base):
    """HTML email template at org level."""

    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="email_templates"
    )

    def __repr__(self) -> str:
        return f"<EmailTemplate id={self.id!r} name={self.name!r}>"
