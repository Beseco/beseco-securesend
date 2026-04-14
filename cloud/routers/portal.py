"""
Gast-Portal: Anmeldung, Posteingang, Nachricht ansehen, Link aus E-Mail übernehmen.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pathlib import Path
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.organization import Organization
from models.reseller import Reseller
from models.shared import Guest, History
from models.user import User

router = APIRouter(prefix="/portal", tags=["portal"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_GUEST_COOKIE = "guest_access"
_GUEST_MAX_AGE = 7 * 24 * 60 * 60


def _guest_token(guest_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=_GUEST_MAX_AGE)
    payload = {
        "sub": guest_id,
        "type": "guest_access",
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _guest_from_cookie(request: Request) -> Optional[str]:
    raw = request.cookies.get(_GUEST_COOKIE)
    if not raw:
        return None
    try:
        payload = jwt.decode(raw, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "guest_access":
            return None
        return payload.get("sub")
    except JWTError:
        return None


def _parse_tracking_token(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    if "/track/l/" in s:
        return s.split("/track/l/")[-1].split("?")[0].split("#")[0].strip() or None
    if "/r/" in s:
        part = s.split("/r/")[-1]
        return part.split("/")[0].split("?")[0].strip() or None
    m = re.search(r"^([A-Za-z0-9_-]{20,})$", s)
    return s if m else None


async def _load_guest(request: Request, db: AsyncSession) -> Optional[Guest]:
    gid = _guest_from_cookie(request)
    if not gid:
        return None
    r = await db.execute(select(Guest).where(Guest.id == gid, Guest.is_active == True))  # noqa: E712
    return r.scalar_one_or_none()


@router.get("/login", response_class=HTMLResponse)
async def portal_login_page(request: Request, db: AsyncSession = Depends(get_db)):
    if await _load_guest(request, db):
        return RedirectResponse(url="/portal/", status_code=302)
    return templates.TemplateResponse(
        "portal_login.html",
        {"request": request, "error": ""},
    )


@router.post("/login")
async def portal_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email_n = email.strip().lower()
    r = await db.execute(select(Guest).where(Guest.email == email))
    guest = r.scalar_one_or_none()
    if not guest or not bcrypt.checkpw(password.encode(), guest.password_hash.encode()):
        return templates.TemplateResponse(
            "portal_login.html",
            {"request": request, "error": "E-Mail oder Passwort ungültig"},
            status_code=401,
        )
    guest.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    resp = RedirectResponse(url="/portal/", status_code=302)
    resp.set_cookie(
        key=_GUEST_COOKIE,
        value=_guest_token(guest.id),
        max_age=_GUEST_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.SECURE_COOKIES,
        path="/",
    )
    return resp


@router.get("/forgot-password", response_class=HTMLResponse)
async def portal_forgot_password_page(request: Request, db: AsyncSession = Depends(get_db)):
    if await _load_guest(request, db):
        return RedirectResponse(url="/portal/", status_code=302)
    return templates.TemplateResponse(
        "portal_forgot_password.html",
        {"request": request, "error": "", "message": "", "email": ""},
    )


@router.post("/forgot-password")
async def portal_forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Leitet zum bestehenden Gast-Reset (/r/reset/{token}), sobald ein Verlauf mit diesem Konto existiert."""
    if await _load_guest(request, db):
        return RedirectResponse(url="/portal/", status_code=302)
    email_n = email.strip().lower()
    r = await db.execute(select(Guest).where(Guest.email == email_n))
    guest = r.scalar_one_or_none()
    if not guest:
        return templates.TemplateResponse(
            "portal_forgot_password.html",
            {
                "request": request,
                "error": "Diese E-Mail-Adresse ist uns nicht bekannt.",
                "message": "",
                "email": email_n,
            },
            status_code=404,
        )
    hr = await db.execute(
        select(History)
        .where(History.guest_id == guest.id)
        .order_by(desc(History.created_at))
        .limit(1)
    )
    h = hr.scalar_one_or_none()
    # Fallback: Konto existiert, aber noch keine explizite guest_id-Verknüpfung.
    # Dann letzten Versand für dieselbe Empfänger-E-Mail nutzen und verknüpfen.
    if not h:
        hr = await db.execute(
            select(History)
            .where(History.to_email == email_n)
            .order_by(desc(History.created_at))
            .limit(1)
        )
        h = hr.scalar_one_or_none()
        if h and not h.guest_id:
            h.guest_id = guest.id
            await db.commit()
    if not h or not (h.tracking_token or "").strip():
        return templates.TemplateResponse(
            "portal_forgot_password.html",
            {
                "request": request,
                "error": "",
                "message": (
                    "Für dieses Konto liegt kein verknüpfter Versand vor. Bitte öffnen Sie eine "
                    "empfangene SecureSend-E-Mail und nutzen Sie dort den Link „Passwort vergessen“ "
                    "bzw. den Zugang zum Posteingang, oder wenden Sie sich an den Absender."
                ),
                "email": email_n,
            },
        )
    return RedirectResponse(url=f"/r/reset/{h.tracking_token}", status_code=302)


@router.post("/logout")
async def portal_logout():
    resp = RedirectResponse(url="/portal/login", status_code=302)
    resp.delete_cookie(key=_GUEST_COOKIE, path="/")
    return resp


@router.get("/", response_class=HTMLResponse)
async def portal_inbox(request: Request, db: AsyncSession = Depends(get_db)):
    guest = await _load_guest(request, db)
    if not guest:
        return RedirectResponse(url="/portal/login", status_code=302)

    r = await db.execute(
        select(History)
        .where(History.guest_id == guest.id)
        .order_by(History.created_at.desc())
        .limit(200)
    )
    items = list(r.scalars().all())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for h in items:
        exp = h.expires_at
        if exp is None and h.created_at:
            exp = h.created_at + timedelta(days=h.expiry_days)
        expired = bool(exp and exp < now) or h.is_revoked
        rows.append(
            {
                "id": h.id,
                "subject": h.subject or h.filename or "Nachricht",
                "to_email": h.to_email,
                "created_at": h.created_at,
                "expired": expired,
                "read_at": h.read_at,
                "is_revoked": h.is_revoked,
            }
        )

    return templates.TemplateResponse(
        "portal_inbox.html",
        {
            "request": request,
            "guest_email": guest.email,
            "items": rows,
            "claim_error": "",
        },
    )


@router.post("/claim")
async def portal_claim(
    request: Request,
    link_or_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    guest = await _load_guest(request, db)
    if not guest:
        return RedirectResponse(url="/portal/login", status_code=302)

    tok = _parse_tracking_token(link_or_token)
    claim_error = ""
    if tok:
        hr = await db.execute(select(History).where(History.tracking_token == tok))
        h = hr.scalar_one_or_none()
        if not h:
            claim_error = "Link nicht gefunden."
        elif (h.to_email or "").strip().lower() != guest.email.strip().lower():
            claim_error = "Diese Nachricht gehört nicht zu Ihrer E-Mail-Adresse."
        else:
            h.guest_id = guest.id
            await db.commit()
            return RedirectResponse(url=f"/portal/m/{h.id}", status_code=302)
    else:
        claim_error = "Bitte gültigen Link oder Token einfügen."

    r = await db.execute(
        select(History)
        .where(History.guest_id == guest.id)
        .order_by(History.created_at.desc())
        .limit(200)
    )
    items = list(r.scalars().all())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for h in items:
        exp = h.expires_at
        if exp is None and h.created_at:
            exp = h.created_at + timedelta(days=h.expiry_days)
        expired = bool(exp and exp < now) or h.is_revoked
        rows.append(
            {
                "id": h.id,
                "subject": h.subject or h.filename or "Nachricht",
                "to_email": h.to_email,
                "created_at": h.created_at,
                "expired": expired,
                "read_at": h.read_at,
                "is_revoked": h.is_revoked,
            }
        )

    return templates.TemplateResponse(
        "portal_inbox.html",
        {
            "request": request,
            "guest_email": guest.email,
            "items": rows,
            "claim_error": claim_error,
        },
        status_code=400 if claim_error else 200,
    )


@router.get("/m/{history_id}", response_class=HTMLResponse)
async def portal_message(
    history_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    guest = await _load_guest(request, db)
    if not guest:
        return RedirectResponse(url="/portal/login", status_code=302)

    r = await db.execute(select(History).where(History.id == history_id))
    h = r.scalar_one_or_none()
    if not h or h.guest_id != guest.id:
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not h.read_at:
        h.read_at = now
        await db.commit()

    ur = await db.execute(select(User).where(User.id == h.user_id))
    user = ur.scalar_one_or_none()
    org = None
    rname = ""
    if user and user.org_id:
        orow = await db.execute(
            select(Organization).where(Organization.id == user.org_id)
        )
        org = orow.scalar_one_or_none()
        if org:
            rs = await db.execute(select(Reseller).where(Reseller.id == org.reseller_id))
            res = rs.scalar_one_or_none()
            if res:
                rname = res.name

    sender_name = (
        f"{user.first_name or ''} {user.last_name or ''}".strip()
        if user
        else "Unbekannt"
    )

    raw_files = []
    if h.files_json:
        raw_files = h.files_json
    elif h.filename:
        raw_files = [{"name": h.filename, "size": 0, "type": "file"}]

    file_rows = []
    for f in raw_files:
        name = f.get("name") or ""
        fn = name.split("/")[-1] or "Datei"
        enc = quote(fn, safe="")
        file_rows.append(
            {
                "display": fn,
                "href": f"/r/{h.tracking_token}/download/{enc}",
            }
        )

    exp = h.expires_at
    if exp is None and h.created_at:
        exp = h.created_at + timedelta(days=h.expiry_days)

    return templates.TemplateResponse(
        "portal_message.html",
        {
            "request": request,
            "h": h,
            "subject": h.subject or h.filename or "Nachricht",
            "message": h.message_preview or "",
            "sender_name": sender_name,
            "sender_email": user.email if user else "",
            "org_name": org.name if org else "",
            "reseller_name": rname,
            "file_rows": file_rows,
            "token": h.tracking_token,
            "expires_at": exp,
            "expired": bool(exp and exp < now) or h.is_revoked,
        },
    )
