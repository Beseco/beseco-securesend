"""
cloud/routers/auth.py — Authentication endpoints.

POST  /auth/login    → access_token + refresh_token
POST  /auth/refresh  → new access_token
POST  /auth/logout   → stateless no-op
GET   /auth/me       → current user info
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from dependencies import (
    CREDENTIALS_EXCEPTION,
    decode_token,
    get_current_user,
    verify_password,
)
from models.user import User
from schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Token creation helpers ────────────────────────────────────────────────────

def _make_access_token(user: User) -> str:
    reseller_id = user.reseller_id  # resolved via property on User
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "org_id": user.org_id,
        "reseller_id": reseller_id,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _make_refresh_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": user.id,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email + password, receive JWT pair."""
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.email == body.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    return TokenResponse(
        access_token=_make_access_token(user),
        refresh_token=_make_refresh_token(user),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AccessTokenResponse:
    """Exchange a valid refresh token for a new access token."""
    payload = decode_token(body.refresh_token)

    if payload.get("type") != "refresh":
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

    if user is None or not user.is_active:
        raise CREDENTIALS_EXCEPTION

    return AccessTokenResponse(access_token=_make_access_token(user))


@router.post("/logout")
async def logout() -> dict:
    """
    Stateless JWT logout — client should discard its tokens.
    A production system would add the JTI to a denylist here.
    """
    return {"detail": "Logged out"}


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user's profile."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role.value,
        org_id=current_user.org_id,
        reseller_id=current_user.reseller_id,
        is_active=current_user.is_active,
    )
