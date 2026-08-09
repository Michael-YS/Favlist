"""Unit tests for TTL/LRU eviction and concurrent single-flight cache loading."""

from __future__ import annotations

import asyncio
import unittest

from app.cache import AsyncTTLCache


class MutableClock:
    """Provide a deterministic monotonic clock for expiry tests."""

    def __init__(self) -> None:
        """Initialize the clock at zero seconds."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current test time."""
        return self.value


class AsyncTTLCacheTests(unittest.IsolatedAsyncioTestCase):
    """Exercise cache expiration, capacity eviction, and loader coalescing."""

    async def test_ttl_expiry_reloads_value(self) -> None:
        """Expired entries must not satisfy a subsequent lookup."""
        clock = MutableClock()
        cache = AsyncTTLCache[str, int](ttl_seconds=10, max_entries=2, clock=clock)
        calls = 0

        async def loader() -> int:
            """Return a distinct value per upstream load."""
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await cache.get_or_load("one", loader), 1)
        clock.value = 11
        self.assertEqual(await cache.get_or_load("one", loader), 2)
        self.assertEqual(calls, 2)

    async def test_lru_evicts_least_recently_used_entry(self) -> None:
        """Accessing an entry refreshes its LRU position before capacity eviction."""
        cache = AsyncTTLCache[str, str](ttl_seconds=100, max_entries=2)

        async def load_one() -> str:
            """Return the first cached value."""
            return "one"

        async def load_two() -> str:
            """Return the second cached value."""
            return "two"

        async def load_three() -> str:
            """Return the third cached value."""
            return "three"

        await cache.get_or_load("one", load_one)
        await cache.get_or_load("two", load_two)
        self.assertEqual(await cache.get("one"), "one")
        await cache.get_or_load("three", load_three)
        self.assertIsNone(await cache.get("two"))
        self.assertEqual(await cache.get("one"), "one")

    async def test_concurrent_callers_share_one_loader(self) -> None:
        """Same-key concurrent misses must execute the upstream loader once."""
        cache = AsyncTTLCache[str, str]()
        gate = asyncio.Event()
        calls = 0

        async def loader() -> str:
            """Block until all competing callers have joined the flight."""
            nonlocal calls
            calls += 1
            await gate.wait()
            return "value"

        waiting = [asyncio.create_task(cache.get_or_load("id", loader)) for _ in range(8)]
        await asyncio.sleep(0)
        gate.set()
        self.assertEqual(await asyncio.gather(*waiting), ["value"] * 8)
        self.assertEqual(calls, 1)
