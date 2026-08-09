"""Authenticated comic list, detail, refresh, deletion, and cover endpoints."""

from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import String, asc, cast, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.dependencies import get_cover_store, get_db_session, get_task_queue
from app.models import Comic, ComicStatus, ComicTag, Tag
from app.schemas import BulkIdsRequest, ComicPage, ComicRead, DeleteComicsRequest
from app.security import require_csrf_header
from app.tagging import build_tag_reads, filter_comics_by_all_tags, tag_preferences_from_settings
from app.config import Settings, get_settings

router = APIRouter(prefix="/comics", tags=["comics"])


def _comic_read(comic: Comic, settings: Settings) -> ComicRead:
    """Serialize one loaded comic while applying the global tag display rules."""
    result = ComicRead.model_validate(comic)
    result.status = comic.status.value
    result.tags = build_tag_reads(comic.tag_links, tag_preferences_from_settings(settings))
    return result


def _normalized_ids(ids: Sequence[int]) -> list[int]:
    """De-duplicate positive IDs without changing the submitted operation order."""
    return list(dict.fromkeys(comic_id for comic_id in ids if comic_id > 0))


def _sort_expression(sort: str) -> tuple[object, ...]:
    """Return deterministic SQL sort expressions for an allowed list ordering."""
    choices: dict[str, tuple[object, ...]] = {
        "added_desc": (desc(Comic.added_at), desc(Comic.id)),
        "title_asc": (asc(Comic.title), asc(Comic.id)),
        "id_asc": (asc(Comic.id),),
        "id_desc": (desc(Comic.id),),
    }
    try:
        return choices[sort]
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort must be one of: added_desc, title_asc, id_asc, id_desc",
        ) from error


async def _get_comic_or_404(session: AsyncSession, comic_id: int) -> Comic:
    """Load one comic and its tags or return the API's standard not-found error."""
    statement = (
        select(Comic)
        .options(selectinload(Comic.tag_links).selectinload(ComicTag.tag))
        .where(Comic.id == comic_id)
    )
    comic = (await session.scalars(statement)).one_or_none()
    if comic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comic not found.")
    return comic


@router.get("", response_model=ComicPage)
async def list_comics(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=500),
    tag: list[str] = Query(default=[]),
    tags: list[str] = Query(default=[]),
    sort: str = Query(default="added_desc"),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(get_current_user),
) -> ComicPage:
    """Return a paginated list filtered by text and all requested tag names."""
    tag_filters = [*tag, *(name for value in tags for name in value.split(","))]
    statement = select(Comic)
    if search and search.strip():
        term = search.strip()
        conditions = [Comic.title.ilike(f"%{term}%"), Comic.author.ilike(f"%{term}%")]
        normalized_id = term[2:] if term.lower().startswith("jm") else term
        if normalized_id.isdigit():
            conditions.append(cast(Comic.id, String).contains(str(int(normalized_id))))
        statement = statement.where(or_(*conditions))
    statement = filter_comics_by_all_tags(statement, tag_filters)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    statement = (
        statement.options(selectinload(Comic.tag_links).selectinload(ComicTag.tag))
        .order_by(*_sort_expression(sort))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    comics = (await session.scalars(statement)).unique().all()
    return ComicPage(
        items=[_comic_read(comic, settings) for comic in comics],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{comic_id}", response_model=ComicRead)
async def get_comic(
    comic_id: int,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    _: str = Depends(get_current_user),
) -> ComicRead:
    """Return the full cached metadata for one comic."""
    return _comic_read(await _get_comic_or_404(session, comic_id), settings)


@router.get("/{comic_id}/cover")
async def get_cover(
    comic_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    covers: object = Depends(get_cover_store),
    _: str = Depends(get_current_user),
) -> Response:
    """Serve a cached WebP cover with private cache and ETag support."""
    comic = await _get_comic_or_404(session, comic_id)
    path = covers.path_for(comic_id)
    if not path.is_file():
        if not comic.cover_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover is not available yet.")
        try:
            restored = await covers.ensure_cover(comic_id, comic.cover_url)
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The cover could not be restored.",
            ) from error
        path = restored.path
        if restored.created:
            comic.cover_version += 1
            await session.commit()
    etag = covers.etag_for(path)
    headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


@router.post("/{comic_id}/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh_comic(
    comic_id: int,
    session: AsyncSession = Depends(get_db_session),
    task_queue: object = Depends(get_task_queue),
    _: str = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_header),
) -> Response:
    """Queue one existing comic for a forced metadata and cover refresh."""
    comic = await _get_comic_or_404(session, comic_id)
    comic.status = ComicStatus.REFRESHING
    comic.error = None
    await session.commit()
    await task_queue.enqueue(comic_id, refresh=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk-refresh", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_refresh_comics(
    payload: BulkIdsRequest,
    session: AsyncSession = Depends(get_db_session),
    task_queue: object = Depends(get_task_queue),
    _: str = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_header),
) -> Response:
    """Queue every selected existing comic for a forced refresh."""
    comic_ids = _normalized_ids(payload.ids)
    existing = list((await session.scalars(select(Comic).where(Comic.id.in_(comic_ids)))).all())
    for comic in existing:
        comic.status = ComicStatus.REFRESHING
        comic.error = None
    await session.commit()
    for comic in existing:
        await task_queue.enqueue(comic.id, refresh=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comics(
    payload: DeleteComicsRequest,
    session: AsyncSession = Depends(get_db_session),
    covers: object = Depends(get_cover_store),
    _: str = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_header),
) -> Response:
    """Delete selected existing comics and their associated cover cache files."""
    comics = list(
        (await session.scalars(select(Comic).where(Comic.id.in_(_normalized_ids(payload.ids))))).all()
    )
    for comic in comics:
        await covers.delete(comic.id)
        await session.delete(comic)
    await session.flush()
    await session.execute(delete(Tag).where(~Tag.comic_links.any()))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
