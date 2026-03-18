"""
cloud/routers/auth.py — Authentication endpoints.

POST  /auth/login    → access_token + refresh_token
POST  /auth/refresh  → new access_token
POST  /auth/logout   → stateless no-op
GET   /auth/me       → current user info
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import logging

import pyotp

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from pydantic import BaseModel

_sec_log = logging.getLogger("securesend.security")
limiter = Limiter(key_func=get_remote_address)

from dependencies import (
    CREDENTIALS_EXCEPTION,
    decode_token,
    get_current_user,
    hash_password,
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
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Authenticate with email + password, receive JWT pair."""
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.email == body.email)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        _sec_log.warning(
            "Failed API login: email=%r ip=%s",
            body.email,
            request.client.host if request.client else "unknown",
        )
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

    user_id: Optional[str] = payload.get("sub")
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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change the authenticated user's own password."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aktuelles Passwort ist falsch",
        )
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Neues Passwort muss mindestens 8 Zeichen haben",
        )
    current_user.password_hash = hash_password(body.new_password)
    await db.commit()


class TotpEnableRequest(BaseModel):
    code: str


class TotpDisableRequest(BaseModel):
    password: str


@router.get("/2fa/status")
async def twofa_status(current_user: User = Depends(get_current_user)) -> dict:
    return {"enabled": current_user.totp_enabled}


@router.post("/2fa/setup")
async def twofa_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a new TOTP secret (not yet enabled). Returns the secret + otpauth URI."""
    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    current_user.totp_enabled = False
    await db.commit()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=current_user.email, issuer_name="SecureSend Cloud")
    return {"secret": secret, "uri": uri}


@router.post("/2fa/enable")
async def twofa_enable(
    body: TotpEnableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify TOTP code and enable 2FA."""
    if not current_user.totp_secret:
        raise HTTPException(
            status_code=400,
            detail="Kein TOTP-Secret vorhanden – bitte zuerst Setup aufrufen",
        )
    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Ungültiger Code")
    current_user.totp_enabled = True
    await db.commit()
    return {"enabled": True}


@router.post("/2fa/disable")
async def twofa_disable(
    body: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable 2FA (requires current password confirmation)."""
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Falsches Passwort")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    await db.commit()
    return {"enabled": False}


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user's profile."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        role=current_user.role.value,
        org_id=current_user.org_id,
        reseller_id=current_user.reseller_id,
        is_active=current_user.is_active,
    )
