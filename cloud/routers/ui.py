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
  GET  /ui/admin/resellers → list of all resellers (superadmin)
  GET  /ui/admin/reseller-cp → reseller context CP (superadmin in reseller ctx)

  POST /ui/ctx/reseller/{reseller_id} → enter reseller context
  POST /ui/ctx/org/{org_id}           → enter org context
  POST /ui/ctx/reset                  → exit current context (one level up)

  GET  /ui/api/history  → JSON history for the authenticated user (cookie auth)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from versioning import get_ui_version_cached
from models.organization import Organization
from models.reseller import Reseller
from models.shared import EmailVerification, History
from models.user import User, UserRole

router = APIRouter(prefix="/ui", tags=["ui"])
limiter = Limiter(key_func=get_remote_address)
_sec_log = logging.getLogger("securesend.security")


# ── Setup-Assistent ────────────────────────────────────────────────────────────


async def _needs_setup(db: AsyncSession) -> bool:
    """Gibt True zurück wenn noch kein Superadmin existiert."""
    res = await db.execute(
        select(User).where(User.role == UserRole.superadmin).limit(1)
    )
    return res.scalar_one_or_none() is None


@router.get("/setup", response_class=HTMLResponse)
@limiter.limit("20/hour")
async def setup_page(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    if not await _needs_setup(db):
        return RedirectResponse(url="/ui/login", status_code=303)
    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "current_year": datetime.now().year,
            "error": "",
        },
    )


@router.post("/setup")
@limiter.limit("5/hour")
async def setup_submit(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    reseller_name: str = Form(...),
    reseller_slug: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    import uuid
    from dependencies import hash_password
    from models.reseller import Reseller
    from models.organization import Organization

    # Organisation bekommt denselben Namen/Slug wie der Reseller
    org_name = reseller_name
    org_slug = reseller_slug

    def _err(msg: str):
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "current_year": datetime.now().year,
                "error": msg,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "reseller_name": reseller_name,
                "reseller_slug": reseller_slug,
            },
            status_code=422,
        )

    if not await _needs_setup(db):
        return RedirectResponse(url="/ui/login", status_code=303)

    # Validierungen
    if not first_name.strip() or not last_name.strip():
        return _err("Bitte Vor- und Nachname eingeben.")
    if "@" not in email:
        return _err("Ungültige E-Mail-Adresse.")
    if len(password) < 12:
        return _err("Passwort muss mindestens 8 Zeichen haben.")
    if password != password2:
        return _err("Die Passwörter stimmen nicht überein.")
    if not reseller_name.strip() or not reseller_slug.strip():
        return _err("Bitte Reseller-Name und Slug eingeben.")
    if not org_name.strip() or not org_slug.strip():
        return _err("Bitte Organisations-Name und Slug eingeben.")

    try:
        # 1. Superadmin anlegen
        admin = User(
            id=str(uuid.uuid4()),
            email=email.lower().strip(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            password_hash=hash_password(password),
            role=UserRole.superadmin,
            is_active=True,
        )
        db.add(admin)

        # 2. Reseller anlegen
        reseller = Reseller(
            id=str(uuid.uuid4()),
            name=reseller_name.strip(),
            slug=reseller_slug.strip().lower(),
            contact_email=email.lower().strip(),
            is_active=True,
        )
        db.add(reseller)
        await db.flush()  # reseller.id verfügbar machen

        # 3. Organisation anlegen
        from services.hosted_provider import merge_org_settings_with_storage_defaults

        org = Organization(
            id=str(uuid.uuid4()),
            reseller_id=reseller.id,
            name=org_name.strip(),
            slug=org_slug.strip().lower(),
            is_active=True,
            settings_json=merge_org_settings_with_storage_defaults(None),
        )
        db.add(org)
        await db.flush()  # org.id verfügbar machen

        # 4. Superadmin der ersten Organisation zuweisen
        #    (gleiche Person, eine E-Mail — hat Zugriff auf alles)
        admin.org_id = org.id
        await db.flush()
        from services.hosted_provider import ensure_hosted_cloud_provider

        await ensure_hosted_cloud_provider(db, org.id)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return _err(f"Datenbankfehler: {exc}")

    return RedirectResponse(url="/ui/login?setup=1", status_code=303)


# ── Template engine ───────────────────────────────────────────────────────────

_ROLE_LABELS = {
    "superadmin": "Superadmin",
    "reseller_admin": "Reseller-Admin",
    "org_admin": "Org-Admin",
    "org_user": "Benutzer",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["role_label"] = lambda r: _ROLE_LABELS.get(
    getattr(r, "value", str(r)), str(r)
)
templates.env.globals["app_version"] = get_ui_version_cached()

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

_CTX_COOKIE = "ss_ctx"


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=settings.SECURE_COOKIES,
        path="/",  # must be "/" so API routes at /admin/* also receive the cookie
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=_COOKIE_NAME, samesite="lax", path="/")


def _safe_redirect(next_url: str, fallback: str = "/ui/") -> str:
    """Validate redirect target to prevent open redirect attacks."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(next_url)
        # Must be relative (no scheme, no host) and start with /ui
        if parsed.scheme or parsed.netloc:
            return fallback
        path = parsed.path
        if not path.startswith("/ui"):
            return fallback
        # Prevent path traversal: resolve and check again
        import posixpath

        resolved = posixpath.normpath(path)
        if not resolved.startswith("/ui"):
            return fallback
        return next_url
    except Exception:
        return fallback


import hashlib
import hmac as _hmac_mod


def _ctx_sign(value: str) -> str:
    """Return value:hmac_hex for context cookie."""
    sig = _hmac_mod.new(
        settings.SECRET_KEY.encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"{value}:{sig}"


def _ctx_verify(raw: str) -> str | None:
    """Verify HMAC signature, return the value or None if invalid."""
    if ":" not in raw:
        return None
    # Signature is always the last 16 chars after final ":"
    idx = raw.rfind(":")
    value, sig = raw[:idx], raw[idx + 1 :]
    expected = _hmac_mod.new(
        settings.SECRET_KEY.encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    if not _hmac_mod.compare_digest(sig, expected):
        return None
    return value


def _set_ctx_cookie(response: RedirectResponse, value: str) -> None:
    response.set_cookie(
        key=_CTX_COOKIE,
        value=_ctx_sign(value),
        max_age=86400 * 7,
        httponly=False,
        samesite="lax",
        secure=settings.SECURE_COOKIES,
        path="/",
    )


def _clear_ctx_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=_CTX_COOKIE, samesite="lax", path="/")


def _read_ctx(request: Request) -> dict:
    """Read context cookie, verify HMAC, and return ctx dict for templates."""
    raw_signed = request.cookies.get(_CTX_COOKIE, "")
    raw = _ctx_verify(raw_signed) or ""
    if raw.startswith("o:"):
        parts = raw.split(":")
        if len(parts) >= 3:
            return {
                "type": "org",
                "reseller_id": parts[1],
                "org_id": parts[2],
                "raw": raw,
            }
    if raw.startswith("r:"):
        return {"type": "reseller", "reseller_id": raw[2:], "raw": raw}
    return {"type": "none", "raw": ""}


# ── Auth helpers ──────────────────────────────────────────────────────────────


async def _get_user_from_cookie(request: Request, db: AsyncSession) -> Optional[User]:
    """
    Try to extract and validate the JWT from the `access_token` cookie.
    Returns the User ORM object, or None on any failure.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        return None

    if payload.get("type") != "access":
        return None

    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        return None

    result = await db.execute(
        select(User).options(selectinload(User.organization)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


def _require_role_rank(user: Optional[User], min_rank: int) -> bool:
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


def _ctx(
    request: Request,
    current_user: Optional[User],
    active_page: str,
    ctx_reseller_name: str = "",
    ctx_org_name: str = "",
    **extra: Any,
) -> dict:
    """Build common template context dict."""
    return {
        "request": request,
        "current_user": current_user,
        "active_page": active_page,
        "current_year": datetime.now().year,
        "ctx": _read_ctx(request),
        "ctx_reseller_name": ctx_reseller_name,
        "ctx_org_name": ctx_org_name,
        **extra,
    }


async def _resolve_ctx_names(ctx: dict, db: AsyncSession) -> tuple[str, str]:
    """Fetch reseller_name and org_name for a given ctx dict."""
    reseller_name = ""
    org_name = ""
    if ctx["type"] in ("reseller", "org"):
        r = await db.execute(select(Reseller).where(Reseller.id == ctx["reseller_id"]))
        res = r.scalar_one_or_none()
        if res:
            reseller_name = res.name
    if ctx["type"] == "org":
        o = await db.execute(
            select(Organization).where(Organization.id == ctx["org_id"])
        )
        org = o.scalar_one_or_none()
        if org:
            org_name = org.name
    return reseller_name, org_name


# ── Login / Logout ────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str = "",
    next: str = "/ui/",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show login form. Redirect to dashboard if already authenticated."""
    # Noch kein Superadmin → Setup-Assistent
    if await _needs_setup(db):
        return RedirectResponse(url="/ui/setup", status_code=303)

    user = await _get_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/ui/", status_code=303)

    # Orgs with self-registration enabled → show registration links
    from models.organization import Organization as OrgModel

    orgs_result = await db.execute(select(OrgModel).where(OrgModel.is_active == True))
    reg_orgs = [
        {"name": o.name, "slug": o.slug}
        for o in orgs_result.scalars().all()
        if (o.settings_json or {}).get("allow_registration") and o.slug
    ]

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "next": next,
            "current_year": datetime.now().year,
            "prefill_email": request.query_params.get("email", ""),
            "reg_orgs": reg_orgs,
        },
    )


@router.post("/login")
@limiter.limit("10/minute")
async def login_submit(
    request: Request,
    email: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/ui/"),
    totp_code: str = Form(default=""),
    pending_token: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Process login form: verify credentials, set cookie, redirect."""
    from dependencies import verify_password  # local import to avoid circular
    from jose import JWTError
    import pyotp as _pyotp

    # ── Step 2: TOTP verification (pending_token present) ──────────────────────
    if pending_token:
        try:
            payload = jwt.decode(
                pending_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            if payload.get("type") != "2fa_pending":
                raise JWTError("wrong type")
            user_id = payload["sub"]
        except (JWTError, KeyError, Exception):
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Sitzung abgelaufen. Bitte erneut anmelden.",
                    "current_year": datetime.now().year,
                    "reg_orgs": [],
                },
                status_code=401,
            )
        result = await db.execute(
            select(User)
            .options(selectinload(User.organization))
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user or not user.totp_secret:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Fehler.",
                    "current_year": datetime.now().year,
                    "reg_orgs": [],
                },
                status_code=401,
            )

        totp = _pyotp.TOTP(user.totp_secret)
        if not totp.verify(totp_code.strip(), valid_window=1):
            # Re-issue pending token so user can try again
            expire = datetime.now(timezone.utc) + timedelta(minutes=5)
            new_pending = jwt.encode(
                {"sub": user.id, "type": "2fa_pending", "exp": expire},
                settings.SECRET_KEY,
                algorithm=settings.ALGORITHM,
            )
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "show_totp": True,
                    "pending_token": new_pending,
                    "error": "Ungültiger Code. Bitte erneut versuchen.",
                    "next": next,
                    "current_year": datetime.now().year,
                    "reg_orgs": [],
                },
                status_code=401,
            )
        # TOTP OK → set cookie
        safe_next = _safe_redirect(next)
        response = RedirectResponse(url=safe_next, status_code=303)
        _set_auth_cookie(response, _make_access_token(user))
        return response

    # ── Step 1: Password verification ──────────────────────────────────────────
    result = await db.execute(
        select(User).options(selectinload(User.organization)).where(User.email == email)
    )
    user = result.scalar_one_or_none()

    error_msg = ""
    if user is None or not verify_password(password, user.password_hash):
        error_msg = "Ungültige E-Mail-Adresse oder Passwort."
        _sec_log.warning(
            "Failed UI login: email=%r ip=%s",
            email,
            request.client.host if request.client else "unknown",
        )
    elif not user.is_active:
        error_msg = (
            "Ihr Konto ist deaktiviert. Bitte wenden Sie sich an Ihren Administrator."
        )

    if error_msg:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": error_msg,
                "next": next,
                "current_year": datetime.now().year,
                "prefill_email": email,
                "reg_orgs": [],
            },
            status_code=401,
        )

    # ── 2FA required? ──────────────────────────────────────────────────────────
    if user.totp_enabled and user.totp_secret:
        expire = datetime.now(timezone.utc) + timedelta(minutes=5)
        p_token = jwt.encode(
            {"sub": user.id, "type": "2fa_pending", "exp": expire},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "show_totp": True,
                "pending_token": p_token,
                "next": next,
                "current_year": datetime.now().year,
                "reg_orgs": [],
            },
        )

    # ── No 2FA: set cookie ─────────────────────────────────────────────────────
    safe_next = _safe_redirect(next)
    response = RedirectResponse(url=safe_next, status_code=303)
    _set_auth_cookie(response, _make_access_token(user))
    return response


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the auth cookie and redirect to login."""
    response = RedirectResponse(url="/ui/login", status_code=303)
    _clear_auth_cookie(response)
    _clear_ctx_cookie(response)
    return response


# ── Self-registration ─────────────────────────────────────────────────────────


@router.get("/register/{org_slug}", response_class=HTMLResponse)
async def register_page(
    request: Request,
    org_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Public registration page for a specific org slug."""
    result = await db.execute(select(Organization).where(Organization.slug == org_slug))
    org = result.scalar_one_or_none()
    settings_j = (org.settings_json or {}) if org else {}
    if not org or not org.is_active or not settings_j.get("allow_registration"):
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "org_slug": org_slug,
                "org_name": None,
                "allowed_domains": [],
                "error": "Die Registrierung für diese Organisation ist nicht verfügbar.",
                "current_year": datetime.now().year,
            },
            status_code=404,
        )
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "org_slug": org_slug,
            "org_name": org.name,
            "allowed_domains": settings_j.get("allowed_domains", []),
            "error": None,
            "current_year": datetime.now().year,
        },
    )


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    token: str = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Verify e-mail token and activate the user account."""
    ctx: dict = {"request": request, "current_year": datetime.now().year}

    if not token:
        ctx.update(success=False, message="Kein Token angegeben.")
        return templates.TemplateResponse("verify_email.html", ctx, status_code=400)

    result = await db.execute(
        select(EmailVerification).where(EmailVerification.token == token)
    )
    verification = result.scalar_one_or_none()

    if not verification:
        ctx.update(
            success=False,
            message="Ungültiger oder bereits verwendeter Bestätigungslink.",
        )
        return templates.TemplateResponse("verify_email.html", ctx, status_code=404)

    if verification.expires_at < datetime.utcnow():
        await db.delete(verification)
        await db.commit()
        ctx.update(
            success=False,
            message="Der Bestätigungslink ist abgelaufen. Bitte registrieren Sie sich erneut.",
        )
        return templates.TemplateResponse("verify_email.html", ctx, status_code=410)

    # Activate user
    user_result = await db.execute(select(User).where(User.id == verification.user_id))
    user = user_result.scalar_one_or_none()
    if user:
        user.is_active = True
    await db.delete(verification)
    await db.commit()

    ctx.update(
        success=True,
        message="Ihre E-Mail-Adresse wurde bestätigt. Sie können sich jetzt anmelden.",
    )
    return templates.TemplateResponse("verify_email.html", ctx)


# ── Passwort vergessen ────────────────────────────────────────────────────────


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "current_year": datetime.now().year,
            "success": False,
            "error": None,
        },
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    from models.shared import PasswordReset

    ctx: dict = {
        "request": request,
        "current_year": datetime.now().year,
        "token": token,
        "error": None,
        "success": False,
    }
    if token:
        result = await db.execute(
            select(PasswordReset).where(PasswordReset.token == token)
        )
        reset = result.scalar_one_or_none()
        if not reset or reset.expires_at < datetime.utcnow():
            ctx["error"] = "Dieser Reset-Link ist ungültig oder abgelaufen."
            ctx["token"] = ""
    else:
        ctx["error"] = "Kein Reset-Token angegeben."
    return templates.TemplateResponse("reset_password.html", ctx)


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/")
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(request, user, "dashboard", ctx_reseller_name=rname, ctx_org_name=oname),
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
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    if not user.org_id:
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request,
                user,
                "dashboard",
                ctx_reseller_name=rname,
                ctx_org_name=oname,
                flash_message="Das Senden ist nur für Organisationsbenutzer verfügbar.",
                flash_type="error",
            ),
        )

    # Fetch organization security settings
    org_settings = {}
    if user.organization and user.organization.settings_json:
        org_settings = user.organization.settings_json

    from services.hosted_provider import merge_org_settings_with_storage_defaults

    merged_os = merge_org_settings_with_storage_defaults(org_settings)
    allowed_levels = merged_os.get(
        "allowed_security_levels",
        ["normal", "standard", "secure", "extended", "advanced", "maximal"],
    )
    default_level = merged_os.get("default_security_level", "secure")
    storage_preference = merged_os.get("storage_preference", "securesend_cloud")

    return templates.TemplateResponse(
        "send.html",
        _ctx(
            request,
            user,
            "send",
            ctx_reseller_name=rname,
            ctx_org_name=oname,
            allowed_security_levels=allowed_levels,
            default_security_level=default_level,
            storage_preference=storage_preference,
        ),
    )


# ── Receive ───────────────────────────────────────────────────────────────────


@router.get("/receive", response_class=HTMLResponse)
async def receive_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/receive")
    if not user.org_id:
        return RedirectResponse(url="/ui/", status_code=303)
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "receive.html",
        _ctx(request, user, "receive", ctx_reseller_name=rname, ctx_org_name=oname),
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
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "contacts.html",
        _ctx(request, user, "contacts", ctx_reseller_name=rname, ctx_org_name=oname),
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
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "history.html",
        _ctx(request, user, "history", ctx_reseller_name=rname, ctx_org_name=oname),
    )


@router.get("/api/history")
async def history_api(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    JSON endpoint for history data, authenticated via cookie.
    Used by the history page JS to load history without needing Bearer auth.
    """
    user = await _get_user_from_cookie(request, db)
    if not user:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    result = await db.execute(
        select(History)
        .where(History.user_id == user.id)
        .order_by(History.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return JSONResponse(
        [
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
                "opened_at": h.opened_at.isoformat() if h.opened_at else None,
                "link_clicked_at": h.link_clicked_at.isoformat()
                if h.link_clicked_at
                else None,
                "download_count": h.download_count or 0,
                "last_downloaded_at": h.last_downloaded_at.isoformat()
                if h.last_downloaded_at
                else None,
                "is_revoked": h.is_revoked,
                "revoked_at": h.revoked_at.isoformat() if h.revoked_at else None,
                "revoked_by": h.revoked_by,
                "files_json": h.files_json or [],
            }
            for h in rows
        ]
    )


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
                request,
                user,
                "dashboard",
                flash_message="Sie haben keine Berechtigung für diese Seite.",
                flash_type="error",
            ),
        )
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)

    # Superadmin in org context: use the ctx org_id directly (no dropdown needed)
    # Superadmin at top level or reseller level: show org selector dropdown
    orgs = []
    ctx_org_id = ""
    if user.role == UserRole.superadmin:
        if ctx["type"] == "org":
            ctx_org_id = ctx["org_id"]
        else:
            from models.organization import Organization

            result = await db.execute(
                select(Organization)
                .where(Organization.is_active == True)
                .order_by(Organization.name)
            )  # noqa: E712
            orgs = [{"id": o.id, "name": o.name} for o in result.scalars().all()]
    return templates.TemplateResponse(
        "admin_org.html",
        _ctx(
            request,
            user,
            "admin_org",
            ctx_reseller_name=rname,
            ctx_org_name=oname,
            orgs=orgs,
            ctx_org_id=ctx_org_id,
        ),
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
                request,
                user,
                "dashboard",
                flash_message="Sie haben keine Berechtigung für diese Seite.",
                flash_type="error",
            ),
        )
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    # For superadmin: pass all resellers for org creation modal + settings selector
    resellers = []
    if user.role == UserRole.superadmin:
        result = await db.execute(
            select(Reseller).where(Reseller.is_active == True).order_by(Reseller.name)
        )  # noqa: E712
        resellers = [{"id": r.id, "name": r.name} for r in result.scalars().all()]
    return templates.TemplateResponse(
        "admin_reseller.html",
        _ctx(
            request,
            user,
            "admin_reseller",
            ctx_reseller_name=rname,
            ctx_org_name=oname,
            resellers=resellers,
            reseller_id=user.reseller_id or "",
        ),
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
                request,
                user,
                "dashboard",
                flash_message="Diese Seite ist nur für Superadministratoren zugänglich.",
                flash_type="error",
            ),
        )
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "admin_super.html",
        _ctx(request, user, "admin_super", ctx_reseller_name=rname, ctx_org_name=oname),
    )


# ── Admin: Resellers list ──────────────────────────────────────────────────────


@router.get("/admin/resellers", response_class=HTMLResponse)
async def admin_resellers_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/admin/resellers")
    if not _require_role_rank(user, _ROLE_RANK[UserRole.superadmin]):
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request,
                user,
                "dashboard",
                flash_message="Diese Seite ist nur für Superadministratoren zugänglich.",
                flash_type="error",
            ),
        )
    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)
    return templates.TemplateResponse(
        "admin_resellers.html",
        _ctx(
            request,
            user,
            "admin_resellers",
            ctx_reseller_name=rname,
            ctx_org_name=oname,
        ),
    )


# ── Admin: Reseller context CP ────────────────────────────────────────────────


@router.get("/admin/reseller-cp", response_class=HTMLResponse)
async def admin_reseller_cp_page(
    request: Request,
    tab: str = "orgs",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user = await _get_user_from_cookie(request, db)
    if not user:
        return _login_redirect("/ui/admin/reseller-cp")
    if not _require_role_rank(user, _ROLE_RANK[UserRole.reseller_admin]):
        return templates.TemplateResponse(
            "dashboard.html",
            _ctx(
                request,
                user,
                "dashboard",
                flash_message="Sie haben keine Berechtigung für diese Seite.",
                flash_type="error",
            ),
        )

    ctx = _read_ctx(request)
    rname, oname = await _resolve_ctx_names(ctx, db)

    # Determine reseller_id: from context cookie for superadmin, from user for reseller_admin
    if user.role == UserRole.superadmin:
        if ctx["type"] not in ("reseller", "org"):
            return RedirectResponse(url="/ui/admin/resellers", status_code=303)
        reseller_id = ctx["reseller_id"]
    else:
        reseller_id = user.reseller_id or ""

    # Fetch reseller data
    r_result = await db.execute(select(Reseller).where(Reseller.id == reseller_id))
    reseller = r_result.scalar_one_or_none()
    if not reseller:
        return RedirectResponse(url="/ui/admin/resellers", status_code=303)

    # Fetch orgs for this reseller
    orgs_result = await db.execute(
        select(Organization)
        .where(Organization.reseller_id == reseller_id)
        .order_by(Organization.name)
    )
    orgs = orgs_result.scalars().all()

    return templates.TemplateResponse(
        "admin_reseller_cp.html",
        _ctx(
            request,
            user,
            "admin_reseller_cp" if tab != "orgs" else "admin_reseller_orgs",
            ctx_reseller_name=rname,
            ctx_org_name=oname,
            reseller=reseller,
            orgs=orgs,
            active_tab=tab,
            reseller_id=reseller_id,
        ),
    )


# ── Context switching routes ───────────────────────────────────────────────────


@router.post("/ctx/reseller/{reseller_id}")
async def ctx_enter_reseller(
    reseller_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Enter reseller context (superadmin only)."""
    user = await _get_user_from_cookie(request, db)
    if not user or user.role != UserRole.superadmin:
        return RedirectResponse(url="/ui/login", status_code=303)

    r_result = await db.execute(select(Reseller).where(Reseller.id == reseller_id))
    reseller = r_result.scalar_one_or_none()
    if not reseller:
        return RedirectResponse(url="/ui/admin/resellers", status_code=303)

    response = RedirectResponse(url="/ui/admin/reseller-cp", status_code=303)
    _set_ctx_cookie(response, f"r:{reseller_id}")
    return response


@router.post("/ctx/org/{org_id}")
async def ctx_enter_org(
    org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Enter org context (superadmin only)."""
    user = await _get_user_from_cookie(request, db)
    if not user or user.role != UserRole.superadmin:
        return RedirectResponse(url="/ui/login", status_code=303)

    o_result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = o_result.scalar_one_or_none()
    if not org:
        return RedirectResponse(url="/ui/admin/reseller-cp", status_code=303)

    response = RedirectResponse(url="/ui/admin/org", status_code=303)
    _set_ctx_cookie(response, f"o:{org.reseller_id}:{org_id}")
    return response


@router.post("/ctx/reset")
async def ctx_reset(
    request: Request,
) -> RedirectResponse:
    """Exit current context — one level up."""
    ctx = _read_ctx(request)
    if ctx["type"] == "org":
        # Go back to reseller context
        reseller_id = ctx["reseller_id"]
        response = RedirectResponse(url="/ui/admin/reseller-cp", status_code=303)
        _set_ctx_cookie(response, f"r:{reseller_id}")
        return response
    elif ctx["type"] == "reseller":
        # Go back to top level
        response = RedirectResponse(url="/ui/", status_code=303)
        _clear_ctx_cookie(response)
        return response
    else:
        response = RedirectResponse(url="/ui/", status_code=303)
        _clear_ctx_cookie(response)
        return response
