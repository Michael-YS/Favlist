"""Shared FastAPI dependency providers for the Favlist HTTP layer."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session owned by the application."""
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("The database session factory has not been configured.")
    async with session_factory() as session:
        yield session


def get_task_queue(request: Request) -> Any:
    """Return the configured background metadata queue or fail clearly."""
    queue = getattr(request.app.state, "task_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The metadata worker is not available.",
        )
    return queue


def get_cover_store(request: Request) -> Any:
    """Return the configured cover cache service or fail clearly."""
    covers = getattr(request.app.state, "cover_store", None)
    if covers is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The cover cache is not available.",
        )
    return covers
