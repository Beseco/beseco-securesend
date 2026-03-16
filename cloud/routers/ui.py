"""
cloud/routers/ui.py — Jinja2 HTML UI routes for SecureSend Cloud.

All HTML pages are served under the /ui/ prefix.
JWT is stored in an HttpOnly cookie named `access_token` on login,
and read from the cookie for every protected page.

Routes:
  GET  /ui/login        → login page
  POST /ui/login        → authenticate, set cookie, redirect
  POST /ui/logout       → clear cookie, redirect to login
  GET  /ui/             → dashboard (protected)
  GET  /ui/send         → send page (protected, org users)
  GET  /ui/contacts     → contacts page (protected, org users)
  GET  /ui/history      → history page (protected, org users)
  GET  /ui/admin/org    → org admin page (org_admin+)
  GET  /ui/admin/reseller → reseller admin page (reseller_admin+)
  GET  /ui/admin/super  → super admin page (superadmin)

  GET  /ui/api/history  → JSON history for the authenticated user (cookie auth)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from models.shared import History
from models.user import User, UserRole

router = APIRouter(prefix="/ui", tags=["ui"])

# ── Template engine ───────────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── Role ordering ─────────────────────────────────────────────────────────────

_ROLE_RANK: dict[str, int] = {
    UserRole.org_user: 0,
    UserRole.org_admin: 1,
    UserRole.reseller_admin: 2,
    UserRole.superadmin: 3,
}

# ── Cookie helpers ────────────────────────────────────────────────────────────

_COOKIE_NAME = "access_token"
_COOKIE_MAX_AGE = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # set to True in production behind HTTPS
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=_COOKIE_NAME, samesite="lax")


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def _get_user_from_cookie(
    request: Request, db: AsyncSession
) -> User | None:
    """
    Try to extract and validate the JWT from the `access_token` cookie.
    Returns the User ORM object, or None on any failure.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None

    if payload.get("type") != "access":
        return None

    user_id: str | None = payload.get("sub")
    if not user_id:
        return None

    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


def _require_role_rank(user: User | None, min_rank: int) -> bool:
    """Return True if the user has at least the given role rank."""
    if user is None:
        return False
    return _ROLE_RANK.get(user.role, -1) >= min_rank


def _make_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "org_id": user.org_id,
        "reseller_id": user.reseller_id,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _login_redirect(next_url: str = "/ui/") -> RedirectResponse:
    return RedirectResponse(url=f"/ui/login?next={next_url}", status_code=303)


def _ctx(request: Request, current_user: User | None, active_page: str, **extra: Any) -> dict:
    """Build common template context dict."""
    return {
        "request": request,
        "current_user": current_user,
        "active_page": active_page,
        "current_year": datetime.now().year,
        **extra,
    }


# ── Login / Logout ────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str = "",
    next: str = "/ui/",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show login form. Redirect to dashboard if already authenticated."""
    user = await _get_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/ui/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "next": next,
            "current_year": datetime.now().year,
            "prefill_email": request.query_params.get("email", ""),
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/ui/"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Process login form: verify credentials, set cookie, redirect."""
    from dependencies import verify_password  # local import to avoid circular

    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.email == email)
    )
    user = result.scalar_one_or_none()

    error_msg = ""
    if user is None or not verify_password(password, user.password_hash):
        error_msg = "Ungültige E-Mail-Adresse oder Passwort."
    elif not user.is_active:
        error_msg = "Ihr Konto ist deaktiviert. Bitte wenden Sie sich an Ihren Administrator."

    if error_msg:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": error_msg,
                "next": next,
                "current_year": datetime.now().year,
                "prefill_email": email,
            },
            status_code=401,
        )

    token = _make_access_token(user)
    # Ensure redirect target is safe (starts with /ui/)
    safe_next = next if next.startswith("/ui") else "/ui/"
    response = RedirectResponse(url=safe_next, status_code=303)
    _set_auth_cookie(response, token)
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the auth cookie and redirect to login."""
    response = RedirectResponse(url="/ui/login", status_code=303)
    _clear_auth_cookie(response)
    return response


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/")
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(request, user, "dashboard"),
    )


# ── Send ──────────────────────────────────────────────────────────────────────

@router.get("/send", response_class=HTMLResponse)
async def send_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/send")
    if not user.org_id:
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request, user, "dashboard",
                flash_message="Das Senden ist nur für Organisationsbenutzer verfügbar.",
                flash_type="error",
            ),
        )
    return templates.TemplateResponse(
        "send.html",
        _ctx(request, user, "send"),
    )


# ── Contacts ──────────────────────────────────────────────────────────────────

@router.get("/contacts", response_class=HTMLResponse)
async def contacts_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/contacts")
    if not user.org_id:
        return RedirectResponse(url="/ui/", status_code=303)
    return templates.TemplateResponse(
        "contacts.html",
        _ctx(request, user, "contacts"),
    )


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/history")
    if not user.org_id:
        return RedirectResponse(url="/ui/", status_code=303)
    return templates.TemplateResponse(
        "history.html",
        _ctx(request, user, "history"),
    )


@router.get("/api/history")
async def history_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    JSON endpoint for history data, authenticated via cookie.
    Used by the history page JS to load history without needing Bearer auth.
    """
    user = await _get_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    result = await db.execute(
        select(History)
        .where(History.user_id == user.id)
        .order_by(History.created_at.desc())
    )
    rows = result.scalars().all()
    return JSONResponse([
        {
            "id": h.id,
            "to_email": h.to_email,
            "to_phone": h.to_phone,
            "filename": h.filename,
            "share_url": h.share_url,
            "provider": h.provider,
            "expiry_days": h.expiry_days,
            "security_level": h.security_level,
            "ip_address": h.ip_address,
            "created_at": h.created_at.isoformat(),
        }
        for h in rows
    ])


# ── Admin: Organisation ───────────────────────────────────────────────────────

@router.get("/admin/org", response_class=HTMLResponse)
async def admin_org_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/admin/org")
    if not _require_role_rank(user, _ROLE_RANK[UserRole.org_admin]):
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request, user, "dashboard",
                flash_message="Sie haben keine Berechtigung für diese Seite.",
                flash_type="error",
            ),
        )
    return templates.TemplateResponse(
        "admin_org.html",
        _ctx(request, user, "admin_org"),
    )


# ── Admin: Reseller ───────────────────────────────────────────────────────────

@router.get("/admin/reseller", response_class=HTMLResponse)
async def admin_reseller_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/admin/reseller")
    if not _require_role_rank(user, _ROLE_RANK[UserRole.reseller_admin]):
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request, user, "dashboard",
                flash_message="Sie haben keine Berechtigung für diese Seite.",
                flash_type="error",
            ),
        )
    return templates.TemplateResponse(
        "admin_reseller.html",
        _ctx(request, user, "admin_reseller"),
    )


# ── Admin: Super ──────────────────────────────────────────────────────────────

@router.get("/admin/super", response_class=HTMLResponse)
async def admin_super_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/admin/super")
    if not _require_role_rank(user, _ROLE_RANK[UserRole.superadmin]):
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request, user, "dashboard",
                flash_message="Diese Seite ist nur für Superadministratoren zugänglich.",
                flash_type="error",
            ),
        )
    return templates.TemplateResponse(
        "admin_super.html",
        _ctx(request, user, "admin_super"),
    )
