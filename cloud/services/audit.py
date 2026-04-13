"""
Datenschutzkonformes Audit-Logging (keine Inhalte, keine Passwörter, keine Tokens in Klartext).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.shared import AuditEvent
from models.user import User


def mask_email(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    local, _, domain = email.strip().partition("@")
    if not local:
        return f"***@{domain}"
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[0]}***@{domain}"


def email_domain_hash(email: Optional[str]) -> str:
    """Nur Domain-Hash für Metadaten (kein Rekonstruktionswert)."""
    if not email or "@" not in email:
        return ""
    _l, _, domain = email.strip().lower().partition("@")
    if not domain:
        return ""
    return hashlib.sha256(domain.encode("utf-8")).hexdigest()[:16]


def redact_exception_message(exc: BaseException, max_len: int = 400) -> str:
    s = f"{type(exc).__name__}: {exc}"
    s = re.sub(
        r"(?i)(password|passwd|pwd|secret|token)\s*[:=]\s*\S+",
        r"\1=***",
        s,
    )
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def actor_fields(user: Optional[User]) -> dict[str, Any]:
    if not user:
        return {
            "actor_user_id": None,
            "actor_role": None,
            "org_id": None,
            "reseller_id": None,
        }
    return {
        "actor_user_id": user.id,
        "actor_role": user.role.value,
        "org_id": user.org_id,
        "reseller_id": user.reseller_id,
    }


def merge_actor_fields(user: Optional[User], **overrides: Any) -> dict[str, Any]:
    """actor_fields plus Overrides in einem Dict (für log_audit_event; kein doppeltes org_id)."""
    merged = dict(actor_fields(user))
    merged.update(overrides)
    return merged


async def log_audit_event(
    *,
    event_type: str,
    severity: str = "info",
    status: str = "success",
    actor_user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    org_id: Optional[str] = None,
    reseller_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message_redacted: Optional[str] = None,
    meta_json: Optional[dict[str, Any]] = None,
    db: Optional[AsyncSession] = None,
    commit: bool = False,
) -> None:
    ev = AuditEvent(
        event_type=event_type,
        severity=severity,
        status=status,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        org_id=org_id,
        reseller_id=reseller_id,
        target_type=target_type,
        target_id=target_id,
        error_code=error_code,
        error_message_redacted=error_message_redacted,
        meta_json=meta_json,
    )
    if db is not None:
        db.add(ev)
        if commit:
            await db.commit()
        return
    async with async_session() as s:
        s.add(ev)
        await s.commit()
