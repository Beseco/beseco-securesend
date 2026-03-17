"""
cloud/models/reseller.py — Reseller ORM model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from typing import Optional

from sqlalchemy import Boolean, DateTime, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Reseller(Base):
    __tablename__ = "resellers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # Flexible JSON bag: smtp config, signature, branding, etc.
    settings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # relationships
    organizations: Mapped[list["Organization"]] = relationship(  # noqa: F821
        "Organization", back_populates="reseller", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Reseller id={self.id!r} slug={self.slug!r}>"
