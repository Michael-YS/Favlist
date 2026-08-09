"""Secure HTTPS retrieval and atomic WebP storage for Favlist covers."""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import importlib
import io
import ipaddress
import os
import socket
import ssl
import tempfile
import time
import warnings
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit


CoverDownloader = Callable[[str], Awaitable[bytes]]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _default_cover_hosts() -> tuple[str, ...]:
    """Read the exact image-host allowlist from the pinned upstream package."""
    try:
        upstream_package = importlib.import_module("jmcomic")
        configured_hosts = upstream_package.JmModuleConfig.DOMAIN_IMAGE_LIST
    except (AttributeError, ImportError) as exc:
        raise RuntimeError(
            "jmcomic==2.7.3 must expose JmModuleConfig.DOMAIN_IMAGE_LIST"
        ) from exc
    hosts = tuple(
        dict.fromkeys(
            str(item).strip().lower().rstrip(".")
            for item in configured_hosts
            if str(item).strip()
        )
    )
    if not hosts:
        raise RuntimeError("jmcomic image-host allowlist must not be empty")
    return hosts


def _hostname_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Match exact hosts, permitting subdomains only for an explicit ``*.`` entry."""
    for raw_pattern in allowed_hosts:
        pattern = raw_pattern.strip().lower().rstrip(".")
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if suffix and hostname.endswith(f".{suffix}"):
                return True
        elif hostname == pattern:
            return True
    return False


def _positive_int(value: str, name: str) -> int:
    """Parse a positive integer cover-security setting."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _positive_float(value: str, name: str) -> float:
    """Parse a positive floating-point cover-security setting."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _csv(value: str) -> tuple[str, ...]:
    """Normalize a comma-separated environment setting."""
    return tuple(item.strip().lower().rstrip(".") for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class CoverSecuritySettings:
    """Bound cover network and image work to an explicit upstream host allowlist."""

    allowed_hosts: tuple[str, ...] = field(default_factory=_default_cover_hosts)
    max_bytes: int = 8 * 1024 * 1024
    total_timeout_seconds: float = 20.0
    network_timeout_seconds: float = 5.0
    max_redirects: int = 3
    max_pixels: int = 40_000_000
    max_width: int = 12_000
    max_height: int = 12_000
    max_frames: int = 1
    allowed_formats: tuple[str, ...] = ("JPEG", "PNG", "WEBP")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "CoverSecuritySettings":
        """Build cover limits from environment variables with safe defaults."""
        values = os.environ if environ is None else environ
        formats = tuple(item.upper() for item in _csv(values.get("COVER_ALLOWED_FORMATS", "JPEG,PNG,WEBP")))
        if not formats:
            raise ValueError("COVER_ALLOWED_FORMATS must not be empty")
        configured_hosts = _csv(values.get("COVER_ALLOWED_HOSTS", ""))
        return cls(
            allowed_hosts=configured_hosts or _default_cover_hosts(),
            max_bytes=_positive_int(values.get("COVER_MAX_BYTES", str(8 * 1024 * 1024)), "COVER_MAX_BYTES"),
            total_timeout_seconds=_positive_float(values.get("COVER_TOTAL_TIMEOUT_SECONDS", "20"), "COVER_TOTAL_TIMEOUT_SECONDS"),
            network_timeout_seconds=_positive_float(values.get("COVER_NETWORK_TIMEOUT_SECONDS", "5"), "COVER_NETWORK_TIMEOUT_SECONDS"),
            max_redirects=_positive_int(values.get("COVER_MAX_REDIRECTS", "3"), "COVER_MAX_REDIRECTS"),
            max_pixels=_positive_int(values.get("COVER_MAX_PIXELS", "40000000"), "COVER_MAX_PIXELS"),
            max_width=_positive_int(values.get("COVER_MAX_WIDTH", "12000"), "COVER_MAX_WIDTH"),
            max_height=_positive_int(values.get("COVER_MAX_HEIGHT", "12000"), "COVER_MAX_HEIGHT"),
            max_frames=_positive_int(values.get("COVER_MAX_FRAMES", "1"), "COVER_MAX_FRAMES"),
            allowed_formats=formats,
        )


@dataclass(frozen=True)
class CoverResult:
    """Describe the cover file delivered or created by a storage operation."""

    path: Path
    created: bool
    etag: str


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect TLS to one validated IP while authenticating the original host."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        """Remember the validated address selected for this single request."""
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        """Open the socket only to the pre-resolved address to prevent DNS rebinding."""
        raw_socket = socket.create_connection(
            (self._validated_address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class CoverStore:
    """Persist validated covers as WebP files without evicting live covers."""

    def __init__(
        self,
        directory: Path | str = "/data/covers",
        *,
        downloader: CoverDownloader | None = None,
        security: CoverSecuritySettings | None = None,
    ) -> None:
        """Create a cover store with injectable retrieval for deterministic tests."""
        self.directory = Path(directory)
        self.security = security or CoverSecuritySettings.from_env()
        self._downloader = downloader or self._download
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    def path_for(self, comic_id: int) -> Path:
        """Return the canonical WebP path after validating a positive identifier."""
        if comic_id <= 0:
            raise ValueError("comic_id must be positive")
        return self.directory / f"{comic_id}.webp"

    def exists(self, comic_id: int) -> bool:
        """Return whether the persisted cover currently exists."""
        return self.path_for(comic_id).is_file()

    async def ensure_cover(self, comic_id: int, source_url: str, *, force: bool = False) -> CoverResult:
        """Securely retrieve and atomically convert a cover within one deadline."""
        if not source_url:
            raise ValueError("source_url is required to restore a missing cover")
        lock = await self._lock_for(comic_id)
        async with lock:
            path = self.path_for(comic_id)
            if path.is_file() and not force:
                return CoverResult(path, False, self.etag_for(path))
            deadline = time.monotonic() + self.security.total_timeout_seconds
            async with asyncio.timeout(self.security.total_timeout_seconds):
                raw_image = await self._downloader(source_url)
                await asyncio.to_thread(self._write_webp_atomic, path, raw_image, deadline)
            return CoverResult(path, True, self.etag_for(path))

    async def delete(self, comic_id: int) -> bool:
        """Delete a comic's cover, returning whether a file was actually removed."""
        lock = await self._lock_for(comic_id)
        async with lock:
            path = self.path_for(comic_id)
            try:
                await asyncio.to_thread(path.unlink)
            except FileNotFoundError:
                return False
            return True

    async def cleanup_orphans(self, active_ids: Iterable[int]) -> list[Path]:
        """Delete WebP files whose numeric identifiers are absent from ``active_ids``."""
        active = {int(comic_id) for comic_id in active_ids}
        return await asyncio.to_thread(self._cleanup_orphans_sync, active)

    def etag_for(self, path: Path) -> str:
        """Return a strong content ETag suitable for private cache validation."""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f'"{digest}"'

    async def _lock_for(self, comic_id: int) -> asyncio.Lock:
        """Return the per-comic write lock, creating it once safely."""
        async with self._locks_guard:
            return self._locks.setdefault(comic_id, asyncio.Lock())

    async def _download(self, source_url: str) -> bytes:
        """Retrieve an HTTPS image on a worker thread under bounded I/O."""
        return await asyncio.to_thread(self._download_sync, source_url)

    def _download_sync(self, source_url: str) -> bytes:
        """Follow a bounded redirect chain, validating and pinning every destination."""
        deadline = time.monotonic() + self.security.total_timeout_seconds
        current_url = source_url
        for redirect_count in range(self.security.max_redirects + 1):
            parsed, addresses = self._validate_destination(current_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("cover download exceeded its total timeout")
            timeout = min(self.security.network_timeout_seconds, remaining)
            port = parsed.port or 443
            connection = _PinnedHTTPSConnection(parsed.hostname or "", port, addresses[0], timeout)
            try:
                target = parsed.path or "/"
                if parsed.query:
                    target += f"?{parsed.query}"
                connection.request(
                    "GET",
                    target,
                    headers={"Accept": "image/*", "Host": parsed.netloc, "User-Agent": "Favlist/1"},
                )
                response = connection.getresponse()
                if response.status in _REDIRECT_STATUSES:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("cover redirect is missing Location")
                    if redirect_count >= self.security.max_redirects:
                        raise ValueError("cover redirect limit exceeded")
                    current_url = urljoin(current_url, location)
                    continue
                if not 200 <= response.status < 300:
                    raise ValueError(f"cover server returned HTTP {response.status}")
                return self._read_response(response, connection, deadline)
            finally:
                connection.close()
        raise ValueError("cover redirect limit exceeded")

    def _validate_destination(self, source_url: str):
        """Require HTTPS, an allowed hostname, and exclusively public DNS results."""
        parsed = urlsplit(source_url)
        if parsed.scheme.lower() != "https":
            raise ValueError("cover URL must use HTTPS")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("cover URL must contain a hostname without credentials")
        hostname = parsed.hostname.lower().rstrip(".")
        if not _hostname_allowed(hostname, self.security.allowed_hosts):
            raise ValueError("cover hostname is not allowed")
        try:
            records = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("cover hostname could not be resolved") from exc
        addresses = tuple(dict.fromkeys(record[4][0] for record in records))
        if not addresses:
            raise ValueError("cover hostname resolved to no addresses")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or any(
                (ip.is_loopback, ip.is_private, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified)
            ):
                raise ValueError("cover hostname resolves to a non-public address")
        return parsed, addresses

    def _read_response(
        self,
        response: http.client.HTTPResponse,
        connection: _PinnedHTTPSConnection,
        deadline: float,
    ) -> bytes:
        """Read a response incrementally with declared and hard byte limits."""
        length_header = response.getheader("Content-Length")
        if length_header is not None:
            try:
                declared_length = int(length_header)
            except ValueError as exc:
                raise ValueError("cover response has invalid Content-Length") from exc
            if declared_length < 0 or declared_length > self.security.max_bytes:
                raise ValueError("cover response exceeds the byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("cover download exceeded its total timeout")
            if connection.sock is not None:
                connection.sock.settimeout(min(self.security.network_timeout_seconds, remaining))
            chunk = response.read(min(64 * 1024, self.security.max_bytes - total + 1))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.security.max_bytes:
                raise ValueError("cover response exceeds the byte limit")
            chunks.append(chunk)

    def _write_webp_atomic(self, target: Path, raw_image: bytes, deadline: float | None = None) -> None:
        """Validate image complexity, convert to WebP, and atomically replace target."""
        if len(raw_image) > self.security.max_bytes:
            raise ValueError("cover image exceeds the byte limit")
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow is required for WebP cover conversion") from exc
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw_image)) as probe:
                self._validate_image(probe)
                probe.verify()
            with Image.open(io.BytesIO(raw_image)) as image:
                self._validate_image(image)
                converted = image.convert("RGB")
                converted.load()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                converted.save(temporary_file, format="WEBP", quality=88, method=6)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("cover conversion exceeded its total timeout")
            os.replace(temporary_name, target)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _validate_image(self, image) -> None:
        """Reject unsupported formats, dimensions, pixel counts, and animation."""
        if image.format not in self.security.allowed_formats:
            raise ValueError("cover image format is not allowed")
        width, height = image.size
        if width > self.security.max_width or height > self.security.max_height:
            raise ValueError("cover image dimensions exceed the limit")
        if width * height > self.security.max_pixels:
            raise ValueError("cover image pixel count exceeds the limit")
        if int(getattr(image, "n_frames", 1)) > self.security.max_frames:
            raise ValueError("cover image frame count exceeds the limit")

    def _cleanup_orphans_sync(self, active_ids: set[int]) -> list[Path]:
        """Synchronously delete orphan WebP files, ignoring unrelated filenames."""
        if not self.directory.exists():
            return []
        removed: list[Path] = []
        for path in self.directory.glob("*.webp"):
            try:
                comic_id = int(path.stem)
            except ValueError:
                continue
            if comic_id not in active_ids:
                path.unlink(missing_ok=True)
                removed.append(path)
        return removed
