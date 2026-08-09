"""Persistent SQLAlchemy models for comics, tags, and revocable sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for model defaults."""
    return datetime.now(timezone.utc)


class ComicStatus(str, Enum):
    """Lifecycle states for an imported comic's background metadata work."""

    PENDING = "pending"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    REFRESHING = "refreshing"


class Comic(Base):
    """A tracked comic and its cached upstream metadata."""

    __tablename__ = "comics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ComicStatus] = mapped_column(default=ComicStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_version: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tag_links: Mapped[list[ComicTag]] = relationship(
        back_populates="comic",
        cascade="all, delete-orphan",
        order_by="ComicTag.position",
    )


class Tag(Base):
    """A normalized tag name shared by every comic that has the tag."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    comic_links: Mapped[list[ComicTag]] = relationship(back_populates="tag")


class ComicTag(Base):
    """Association row retaining the upstream tag order for one comic."""

    __tablename__ = "comic_tags"
    __table_args__ = (UniqueConstraint("comic_id", "tag_id", name="uq_comic_tag"),)

    comic_id: Mapped[int] = mapped_column(ForeignKey("comics.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    comic: Mapped[Comic] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(back_populates="comic_links")


class AuthSession(Base):
    """A hashed server-side session record supporting logout revocation."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    revoked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
