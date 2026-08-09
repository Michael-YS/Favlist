"""SQLAlchemy async engine, declarative base, and session lifecycle helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, selectinload

from .config import Settings


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for all persisted domain models."""


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create an async database engine with SQLite-safe connection options."""
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_async_engine(settings.database_url, connect_args=connect_args)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory that does not expire loaded objects on commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped database session and close it afterwards."""
    async with session_factory() as session:
        yield session


async def initialize_database(engine: AsyncEngine) -> None:
    """Create all core tables when they do not yet exist."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


class ComicRepository:
    """Async persistence adapter consumed by API routes and metadata workers."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to one application-managed database session."""
        self.session = session

    async def get_comic(self, comic_id: int) -> Any | None:
        """Load one comic with its ordered tags, or return ``None`` when absent."""
        from .models import Comic, ComicTag

        result = await self.session.execute(
            select(Comic)
            .where(Comic.id == comic_id)
            .options(selectinload(Comic.tag_links).selectinload(ComicTag.tag))
        )
        return result.scalar_one_or_none()

    async def set_status(self, comic_id: int, status: Any, error: str | None = None) -> None:
        """Persist a worker lifecycle status and its most recent failure message."""
        from .models import ComicStatus

        comic = await self.get_comic(comic_id)
        if comic is None:
            raise KeyError(f"comic {comic_id} does not exist")
        comic.status = status if isinstance(status, ComicStatus) else ComicStatus(status)
        comic.error = error
        await self.session.commit()

    async def save_metadata(self, comic_id: int, metadata: Mapping[str, object] | object) -> None:
        """Persist successful upstream metadata and replace tags in upstream order."""
        comic = await self.get_comic(comic_id)
        if comic is None:
            raise KeyError(f"comic {comic_id} does not exist")
        values = self._metadata_values(metadata)
        for field in (
            "title",
            "description",
            "author",
            "page_count",
            "published_at",
            "views",
            "likes",
            "comments",
            "cover_url",
        ):
            if field in values:
                setattr(comic, field, values[field])
        if "tags" in values:
            await self._replace_tags(comic, values["tags"])
        from .models import ComicStatus, utc_now

        comic.status = ComicStatus.READY
        comic.error = None
        comic.refreshed_at = utc_now()
        await self.session.commit()

    async def bump_cover_version(self, comic_id: int) -> int:
        """Increment and persist a comic cover cache version, returning the new value."""
        comic = await self.session.get(self._comic_model(), comic_id)
        if comic is None:
            raise KeyError(f"comic {comic_id} does not exist")
        comic.cover_version += 1
        await self.session.commit()
        return comic.cover_version

    async def list_unfinished_ids(self) -> list[int]:
        """Return IDs whose metadata jobs should be re-queued after a restart."""
        from .models import ComicStatus

        result = await self.session.scalars(
            select(self._comic_model().id).where(
                self._comic_model().status.in_(
                    (ComicStatus.PENDING, ComicStatus.LOADING, ComicStatus.REFRESHING)
                )
            )
        )
        return list(result)

    @staticmethod
    def _comic_model() -> Any:
        """Import the comic model lazily to avoid a database/model import cycle."""
        from .models import Comic

        return Comic

    @staticmethod
    def _metadata_values(metadata: Mapping[str, object] | object) -> Mapping[str, object]:
        """Adapt either an upstream metadata DTO or a mapping to named values."""
        if isinstance(metadata, Mapping):
            return metadata
        fields = (
            "title",
            "description",
            "author",
            "page_count",
            "published_at",
            "views",
            "likes",
            "comments",
            "cover_url",
            "tags",
        )
        return {field: getattr(metadata, field) for field in fields if hasattr(metadata, field)}

    async def _replace_tags(self, comic: Any, raw_tags: object) -> None:
        """Replace association rows with normalized unique tag names in source order."""
        from .models import ComicTag, Tag
        from .tagging import normalize_tag

        if not isinstance(raw_tags, Sequence) or isinstance(raw_tags, (str, bytes)):
            raise ValueError("metadata tags must be a sequence")
        names: list[str] = []
        for raw_tag in raw_tags:
            if isinstance(raw_tag, Mapping):
                raw_name = raw_tag.get("name")
            else:
                raw_name = getattr(raw_tag, "name", raw_tag)
            if not isinstance(raw_name, str):
                raise ValueError("metadata tags must contain strings or name mappings")
            normalized = normalize_tag(raw_name)
            if normalized and normalized not in names:
                names.append(normalized)
        comic.tag_links.clear()
        await self.session.flush()
        await self.session.execute(delete(Tag).where(~Tag.comic_links.any()))
        for position, name in enumerate(names):
            tag = await self.session.scalar(select(Tag).where(Tag.name == name))
            if tag is None:
                tag = Tag(name=name)
                self.session.add(tag)
                await self.session.flush()
            comic.tag_links.append(ComicTag(tag=tag, position=position))
