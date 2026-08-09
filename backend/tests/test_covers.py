"""Unit tests for atomic WebP cover writes, restoration, and orphan cleanup."""

from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.covers import CoverSecuritySettings, CoverStore


def png_bytes() -> bytes:
    """Produce a tiny valid source image for deterministic cover conversion tests."""
    image = Image.new("RGB", (2, 2), color=(30, 50, 70))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_bytes(image_format: str, *, frames: int = 1) -> bytes:
    """Create a deterministic image payload with an optional animation."""
    images = [Image.new("RGB", (2, 2), color=(index * 20, 50, 70)) for index in range(frames)]
    buffer = io.BytesIO()
    images[0].save(
        buffer,
        format=image_format,
        save_all=frames > 1,
        append_images=images[1:],
    )
    return buffer.getvalue()


class FakeResponse:
    """Expose the HTTPResponse subset consumed by secure cover retrieval."""

    def __init__(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        """Store a response status, headers, and streaming body."""
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = headers or {}

    def getheader(self, name: str) -> str | None:
        """Return a case-sensitive fake header value."""
        return self._headers.get(name)

    def read(self, amount: int) -> bytes:
        """Read at most ``amount`` bytes from the fake body."""
        return self._body.read(amount)


class FakeSocket:
    """Record timeout changes without opening a network socket."""

    def settimeout(self, timeout: float) -> None:
        """Accept a timeout assigned by the bounded reader."""
        del timeout


class FakeConnection:
    """Return one predetermined response from the pinned connection API."""

    responses: list[FakeResponse] = []

    def __init__(self, *_: object) -> None:
        """Attach a fake socket while ignoring connection coordinates."""
        self.sock = FakeSocket()

    def request(self, *_: object, **__: object) -> None:
        """Accept a request without network activity."""

    def getresponse(self) -> FakeResponse:
        """Pop the next response configured by the test."""
        return self.responses.pop(0)

    def close(self) -> None:
        """Close the no-op fake connection."""


class CoverStoreTests(unittest.IsolatedAsyncioTestCase):
    """Verify storage behavior without using an HTTP server."""

    async def test_missing_cover_is_downloaded_as_webp_once(self) -> None:
        """A missing cover is restored and later hits reuse its on-disk file."""
        calls = 0

        async def download(_: str) -> bytes:
            """Count downloads and return a valid PNG payload."""
            nonlocal calls
            calls += 1
            return png_bytes()

        with tempfile.TemporaryDirectory() as directory:
            store = CoverStore(directory, downloader=download)
            first = await store.ensure_cover(101, "https://covers.example/101")
            second = await store.ensure_cover(101, "https://covers.example/101")
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(calls, 1)
            self.assertEqual(first.path.suffix, ".webp")
            with Image.open(first.path) as image:
                self.assertEqual(image.format, "WEBP")

    async def test_valid_webp_source_is_accepted(self) -> None:
        """A valid single-frame WebP payload survives validation and conversion."""
        async def download(_: str) -> bytes:
            """Return a safe WebP source payload."""
            return image_bytes("WEBP")

        with tempfile.TemporaryDirectory() as directory:
            result = await CoverStore(directory, downloader=download).ensure_cover(
                102, "https://covers.example/102"
            )
            with Image.open(result.path) as image:
                self.assertEqual(image.format, "WEBP")

    async def test_file_and_http_urls_are_rejected(self) -> None:
        """Only HTTPS cover destinations may reach DNS or the network."""
        store = CoverStore(security=CoverSecuritySettings(allowed_hosts=("covers.example",)))
        for source_url in ("file:///etc/passwd", "http://example.com/cover.png"):
            with self.subTest(source_url=source_url), self.assertRaisesRegex(ValueError, "HTTPS"):
                store._download_sync(source_url)

    async def test_private_dns_result_is_rejected(self) -> None:
        """Every resolved address must be public after the hostname allowlist check."""
        store = CoverStore(security=CoverSecuritySettings(allowed_hosts=("covers.example",)))
        records = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch("app.covers.socket.getaddrinfo", return_value=records):
            with self.assertRaisesRegex(ValueError, "non-public"):
                store._download_sync("https://covers.example/a.png")

    async def test_redirect_to_private_address_is_revalidated(self) -> None:
        """A public first hop cannot redirect retrieval into a private network."""
        store = CoverStore(
            security=CoverSecuritySettings(
                allowed_hosts=("covers.example", "internal.example")
            )
        )
        FakeConnection.responses = [FakeResponse(302, headers={"Location": "https://internal.example/a"})]

        def resolve(host: str, port: int, *, type: int) -> list[tuple[object, ...]]:
            """Resolve the redirect target privately and the first hop publicly."""
            del type
            address = "10.0.0.8" if host == "internal.example" else "93.184.216.34"
            return [(2, 1, 6, "", (address, port))]

        with patch("app.covers.socket.getaddrinfo", side_effect=resolve), patch(
            "app.covers._PinnedHTTPSConnection", FakeConnection
        ):
            with self.assertRaisesRegex(ValueError, "non-public"):
                store._download_sync("https://covers.example/a.png")

    async def test_redirect_to_unlisted_public_host_is_revalidated(self) -> None:
        """Reject a redirect that leaves the exact upstream hostname allowlist."""
        store = CoverStore(
            security=CoverSecuritySettings(allowed_hosts=("covers.example",))
        )
        FakeConnection.responses = [
            FakeResponse(302, headers={"Location": "https://other.example/a"})
        ]
        public_record = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("app.covers.socket.getaddrinfo", return_value=public_record), patch(
            "app.covers._PinnedHTTPSConnection", FakeConnection
        ):
            with self.assertRaisesRegex(ValueError, "hostname is not allowed"):
                store._download_sync("https://covers.example/a.png")

    async def test_declared_and_streamed_oversized_responses_are_rejected(self) -> None:
        """Both Content-Length and actual bytes enforce the same hard cap."""
        settings = CoverSecuritySettings(allowed_hosts=("covers.example",), max_bytes=4)
        store = CoverStore(security=settings)
        public_record = [(2, 1, 6, "", ("93.184.216.34", 443))]
        responses = [
            FakeResponse(200, b"12345", {"Content-Length": "5"}),
            FakeResponse(200, b"12345"),
        ]
        with patch("app.covers.socket.getaddrinfo", return_value=public_record), patch(
            "app.covers._PinnedHTTPSConnection", FakeConnection
        ):
            for response in responses:
                FakeConnection.responses = [response]
                with self.assertRaisesRegex(ValueError, "byte limit"):
                    store._download_sync("https://covers.example/a.png")

    async def test_empty_or_unrelated_host_allowlist_is_fail_closed(self) -> None:
        """Never treat an empty allowlist as permission for arbitrary public hosts."""
        for allowed_hosts in ((), ("other.example",)):
            store = CoverStore(security=CoverSecuritySettings(allowed_hosts=allowed_hosts))
            with self.subTest(allowed_hosts=allowed_hosts), self.assertRaisesRegex(
                ValueError, "hostname is not allowed"
            ):
                store._download_sync("https://covers.example/a.png")

    async def test_plain_host_is_exact_and_wildcard_subdomains_are_explicit(self) -> None:
        """Do not silently expand an exact allowlist entry to every subdomain."""
        public_record = [(2, 1, 6, "", ("93.184.216.34", 443))]
        FakeConnection.responses = [FakeResponse(200, b"")]
        exact = CoverStore(security=CoverSecuritySettings(allowed_hosts=("example.com",)))
        with self.assertRaisesRegex(ValueError, "hostname is not allowed"):
            exact._download_sync("https://cdn.example.com/a.png")
        wildcard = CoverStore(security=CoverSecuritySettings(allowed_hosts=("*.example.com",)))
        with patch("app.covers.socket.getaddrinfo", return_value=public_record), patch(
            "app.covers._PinnedHTTPSConnection", FakeConnection
        ):
            self.assertEqual(wildcard._download_sync("https://cdn.example.com/a.png"), b"")

    async def test_default_allowlist_comes_from_pinned_upstream_package(self) -> None:
        """Use the pinned adapter's image domains when no override is configured."""
        settings = CoverSecuritySettings.from_env({})
        self.assertTrue(settings.allowed_hosts)
        self.assertIn("cdn-msp.jmapiproxy1.cc", settings.allowed_hosts)

    async def test_large_pixel_count_and_multiple_frames_are_rejected(self) -> None:
        """Decoded complexity limits apply before conversion allocates output."""
        async def large(_: str) -> bytes:
            """Return four pixels when only three are permitted."""
            return png_bytes()

        async def animated(_: str) -> bytes:
            """Return a two-frame GIF for frame-limit validation."""
            return image_bytes("GIF", frames=2)

        with tempfile.TemporaryDirectory() as directory:
            pixel_store = CoverStore(
                directory,
                downloader=large,
                security=CoverSecuritySettings(max_pixels=3),
            )
            with self.assertRaisesRegex(ValueError, "pixel count"):
                await pixel_store.ensure_cover(1, "https://covers.example/a")
            frame_store = CoverStore(
                directory,
                downloader=animated,
                security=CoverSecuritySettings(allowed_formats=("GIF",), max_frames=1),
            )
            with self.assertRaisesRegex(ValueError, "frame count"):
                await frame_store.ensure_cover(2, "https://covers.example/b")

    async def test_total_timeout_preserves_existing_cover(self) -> None:
        """A forced refresh timeout leaves the prior atomic file untouched."""
        async def slow(_: str) -> bytes:
            """Sleep beyond the configured operation deadline."""
            await asyncio.sleep(0.05)
            return png_bytes()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "9.webp"
            target.write_bytes(b"old-cover")
            store = CoverStore(
                directory,
                downloader=slow,
                security=CoverSecuritySettings(total_timeout_seconds=0.01),
            )
            with self.assertRaises(TimeoutError):
                await store.ensure_cover(9, "https://covers.example/9", force=True)
            self.assertEqual(target.read_bytes(), b"old-cover")

    async def test_cleanup_removes_only_orphan_numeric_webp_files(self) -> None:
        """Maintenance preserves active covers and ignores unrelated filesystem names."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.webp").write_bytes(b"active")
            (root / "2.webp").write_bytes(b"orphan")
            (root / "notes.webp").write_bytes(b"unrelated")
            store = CoverStore(root)
            removed = await store.cleanup_orphans([1])
            self.assertEqual(removed, [root / "2.webp"])
            self.assertTrue((root / "1.webp").exists())
            self.assertTrue((root / "notes.webp").exists())
