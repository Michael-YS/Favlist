"""Authenticated tag facet endpoint with shared visual emphasis semantics."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import Settings, get_settings
from app.dependencies import get_db_session
from app.models import ComicTag, Tag
from app.schemas import TagRead
from app.tagging import tag_preferences_from_settings

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[TagRead])
async def list_tags(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(get_current_user),
) -> list[TagRead]:
    """Return all tag facets, their usage count, and the configured emphasis."""
    preferences = tag_preferences_from_settings(settings)
    rows = (
        await session.execute(
            select(Tag.name, func.count(ComicTag.comic_id))
            .outerjoin(ComicTag, ComicTag.tag_id == Tag.id)
            .group_by(Tag.id, Tag.name)
        )
    ).all()
    response = [
        TagRead(name=name, count=count, emphasis=preferences.emphasis_for(name))
        for name, count in rows
    ]
    return sorted(
        response,
        key=lambda tag: preferences.display_key(tag.name, tag.display_order or 0),
    )
