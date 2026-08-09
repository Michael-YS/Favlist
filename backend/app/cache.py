"""Async TTL/LRU cache primitives for short-lived upstream responses."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Hashable, TypeVar


K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True)
class CacheStats:
    """Expose cache counters for diagnostics and focused tests."""

    hits: int
    misses: int
    size: int
    in_flight: int


class AsyncTTLCache(Generic[K, V]):
    """A bounded async cache with TTL expiry and per-key single-flight loads."""

    def __init__(
        self,
        ttl_seconds: float = 15 * 60,
        max_entries: int = 512,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a cache using the supplied positive TTL and capacity."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._in_flight: dict[K, asyncio.Task[V]] = {}
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: K) -> V | None:
        """Return a non-expired value for ``key`` and update its LRU position."""
        async with self._lock:
            return self._get_locked(key)

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        *,
        bypass_cache: bool = False,
    ) -> V:
        """Load once concurrently, optionally ignoring but then replacing a cached value."""
        async with self._lock:
            if not bypass_cache:
                cached = self._get_locked(key)
                if cached is not None:
                    return cached
            self._misses += 1
            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(self._load(key, loader))
                self._in_flight[key] = task
        return await asyncio.shield(task)

    async def invalidate(self, key: K) -> None:
        """Remove a cached key without disturbing a currently running loader."""
        async with self._lock:
            self._items.pop(key, None)

    async def clear(self) -> None:
        """Remove all completed cached values."""
        async with self._lock:
            self._items.clear()

    async def stats(self) -> CacheStats:
        """Return a consistent snapshot of cache state and counters."""
        async with self._lock:
            self._purge_expired_locked()
            return CacheStats(self._hits, self._misses, len(self._items), len(self._in_flight))

    def _get_locked(self, key: K) -> V | None:
        """Get a value while holding the cache lock, evicting expiry first."""
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        self._hits += 1
        return value

    async def _load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
    ) -> V:
        """Execute a loader and atomically record its successful result."""
        try:
            value = await loader()
            async with self._lock:
                self._items[key] = (self._clock() + self._ttl_seconds, value)
                self._items.move_to_end(key)
                self._purge_expired_locked()
                while len(self._items) > self._max_entries:
                    self._items.popitem(last=False)
            return value
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)

    def _purge_expired_locked(self) -> None:
        """Drop expired entries while holding the lock."""
        now = self._clock()
        for key, (expires_at, _) in list(self._items.items()):
            if expires_at <= now:
                del self._items[key]
