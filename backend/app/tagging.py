"""Canonical tag normalization, emphasis classification, sorting, and filtering."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Select, distinct, func, select

from .config import Settings
from .models import Comic, ComicTag, Tag
from .schemas import TagRead

Emphasis = str


def normalize_tag(value: str) -> str:
    """Normalize Unicode and collapsible whitespace for exact tag matching."""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _normalize_configured_tags(tags: Iterable[str], setting_name: str) -> tuple[str, ...]:
    """Normalize a configured ordered list and reject blank or duplicate names."""
    normalized = tuple(normalize_tag(tag) for tag in tags)
    if any(not tag for tag in normalized):
        raise ValueError(f"{setting_name} cannot contain blank tag names")
    duplicates = sorted({tag for tag in normalized if normalized.count(tag) > 1})
    if duplicates:
        raise ValueError(f"{setting_name} contains duplicate tags: {', '.join(duplicates)}")
    return normalized


@dataclass(frozen=True, slots=True)
class TagPreferences:
    """Validated globally ordered liked and disliked tag preference lists."""

    liked_tags: tuple[str, ...]
    disliked_tags: tuple[str, ...]

    @classmethod
    def from_lists(
        cls,
        liked_tags: Iterable[str],
        disliked_tags: Iterable[str],
    ) -> "TagPreferences":
        """Build preferences and fail clearly if a tag belongs to both groups."""
        liked = _normalize_configured_tags(liked_tags, "liked_tags")
        disliked = _normalize_configured_tags(disliked_tags, "disliked_tags")
        conflicts = sorted(set(liked).intersection(disliked))
        if conflicts:
            raise ValueError("tags cannot be both liked and disliked: " + ", ".join(conflicts))
        return cls(liked_tags=liked, disliked_tags=disliked)

    def emphasis_for(self, tag_name: str) -> Emphasis:
        """Return the visual emphasis assigned to a canonical tag name."""
        normalized = normalize_tag(tag_name)
        if normalized in self.disliked_tags:
            return "disliked"
        if normalized in self.liked_tags:
            return "liked"
        return "normal"

    def display_key(self, tag_name: str, upstream_position: int) -> tuple[int, int]:
        """Return the deterministic limited-space display sort key for a tag."""
        normalized = normalize_tag(tag_name)
        if normalized in self.disliked_tags:
            return 0, self.disliked_tags.index(normalized)
        if normalized in self.liked_tags:
            return 1, self.liked_tags.index(normalized)
        return 2, upstream_position


def tag_preferences_from_settings(settings: Settings) -> TagPreferences:
    """Convert raw application settings into validated tag preferences."""
    return TagPreferences.from_lists(settings.liked_tags, settings.disliked_tags)


def build_tag_reads(
    tag_links: Sequence[ComicTag],
    preferences: TagPreferences,
) -> list[TagRead]:
    """Convert ordered ORM tag links into consistently emphasized API tags."""
    ordered = sorted(
        tag_links,
        key=lambda link: preferences.display_key(link.tag.name, link.position),
    )
    return [
        TagRead(
            name=normalize_tag(link.tag.name),
            emphasis=preferences.emphasis_for(link.tag.name),
            display_order=index,
        )
        for index, link in enumerate(ordered)
    ]


def sort_tag_reads(tags: Sequence[TagRead], preferences: TagPreferences) -> list[TagRead]:
    """Sort existing API tags by emphasis priority and their upstream position."""
    return sorted(
        tags,
        key=lambda tag: preferences.display_key(tag.name, tag.display_order or 0),
    )


def normalize_tag_filter(tag_names: Iterable[str]) -> tuple[str, ...]:
    """Normalize and de-duplicate query tags while retaining their first occurrence."""
    result: list[str] = []
    for tag_name in tag_names:
        normalized = normalize_tag(tag_name)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def filter_comics_by_all_tags(
    statement: Select[tuple[Comic]],
    tag_names: Iterable[str],
) -> Select[tuple[Comic]]:
    """Add an AND-semantics tag predicate to a SQLAlchemy comic statement."""
    normalized_tags = normalize_tag_filter(tag_names)
    if not normalized_tags:
        return statement
    matching_comic_ids = (
        select(ComicTag.comic_id)
        .join(Tag, Tag.id == ComicTag.tag_id)
        .where(Tag.name.in_(normalized_tags))
        .group_by(ComicTag.comic_id)
        .having(func.count(distinct(Tag.name)) == len(normalized_tags))
    )
    return statement.where(Comic.id.in_(matching_comic_ids))
