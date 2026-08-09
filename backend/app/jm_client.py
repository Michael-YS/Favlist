"""Asynchronous, injectable adapter around the pinned jmcomic APP client."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .cache import AsyncTTLCache


@dataclass(frozen=True)
class UpstreamTag:
    """A tag exactly as ordered by the upstream response."""

    name: str
    upstream_order: int


@dataclass(frozen=True)
class ComicMetadata:
    """Normalized metadata consumed by persistence and worker layers."""

    comic_id: int
    title: str | None
    description: str | None
    author: str | None
    page_count: int | None
    published_at: str | None
    views: int | None
    likes: int | None
    comments: int | None
    tags: tuple[UpstreamTag, ...]
    cover_url: str | None


class ComicUpstream(Protocol):
    """Small testable contract implemented by an upstream metadata provider."""

    async def fetch_comic(self, comic_id: int, *, force_refresh: bool = False) -> ComicMetadata:
        """Fetch and normalize one comic's metadata."""


class AsyncJmComicClient:
    """Fetch upstream album details through an APP client with TTL/LRU protection."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        cache: AsyncTTLCache[int, ComicMetadata] | None = None,
    ) -> None:
        """Create an adapter; ``client_factory`` enables deterministic test injection."""
        self._client_factory = client_factory or self._build_app_client
        self._cache = cache or AsyncTTLCache()
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def fetch_comic(self, comic_id: int, *, force_refresh: bool = False) -> ComicMetadata:
        """Return metadata, bypassing short-term cache for explicit refreshes."""
        if comic_id <= 0:
            raise ValueError("comic_id must be positive")

        async def load() -> ComicMetadata:
            """Load and normalize the album from the configured upstream client."""
            client = await self._get_client()
            album = await self._get_album(client, comic_id)
            return self._normalize_album(comic_id, album)

        return await self._cache.get_or_load(comic_id, load, bypass_cache=force_refresh)

    async def _get_client(self) -> Any:
        """Lazily create the APP client once, avoiding import-time side effects."""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                candidate = self._client_factory()
                self._client = await _await_if_needed(candidate)
            return self._client

    async def _get_album(self, client: Any, comic_id: int) -> Any:
        """Call asynchronous or synchronous jmcomic album APIs safely."""
        method = getattr(client, "get_album_detail", None) or getattr(client, "get_album", None)
        if method is None:
            raise RuntimeError("Configured upstream client has no album-detail method")
        if inspect.iscoroutinefunction(method):
            return await method(str(comic_id))
        # JmApiClient.get_album_detail is synchronous. It must never run on the
        # FastAPI event loop when an older jmcomic installation lacks async API.
        result = await asyncio.to_thread(method, str(comic_id))
        return await _await_if_needed(result)

    def _build_app_client(self) -> Any:
        """Create jmcomic's API/APP client using optional AVS cookie and proxy env vars."""
        try:
            jmcomic = importlib.import_module("jmcomic")
        except ImportError as exc:
            raise RuntimeError("jmcomic==2.7.3 is required for upstream requests") from exc

        proxy = os.getenv("JM_PROXY")
        avs_cookie = os.getenv("JM_AVS_COOKIE")
        client_config: dict[str, Any] = {"impl": "api", "async_impl": "async_api"}
        if proxy:
            client_config["postman"] = {"meta_data": {"proxies": {"http": proxy, "https": proxy}}}
        if avs_cookie:
            client_config.setdefault("postman", {}).setdefault("meta_data", {})["cookies"] = {"AVS": avs_cookie}
        # construct() performs jmcomic's documented deep merge with defaults;
        # mutating/replacing the client object would discard retry/domain defaults.
        option = jmcomic.JmOption.construct({"client": client_config})

        # jmcomic releases have exposed both spellings; 2.7.x documentation
        # identifies the async option factory and the async_api registry key.
        for builder_name in ("new_async_client", "new_jm_async_client"):
            builder = getattr(option, builder_name, None)
            if builder is not None:
                return builder()
        for builder_name in ("new_jm_client", "build_jm_client"):
            builder = getattr(option, builder_name, None)
            if builder is not None:
                try:
                    return builder(impl="api")
                except TypeError:
                    return builder()
        get_client = getattr(getattr(jmcomic, "JmModuleConfig", None), "get_client", None)
        if get_client is not None:
            return get_client(option, impl="api")
        raise RuntimeError("jmcomic==2.7.3 does not expose an APP client builder")

    def _normalize_album(self, comic_id: int, album: Any) -> ComicMetadata:
        """Map jmcomic object or mapping fields into a stable application DTO."""
        tags = tuple(
            UpstreamTag(_text(tag), order)
            for order, tag in enumerate(_value(album, "tags", "tag_list", default=()) or ())
            if _text(tag)
        )
        return ComicMetadata(
            comic_id=comic_id,
            title=_optional_text(_value(album, "name", "title")),
            description=_optional_text(_value(album, "description", "intro")),
            author=_optional_text(_value(album, "author", "authors")),
            page_count=_optional_int(_value(album, "page_count", "page_total", "total_pages")),
            published_at=_optional_text(_value(album, "date", "published_at", "publish_date")),
            views=_optional_int(_value(album, "views", "view_count")),
            likes=_optional_int(_value(album, "likes", "like_count")),
            comments=_optional_int(_value(album, "comment_count", "comments")),
            tags=tags,
            cover_url=(
                _optional_text(_value(album, "cover", "cover_url", "image_url"))
                or self._toolkit_cover_url(comic_id)
            ),
        )

    def _toolkit_cover_url(self, comic_id: int) -> str | None:
        """Generate the canonical cover URL with jmcomic's official toolkit helper."""
        try:
            jmcomic = importlib.import_module("jmcomic")
        except ImportError:
            return None
        toolkit = getattr(jmcomic, "JmcomicText", None)
        generator = getattr(toolkit, "get_album_cover_url", None)
        if generator is None:
            return None
        return _optional_text(generator(comic_id))


async def _await_if_needed(value: Any) -> Any:
    """Await a value only when a test factory or client builder returned an awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


def _value(source: Any, *names: str, default: Any = None) -> Any:
    """Read the first present mapping key or object attribute from ``source``."""
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        value = getattr(source, name, None)
        if value is not None:
            return value
    return default


def _text(value: Any) -> str:
    """Turn a potentially structured upstream value into stripped display text."""
    if isinstance(value, Mapping):
        value = value.get("name", value.get("title", ""))
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    """Return a non-empty text representation or ``None``."""
    text = _text(value) if value is not None else ""
    return text or None


def _optional_int(value: Any) -> int | None:
    """Return an integer representation when the upstream value is valid."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
