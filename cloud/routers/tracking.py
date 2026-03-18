"""cloud/routers/tracking.py — Email open tracking + link-click redirect."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.shared import History

router = APIRouter(tags=["tracking"])

# Minimal 1x1 transparent GIF
_PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


@router.get("/track/o/{token}", include_in_schema=False)
async def track_open(token: str, db: AsyncSession = Depends(get_db)) -> Response:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    if h and not h.opened_at:
        h.opened_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/track/l/{token}", include_in_schema=False)
async def track_link(token: str, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    result = await db.execute(select(History).where(History.tracking_token == token))
    h = result.scalar_one_or_none()
    url = "#"
    if h:
        if not h.link_clicked_at:
            h.link_clicked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
        url = h.share_url or "#"
    return RedirectResponse(url=url, status_code=302)
