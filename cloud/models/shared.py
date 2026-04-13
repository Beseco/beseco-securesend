"""
cloud/models/shared.py — Shared ORM models:
  CloudProvider, SmsGateway, Contact, History, MsgTemplate, EmailTemplate
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Optional

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
    org_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # z. B. "nextcloud" | "owncloud" | "onedrive"
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Optional[Organization]"] = relationship(  # noqa: F821
        "Organization",
        primaryjoin="CloudProvider.org_id == Organization.id",
        back_populates="cloud_providers",
    )
    owner_user: Mapped["Optional[User]"] = relationship(  # noqa: F821
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
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False, default="sipgate")
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
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
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_phone: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    message_preview: Mapped[Optional[str]] = mapped_column(String(600), nullable=True)
    share_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    expiry_days: Mapped[int] = mapped_column(nullable=False, default=7)
    # Absolutes Ablaufdatum (für Anzeige, Verlängerung, Cleanup)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # Lesebestätigung (Gast-Portal)
    security_level: Mapped[str] = mapped_column(
        String(50), nullable=False, default="standard"
    )
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    # For client-side encrypted files (advanced/maximal)
    encrypted_files_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    tracking_token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=lambda: secrets.token_urlsafe(32),
        unique=True,
        index=True,
    )
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    link_clicked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Download tracking
    download_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_downloaded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    # Revoke functionality
    is_revoked: Mapped[bool] = mapped_column(nullable=False, default=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Guest-Zuordnung (nach Registration)
    guest_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("guests.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Multiple files support
    files_json: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    # Access tracking
    access_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_access_count: Mapped[Optional[int]] = mapped_column(nullable=True)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    # Für serverseitiges Löschen (Hosted/MinIO); optional Einzeldatei
    storage_folder_path: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    storage_delete_filename: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    cloud_provider_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )

    user: Mapped["User"] = relationship("User", back_populates="history")  # noqa: F821
    guest: Mapped["Optional[Guest]"] = relationship(  # noqa: F821
        "Guest",
        primaryjoin="History.guest_id == Guest.id",
    )

    def __repr__(self) -> str:
        return f"<History id={self.id!r} filename={self.filename!r}>"


class DownloadLog(Base):
    """Detailed download logs for audit trail."""

    __tablename__ = "download_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    history_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("history.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    filename: Mapped[str] = mapped_column(String(500), nullable=False, default="")


class MsgTemplate(Base):
    """SMS/message template (org-level or user-level)."""

    __tablename__ = "msg_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    organization: Mapped["Optional[Organization]"] = relationship(  # noqa: F821
        "Organization",
        primaryjoin="MsgTemplate.org_id == Organization.id",
        back_populates="msg_templates",
    )
    owner_user: Mapped["Optional[User]"] = relationship(  # noqa: F821
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
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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


class EmailVerification(Base):
    """Pending email verification tokens for self-registration."""

    __tablename__ = "email_verifications"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # stored as UTC-naive datetime
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")  # noqa: F821

    def __repr__(self) -> str:
        return f"<EmailVerification user_id={self.user_id!r}>"


class PasswordReset(Base):
    """Password-Reset-Token für 'Passwort vergessen'-Funktion."""

    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")  # noqa: F821

    def __repr__(self) -> str:
        return f"<PasswordReset user_id={self.user_id!r}>"


class PhoneRequest(Base):
    """Request to a contact to submit their mobile phone number."""

    __tablename__ = "phone_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    sender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # pending | fulfilled | expired
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    sender: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys="PhoneRequest.sender_id"
    )
    contact: Mapped["Contact"] = relationship(  # noqa: F821
        "Contact", foreign_keys="PhoneRequest.contact_id"
    )

    def __repr__(self) -> str:
        return f"<PhoneRequest id={self.id!r} status={self.status!r}>"


class UploadRequest(Base):
    """Request to a recipient to upload files securely (dropbox)."""

    __tablename__ = "upload_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    sender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # pending | fulfilled | expired
    result_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    sender: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys="UploadRequest.sender_id"
    )

    def __repr__(self) -> str:
        return f"<UploadRequest id={self.id!r} status={self.status!r}>"


class Guest(Base):
    """Gast-Konto für Empfänger-Portal (nach erstem Login erstellt)."""

    __tablename__ = "guests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    email_code: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    email_code_expires: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Zuordnung zu History-Eintrag (für Zugriff)
    history_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("history.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    history: Mapped["Optional[History]"] = relationship(  # noqa: F821
        "History",
        primaryjoin="Guest.history_id == History.id",
    )

    def __repr__(self) -> str:
        return f"<Guest id={self.id!r} email={self.email!r}>"


class AuditEvent(Base):
    """Admin-/Debug-Audit (ohne Nachrichten- oder Dateiinhalte)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    actor_role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    target_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message_redacted: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    meta_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<AuditEvent id={self.id!r} type={self.event_type!r}>"
