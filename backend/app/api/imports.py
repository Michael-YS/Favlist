"""Authenticated import endpoint and JM identifier parsing helpers."""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.dependencies import get_db_session, get_task_queue
from app.models import Comic, ComicStatus
from app.schemas import ImportRequest, ImportSummary
from app.security import require_csrf_header

router = APIRouter(tags=["imports"])

_IMPORT_TOKEN_SPLIT = re.compile(r"[\s,;]+")
_JM_IDENTIFIER = re.compile(r"(?:jm)?(\d+)", re.IGNORECASE)
_IMPORT_LIMIT = 5_000


def parse_import_identifiers(text: str) -> tuple[list[int], int]:
    """Parse separated JM or numeric identifiers and count invalid tokens."""
    identifiers: list[int] = []
    invalid = 0
    for token in (item for item in _IMPORT_TOKEN_SPLIT.split(text.strip()) if item):
        match = _JM_IDENTIFIER.fullmatch(token)
        if match is None or int(match.group(1)) <= 0:
            invalid += 1
            continue
        identifiers.append(int(match.group(1)))
    return identifiers, invalid


def _pending_status() -> ComicStatus | str:
    """Return the pending enum value while supporting conventional enum names."""
    return getattr(ComicStatus, "PENDING", getattr(ComicStatus, "pending", "pending"))


@router.post("/import", response_model=ImportSummary)
async def import_comics(
    payload: ImportRequest,
    session: AsyncSession = Depends(get_db_session),
    task_queue: object = Depends(get_task_queue),
    _: str = Depends(get_current_user),
    _csrf: None = Depends(require_csrf_header),
) -> ImportSummary:
    """Persist new identifiers and enqueue their initial metadata retrieval."""
    parsed, invalid = parse_import_identifiers(payload.text)
    if len(parsed) > _IMPORT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"An import may contain at most {_IMPORT_LIMIT} identifiers.",
        )

    unique_ids = list(dict.fromkeys(parsed))
    if not unique_ids:
        return ImportSummary(added=0, duplicate=0, invalid=invalid)

    existing_ids = set(
        (await session.scalars(select(Comic.id).where(Comic.id.in_(unique_ids)))).all()
    )
    ids_to_add = [comic_id for comic_id in unique_ids if comic_id not in existing_ids]
    for comic_id in ids_to_add:
        session.add(Comic(id=comic_id, status=_pending_status()))
    await session.commit()

    for comic_id in ids_to_add:
        await task_queue.enqueue(comic_id)

    return ImportSummary(
        added=len(ids_to_add),
        duplicate=(len(parsed) - len(unique_ids)) + len(existing_ids),
        invalid=invalid,
    )
