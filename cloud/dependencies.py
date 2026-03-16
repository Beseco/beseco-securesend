"""
cloud/dependencies.py — FastAPI dependency factories for authentication and
role-based access control.
"""

from __future__ import annotations

from typing import Callable

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Role ordering for hierarchical checks
_ROLE_RANK: dict[str, int] = {
    UserRole.org_user: 0,
    UserRole.org_admin: 1,
    UserRole.reseller_admin: 2,
    UserRole.superadmin: 3,
}

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────

def decode_token(token: str) -> dict:
    """Decode and validate a JWT; raise 401 on any error."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise CREDENTIALS_EXCEPTION


# ── Core auth dependency ──────────────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate the Bearer token and return the active User ORM object.
    The organization (and therefore reseller_id) is eagerly loaded.
    """
    payload = decode_token(token)

    # Reject refresh tokens used as access tokens
    if payload.get("type") != "access":
        raise CREDENTIALS_EXCEPTION

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise CREDENTIALS_EXCEPTION

    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise CREDENTIALS_EXCEPTION
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )
    return user


# ── Role-based access control ─────────────────────────────────────────────────

def require_role(*roles: UserRole) -> Callable:
    """
    Dependency factory. Usage:

        @router.get("/admin", dependencies=[Depends(require_role(UserRole.org_admin))])

    or as a direct dependency returning the user:

        user = Depends(require_role(UserRole.org_admin, UserRole.superadmin))
    """
    min_rank = min(_ROLE_RANK[r] for r in roles)

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if _ROLE_RANK.get(current_user.role, -1) < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {[r.value for r in roles]}",
            )
        return current_user

    return _check


# Convenience pre-built dependencies

def org_user_required() -> Callable:
    return require_role(UserRole.org_user, UserRole.org_admin,
                        UserRole.reseller_admin, UserRole.superadmin)


def org_admin_required() -> Callable:
    return require_role(UserRole.org_admin, UserRole.reseller_admin, UserRole.superadmin)


def reseller_admin_required() -> Callable:
    return require_role(UserRole.reseller_admin, UserRole.superadmin)


def superadmin_required() -> Callable:
    return require_role(UserRole.superadmin)
