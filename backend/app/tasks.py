"""Bounded background retrieval queue with retries and restart recovery hooks."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .covers import CoverStore
from .jm_client import ComicMetadata, ComicUpstream


class TaskRepository(Protocol):
    """Persistence operations required by ``ComicTaskQueue`` without ORM coupling."""

    async def get_comic(self, comic_id: int) -> Any | None:
        """Return the persisted comic, if present."""

    async def set_status(self, comic_id: int, status: str, error: str | None = None) -> None:
        """Set a worker-visible status and optional error message."""

    async def save_metadata(self, comic_id: int, metadata: ComicMetadata) -> None:
        """Persist a successfully fetched metadata snapshot and ordered tags."""

    async def bump_cover_version(self, comic_id: int) -> None:
        """Increment the persistent cache-busting cover version."""

    async def list_unfinished_ids(self) -> Iterable[int]:
        """List records left pending/loading/refreshing by a prior process."""


class SessionTaskRepository:
    """Create a short-lived SQLAlchemy repository per worker persistence operation."""

    def __init__(self, session_factory: Any) -> None:
        """Bind the adapter to an async-session factory rather than a shared session."""
        self._session_factory = session_factory

    async def get_comic(self, comic_id: int) -> Any | None:
        """Load a comic through a session owned by this one operation."""
        return await self._run("get_comic", comic_id)

    async def set_status(self, comic_id: int, status: str, error: str | None = None) -> None:
        """Persist a lifecycle state without sharing an AsyncSession between workers."""
        await self._run("set_status", comic_id, status, error)

    async def save_metadata(self, comic_id: int, metadata: ComicMetadata) -> None:
        """Persist one successful upstream snapshot through a fresh repository."""
        await self._run("save_metadata", comic_id, metadata)

    async def bump_cover_version(self, comic_id: int) -> int:
        """Increment the cache-busting version using a short-lived transaction."""
        return await self._run("bump_cover_version", comic_id)

    async def list_unfinished_ids(self) -> Iterable[int]:
        """Read restart-recovery IDs without retaining a database session."""
        return await self._run("list_unfinished_ids")

    async def _run(self, method_name: str, *args: Any) -> Any:
        """Open a session, call the matching core repository method, then close it."""
        from .database import ComicRepository

        async with self._session_factory() as session:
            repository = ComicRepository(session)
            return await _call(repository, method_name, *args)


@dataclass(frozen=True)
class QueueSettings:
    """Worker limits matching the product's upstream-safety defaults."""

    concurrency: int = 4
    timeout_seconds: float = 20.0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        """Reject invalid queue limits before any worker is started."""
        if self.concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")


@dataclass(frozen=True)
class _WorkItem:
    """Represent one metadata retrieval request in the in-process queue."""

    comic_id: int
    refresh: bool


class ComicTaskQueue:
    """Execute Favlist retrieval jobs with bounded concurrency and safe recovery."""

    def __init__(
        self,
        repository: TaskRepository | Any,
        upstream: ComicUpstream,
        *,
        covers: CoverStore | None = None,
        settings: QueueSettings | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create an idle queue; call ``start`` explicitly during app lifespan."""
        self._repository = repository
        self._upstream = upstream
        self._covers = covers
        self._settings = settings or QueueSettings()
        self._sleep = sleep
        self._queue: asyncio.Queue[_WorkItem | None] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._queued: dict[int, bool] = {}
        self._active: set[int] = set()
        self._after_active: dict[int, bool] = {}
        self._state_lock = asyncio.Lock()
        self._running = False

    @property
    def is_running(self) -> bool:
        """Report whether worker tasks have been started and not yet stopped."""
        return self._running

    async def start(self) -> None:
        """Start workers exactly once; no task starts merely by importing this module."""
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"favlist-metadata-worker-{index}")
            for index in range(self._settings.concurrency)
        ]

    async def stop(self) -> None:
        """Stop workers after jobs already dequeued complete."""
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, comic_id: int, *, refresh: bool = False) -> bool:
        """Queue a comic, coalescing duplicate requests while retaining refresh intent."""
        if comic_id <= 0:
            raise ValueError("comic_id must be positive")
        async with self._state_lock:
            if comic_id in self._active:
                self._after_active[comic_id] = self._after_active.get(comic_id, False) or refresh
                return False
            queued_refresh = self._queued.get(comic_id)
            if queued_refresh is not None:
                self._queued[comic_id] = queued_refresh or refresh
                return False
            self._queued[comic_id] = refresh
            await self._queue.put(_WorkItem(comic_id, refresh))
            return True

    async def recover_unfinished(self) -> int:
        """Requeue persisted unfinished records after process start or crash recovery."""
        identifiers = await _call(self._repository, "list_unfinished_ids")
        count = 0
        for comic_id in identifiers:
            if await self.enqueue(int(comic_id)):
                count += 1
        return count

    async def join(self) -> None:
        """Wait until all jobs queued at this point have been marked complete."""
        await self._queue.join()

    async def _worker(self, worker_index: int) -> None:
        """Continuously process queue items until a shutdown sentinel is received."""
        del worker_index
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                refresh = await self._begin(item)
                try:
                    await self._process(item.comic_id, refresh)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A persistence adapter or malformed single job must not
                    # permanently shrink the configured worker pool.
                    continue
            finally:
                if item is not None:
                    await self._finish(item.comic_id)
                self._queue.task_done()

    async def _begin(self, item: _WorkItem) -> bool:
        """Move a dequeued ID to active state and read any coalesced refresh flag."""
        async with self._state_lock:
            refresh = self._queued.pop(item.comic_id, item.refresh)
            self._active.add(item.comic_id)
            return refresh

    async def _finish(self, comic_id: int) -> None:
        """Release active state and schedule one request that arrived mid-flight."""
        async with self._state_lock:
            self._active.discard(comic_id)
            deferred_refresh = self._after_active.pop(comic_id, None)
        if deferred_refresh is not None:
            await self.enqueue(comic_id, refresh=deferred_refresh)

    async def _process(self, comic_id: int, refresh: bool) -> None:
        """Fetch, persist and cover-cache one comic, preserving old data on failure."""
        record = await _call(self._repository, "get_comic", comic_id)
        if record is None:
            return
        has_successful_data = _record_has_successful_data(record)
        try:
            status = "refreshing" if refresh and has_successful_data else "loading"
            await _call(self._repository, "set_status", comic_id, status, None)
            metadata = await self._fetch_with_retries(comic_id, refresh)
            cover_created = False
            if self._covers is not None and metadata.cover_url:
                result = await self._covers.ensure_cover(
                    comic_id, metadata.cover_url, force=refresh
                )
                cover_created = result.created
            # A cover retrieval error must leave the prior metadata snapshot intact.
            # The CoverStore's atomic replacement independently retains the prior file.
            await _call(self._repository, "save_metadata", comic_id, metadata)
            if cover_created:
                await _call(self._repository, "bump_cover_version", comic_id)
            await _call(self._repository, "set_status", comic_id, "ready", None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = _error_message(exc)
            # Metadata is intentionally never overwritten on the error path: a refresh
            # therefore retains the last successful snapshot while exposing its error.
            try:
                await _call(self._repository, "set_status", comic_id, "error", message)
            except KeyError:
                # Deletion is allowed while a job is in flight. The disappeared row
                # is already in its desired final state and needs no error update.
                return

    async def _fetch_with_retries(self, comic_id: int, refresh: bool) -> ComicMetadata:
        """Fetch with a 20-second per-attempt timeout and bounded exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                async with asyncio.timeout(self._settings.timeout_seconds):
                    return await self._upstream.fetch_comic(comic_id, force_refresh=refresh)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self._settings.max_attempts:
                    await self._sleep(0.25 * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error


async def _call(target: Any, method_name: str, *args: Any) -> Any:
    """Call a sync or async repository method by name with a clear contract error."""
    method = getattr(target, method_name, None)
    if method is None:
        raise RuntimeError(f"Task repository must implement {method_name}()")
    result = method(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _record_has_successful_data(record: Any) -> bool:
    """Determine whether a persisted object or mapping contains a prior metadata snapshot."""
    if isinstance(record, dict):
        return bool(record.get("title") or record.get("refreshed_at"))
    return bool(getattr(record, "title", None) or getattr(record, "refreshed_at", None))


def _error_message(error: Exception) -> str:
    """Produce a bounded error message that remains useful in the list UI."""
    message = str(error).strip() or error.__class__.__name__
    return message[:500]
