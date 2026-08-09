"""Authenticated plain-text upstream identifier export endpoint."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.dependencies import get_db_session
from app.models import Comic

router = APIRouter(tags=["exports"])


@router.get("/export/ids", response_class=PlainTextResponse)
async def export_identifiers(
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(get_current_user),
) -> PlainTextResponse:
    """Export every persisted identifier as UTF-8 text in stable numeric order."""
    comic_ids = (await session.scalars(select(Comic.id).order_by(Comic.id))).all()
    body = "".join(f"JM{comic_id}\n" for comic_id in comic_ids)
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="favlist-ids.txt"'},
    )
