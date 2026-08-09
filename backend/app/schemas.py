"""Pydantic request and response schemas shared by FastAPI route handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Credentials submitted to the single administrator login endpoint."""

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class AuthUser(BaseModel):
    """The authenticated administrator identity returned to the browser."""

    username: str


class TagRead(BaseModel):
    """A tag rendered with its global emphasis and display position."""

    name: str
    emphasis: Literal["disliked", "liked", "normal"]
    count: int | None = Field(default=None, ge=0)
    display_order: int | None = Field(default=None, ge=0)


class ComicRead(BaseModel):
    """Serialized comic metadata for a list row or detail drawer."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    description: str | None = None
    author: str | None
    page_count: int | None = None
    published_at: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    status: Literal["pending", "loading", "ready", "error", "refreshing"]
    error: str | None
    cover_url: str | None = None
    tags: list[TagRead] = Field(default_factory=list)
    cover_version: int
    added_at: datetime | None = None
    refreshed_at: datetime | None = None


class ComicPage(BaseModel):
    """One paginated response from the comic list endpoint."""

    items: list[ComicRead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ImportRequest(BaseModel):
    """A raw text block containing JM identifiers to import."""

    text: str = Field(max_length=100_000)


class ImportSummary(BaseModel):
    """Counts returned after identifiers are parsed and persisted."""

    added: int = Field(ge=0)
    duplicate: int = Field(ge=0)
    invalid: int = Field(ge=0)


class BulkIdsRequest(BaseModel):
    """A non-empty collection of comic identifiers for bulk operations."""

    ids: list[int] = Field(min_length=1, max_length=5_000)


class DeleteComicsRequest(BulkIdsRequest):
    """The request body accepted by the bulk comic deletion endpoint."""
