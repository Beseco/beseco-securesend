"""
cloud/models/organization.py — Organization ORM model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    reseller_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resellers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # Flexible JSON bag: smtp config, signature, expiry_days, branding, etc.
    settings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # relationships
    reseller: Mapped["Reseller"] = relationship(  # noqa: F821
        "Reseller", back_populates="organizations"
    )
    users: Mapped[list["User"]] = relationship(  # noqa: F821
        "User", back_populates="organization", cascade="all, delete-orphan"
    )
    cloud_providers: Mapped[list["CloudProvider"]] = relationship(  # noqa: F821
        "CloudProvider",
        primaryjoin="and_(CloudProvider.org_id == Organization.id)",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    sms_gateways: Mapped[list["SmsGateway"]] = relationship(  # noqa: F821
        "SmsGateway", back_populates="organization", cascade="all, delete-orphan"
    )
    msg_templates: Mapped[list["MsgTemplate"]] = relationship(  # noqa: F821
        "MsgTemplate",
        primaryjoin="and_(MsgTemplate.org_id == Organization.id)",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    email_templates: Mapped[list["EmailTemplate"]] = relationship(  # noqa: F821
        "EmailTemplate", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id!r} slug={self.slug!r}>"
