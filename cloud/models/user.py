"""
cloud/models/user.py — User ORM model with role enum.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    reseller_admin = "reseller_admin"
    org_admin = "org_admin"
    org_user = "org_user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.org_user
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    organization: Mapped["Organization | None"] = relationship(  # noqa: F821
        "Organization", back_populates="users"
    )
    contacts: Mapped[list["Contact"]] = relationship(  # noqa: F821
        "Contact", back_populates="user", cascade="all, delete-orphan"
    )
    history: Mapped[list["History"]] = relationship(  # noqa: F821
        "History", back_populates="user", cascade="all, delete-orphan"
    )
    cloud_providers: Mapped[list["CloudProvider"]] = relationship(  # noqa: F821
        "CloudProvider",
        primaryjoin="and_(CloudProvider.user_id == User.id)",
        back_populates="owner_user",
    )
    msg_templates: Mapped[list["MsgTemplate"]] = relationship(  # noqa: F821
        "MsgTemplate",
        primaryjoin="and_(MsgTemplate.user_id == User.id)",
        back_populates="owner_user",
    )

    @property
    def reseller_id(self) -> str | None:
        """Convenience accessor — resolved via the organization relationship."""
        if self.organization:
            return self.organization.reseller_id
        return None

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} role={self.role}>"
