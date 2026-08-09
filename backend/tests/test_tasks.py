"""Unit tests for worker retry behavior, error retention, and restart recovery."""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from app.covers import CoverSecuritySettings, CoverStore
from app.jm_client import AsyncJmComicClient, ComicMetadata
from app.tasks import ComicTaskQueue, QueueSettings


def metadata(comic_id: int = 7) -> ComicMetadata:
    """Create the smallest valid successful upstream response for worker tests."""
    return ComicMetadata(
        comic_id=comic_id,
        title="A title",
        description=None,
        author=None,
        page_count=None,
        published_at=None,
        views=None,
        likes=None,
        comments=None,
        tags=(),
        cover_url=None,
    )


class FakeRepository:
    """Keep worker persistence observations in memory."""

    def __init__(self, records: dict[int, dict[str, object]]) -> None:
        """Start with a mapping representing persisted comic rows."""
        self.records = records
        self.statuses: list[tuple[int, str, str | None]] = []
        self.saved: list[ComicMetadata] = []

    async def get_comic(self, comic_id: int) -> dict[str, object] | None:
        """Return a fake row by identifier."""
        return self.records.get(comic_id)

    async def set_status(self, comic_id: int, status: str, error: str | None = None) -> None:
        """Record status updates and mutate the fake row."""
        self.statuses.append((comic_id, status, error))
        self.records[comic_id]["status"] = status
        self.records[comic_id]["error"] = error

    async def save_metadata(self, comic_id: int, value: ComicMetadata) -> None:
        """Record a successful save and update the fake row title."""
        self.saved.append(value)
        self.records[comic_id]["title"] = value.title

    async def bump_cover_version(self, comic_id: int) -> None:
        """Increment a cover version when a cover was written."""
        self.records[comic_id]["cover_version"] = int(self.records[comic_id].get("cover_version", 0)) + 1

    async def list_unfinished_ids(self) -> list[int]:
        """Return the current fake identifiers awaiting recovery."""
        return list(self.records)


class FlakyUpstream:
    """Fail a configurable number of times before producing metadata."""

    def __init__(self, failures: int) -> None:
        """Set the number of initial attempts that should fail."""
        self.failures = failures
        self.calls = 0

    async def fetch_comic(self, comic_id: int, *, force_refresh: bool = False) -> ComicMetadata:
        """Raise transiently or return successful metadata."""
        del force_refresh
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary upstream error")
        return metadata(comic_id)


class AsyncAlbumClient:
    """Expose a native async album method for adapter-await tests."""

    def __init__(self) -> None:
        """Initialize the observation flag to false."""
        self.awaited = False

    async def get_album_detail(self, comic_id: str) -> dict[str, object]:
        """Yield once before returning an album mapping."""
        await asyncio.sleep(0)
        self.awaited = True
        return {"id": comic_id, "name": "async title", "cover": "https://cover"}


class SyncAlbumClient:
    """Expose a deliberately blocking synchronous album method."""

    def get_album_detail(self, comic_id: str) -> dict[str, object]:
        """Block briefly to reveal accidental event-loop execution."""
        time.sleep(0.08)
        return {"id": comic_id, "name": "sync title", "cover": "https://cover"}


class CoverUpstream:
    """Return refreshed metadata that includes a cover URL."""

    async def fetch_comic(self, comic_id: int, *, force_refresh: bool = False) -> ComicMetadata:
        """Return one deterministic metadata snapshot with a cover destination."""
        del force_refresh
        value = metadata(comic_id)
        return ComicMetadata(
            comic_id=value.comic_id,
            title="new snapshot",
            description=value.description,
            author=value.author,
            page_count=value.page_count,
            published_at=value.published_at,
            views=value.views,
            likes=value.likes,
            comments=value.comments,
            tags=value.tags,
            cover_url="https://covers.example/cover.png",
        )


class DeletingRepository(FakeRepository):
    """Simulate deletion between upstream completion and metadata persistence."""

    def __init__(self, records: dict[int, dict[str, object]], deleted_id: int) -> None:
        """Configure the identifier deleted during its save operation."""
        super().__init__(records)
        self.deleted_id = deleted_id

    async def set_status(self, comic_id: int, status: str, error: str | None = None) -> None:
        """Raise the core repository's missing-row error after deletion."""
        if comic_id not in self.records:
            raise KeyError(f"comic {comic_id} does not exist")
        await super().set_status(comic_id, status, error)

    async def save_metadata(self, comic_id: int, value: ComicMetadata) -> None:
        """Delete the chosen row at the exact persistence race point."""
        if comic_id == self.deleted_id:
            self.records.pop(comic_id, None)
            raise KeyError(f"comic {comic_id} does not exist")
        await super().save_metadata(comic_id, value)


async def no_sleep(_: float) -> None:
    """Avoid real backoff delays in retry unit tests."""


class ComicTaskQueueTests(unittest.IsolatedAsyncioTestCase):
    """Verify jobs retry and do not erase a successful refresh snapshot on failure."""

    async def test_retries_three_attempts_then_saves_metadata(self) -> None:
        """The third allowed retry attempt produces a ready record."""
        repository = FakeRepository({7: {"title": None}})
        upstream = FlakyUpstream(failures=2)
        queue = ComicTaskQueue(
            repository,
            upstream,
            settings=QueueSettings(concurrency=1, timeout_seconds=1, max_attempts=3),
            sleep=no_sleep,
        )
        await queue.start()
        await queue.enqueue(7)
        await queue.join()
        await queue.stop()
        self.assertEqual(upstream.calls, 3)
        self.assertEqual(len(repository.saved), 1)
        self.assertEqual(repository.statuses[-1], (7, "ready", None))

    async def test_failed_refresh_preserves_existing_metadata(self) -> None:
        """Refresh failure must record an error without running metadata persistence."""
        repository = FakeRepository({7: {"title": "old snapshot", "refreshed_at": "earlier"}})
        upstream = FlakyUpstream(failures=3)
        queue = ComicTaskQueue(
            repository,
            upstream,
            settings=QueueSettings(concurrency=1, timeout_seconds=1, max_attempts=3),
            sleep=no_sleep,
        )
        await queue.start()
        await queue.enqueue(7, refresh=True)
        await queue.join()
        await queue.stop()
        self.assertEqual(repository.records[7]["title"], "old snapshot")
        self.assertEqual(repository.saved, [])
        self.assertEqual(repository.statuses[0][1], "refreshing")
        self.assertEqual(repository.statuses[-1][1], "error")

    async def test_recovery_requeues_persisted_unfinished_identifiers(self) -> None:
        """Startup recovery queues all repository-provided unfinished jobs."""
        repository = FakeRepository({3: {"title": None}, 4: {"title": None}})
        queue = ComicTaskQueue(
            repository,
            FlakyUpstream(failures=0),
            settings=QueueSettings(concurrency=1, timeout_seconds=1, max_attempts=1),
        )
        await queue.start()
        self.assertEqual(await queue.recover_unfinished(), 2)
        await queue.join()
        await queue.stop()
        self.assertEqual(len(repository.saved), 2)

    async def test_deleted_job_does_not_kill_worker(self) -> None:
        """A row deleted in flight is ignored and the same worker handles its next job."""
        repository = DeletingRepository({7: {"title": None}, 8: {"title": None}}, deleted_id=7)
        queue = ComicTaskQueue(
            repository,
            FlakyUpstream(failures=0),
            settings=QueueSettings(concurrency=1, timeout_seconds=1, max_attempts=1),
        )
        await queue.start()
        await queue.enqueue(7)
        await queue.enqueue(8)
        await queue.join()
        self.assertTrue(queue.is_running)
        await queue.stop()
        self.assertEqual([saved.comic_id for saved in repository.saved], [8])

    async def test_cover_timeout_preserves_snapshot_and_cover_then_records_error(self) -> None:
        """A cover deadline failure retains old data while exposing a bounded error."""
        async def slow_download(_: str) -> bytes:
            """Outlast the cover operation's deliberately short deadline."""
            await asyncio.sleep(0.05)
            return b"unreachable"

        repository = FakeRepository({7: {"title": "old snapshot", "refreshed_at": "earlier"}})
        with tempfile.TemporaryDirectory() as directory:
            old_cover = Path(directory) / "7.webp"
            old_cover.write_bytes(b"old cover")
            covers = CoverStore(
                directory,
                downloader=slow_download,
                security=CoverSecuritySettings(total_timeout_seconds=0.01),
            )
            queue = ComicTaskQueue(
                repository,
                CoverUpstream(),
                covers=covers,
                settings=QueueSettings(concurrency=1, timeout_seconds=1, max_attempts=1),
            )
            await queue.start()
            await queue.enqueue(7, refresh=True)
            await queue.join()
            await queue.stop()
            self.assertEqual(repository.records[7]["title"], "old snapshot")
            self.assertEqual(repository.saved, [])
            self.assertEqual(old_cover.read_bytes(), b"old cover")
            self.assertEqual(repository.statuses[-1][1], "error")


class AsyncJmComicClientTests(unittest.IsolatedAsyncioTestCase):
    """Verify native async and legacy sync clients both preserve event-loop progress."""

    async def test_native_async_album_method_is_awaited(self) -> None:
        """The adapter awaits the official asynchronous client method."""
        raw_client = AsyncAlbumClient()

        def create_client() -> AsyncAlbumClient:
            """Return the injected native asynchronous client."""
            return raw_client

        client = AsyncJmComicClient(client_factory=create_client)
        result = await client.fetch_comic(11)
        self.assertTrue(raw_client.awaited)
        self.assertEqual(result.title, "async title")

    async def test_sync_album_method_runs_off_event_loop(self) -> None:
        """A synchronous fallback call must not block unrelated async timers."""
        client = AsyncJmComicClient(client_factory=SyncAlbumClient)
        timer = asyncio.create_task(asyncio.sleep(0.02))
        result = await client.fetch_comic(12)
        self.assertTrue(timer.done())
        self.assertEqual(result.title, "sync title")
