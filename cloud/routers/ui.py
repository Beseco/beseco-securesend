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
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from database import get_db
from models.organization import Organization
from models.reseller import Reseller
from models.shared import EmailVerification, History
from models.user import User, UserRole

router = APIRouter(prefix="/ui", tags=["ui"])


# ── Setup-Assistent ────────────────────────────────────────────────────────────

async def _needs_setup(db: AsyncSession) -> bool:
    """Gibt True zurück wenn noch kein Superadmin existiert."""
    res = await db.execute(
        select(User).where(User.role == UserRole.superadmin).limit(1)
    )
    return res.scalar_one_or_none() is None


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    if not await _needs_setup(db):
        return RedirectResponse(url="/ui/login", status_code=303)
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "current_year": datetime.now().year,
        "error": "",
    })


@router.post("/setup")
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
        return templates.TemplateResponse("setup.html", {
            "request": request,
            "current_year": datetime.now().year,
            "error": msg,
            "first_name": first_name, "last_name": last_name, "email": email,
            "reseller_name": reseller_name, "reseller_slug": reseller_slug,
        }, status_code=422)

    if not await _needs_setup(db):
        return RedirectResponse(url="/ui/login", status_code=303)

    # Validierungen
    if not first_name.strip() or not last_name.strip():
        return _err("Bitte Vor- und Nachname eingeben.")
    if "@" not in email:
        return _err("Ungültige E-Mail-Adresse.")
    if len(password) < 8:
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
        org = Organization(
            id=str(uuid.uuid4()),
            reseller_id=reseller.id,
            name=org_name.strip(),
            slug=org_slug.strip().lower(),
            is_active=True,
        )
        db.add(org)
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
        path="/",       # must be "/" so API routes at /admin/* also receive the cookie
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=_COOKIE_NAME, samesite="lax", path="/")


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def _get_user_from_cookie(
    request: Request, db: AsyncSession
) -> Optional[User]:
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

    user_id: Optional[str] = payload.get("sub")
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


def _ctx(request: Request, current_user: Optional[User], active_page: str, **extra: Any) -> dict:
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
    # Noch kein Superadmin → Setup-Assistent
    if await _needs_setup(db):
        return RedirectResponse(url="/ui/setup", status_code=303)

    user = await _get_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/ui/", status_code=303)

    # Orgs with self-registration enabled → show registration links
    from models.organization import Organization as OrgModel
    orgs_result = await db.execute(
        select(OrgModel).where(OrgModel.is_active == True)
    )
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


# ── Self-registration ─────────────────────────────────────────────────────────

@router.get("/register/{org_slug}", response_class=HTMLResponse)
async def register_page(
    request: Request,
    org_slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Public registration page for a specific org slug."""
    result = await db.execute(
        select(Organization).where(Organization.slug == org_slug)
    )
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
        ctx.update(success=False, message="Ungültiger oder bereits verwendeter Bestätigungslink.")
        return templates.TemplateResponse("verify_email.html", ctx, status_code=404)

    if verification.expires_at < datetime.utcnow():
        await db.delete(verification)
        await db.commit()
        ctx.update(success=False, message="Der Bestätigungslink ist abgelaufen. Bitte registrieren Sie sich erneut.")
        return templates.TemplateResponse("verify_email.html", ctx, status_code=410)

    # Activate user
    user_result = await db.execute(
        select(User).where(User.id == verification.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user:
        user.is_active = True
    await db.delete(verification)
    await db.commit()

    ctx.update(success=True, message="Ihre E-Mail-Adresse wurde bestätigt. Sie können sich jetzt anmelden.")
    return templates.TemplateResponse("verify_email.html", ctx)


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
    # Superadmin sees an org selector dropdown
    orgs = []
    if user.role == UserRole.superadmin:
        from models.organization import Organization
        result = await db.execute(select(Organization).where(Organization.is_active == True).order_by(Organization.name))  # noqa: E712
        orgs = [{"id": o.id, "name": o.name} for o in result.scalars().all()]
    return templates.TemplateResponse(
        "admin_org.html",
        _ctx(request, user, "admin_org", orgs=orgs),
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
    # For superadmin: pass all resellers for org creation modal + settings selector
    resellers = []
    if user.role == UserRole.superadmin:
        result = await db.execute(select(Reseller).where(Reseller.is_active == True).order_by(Reseller.name))  # noqa: E712
        resellers = [{"id": r.id, "name": r.name} for r in result.scalars().all()]
    return templates.TemplateResponse(
        "admin_reseller.html",
        _ctx(request, user, "admin_reseller", resellers=resellers,
             reseller_id=user.reseller_id or ""),
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
