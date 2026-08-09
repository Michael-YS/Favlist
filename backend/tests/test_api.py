"""Integration coverage for the session-protected HTTP API surface."""

from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.api.imports import parse_import_identifiers
from app.config import Settings
from app.jm_client import ComicMetadata
from app.main import create_app
from app.models import Comic, ComicStatus, ComicTag, Tag

_CSRF_HEADERS = {"X-Favlist-CSRF": "1"}


class StubUpstream:
    """Provide deterministic metadata without making network requests in API tests."""

    async def fetch_comic(self, comic_id: int, *, force_refresh: bool = False) -> ComicMetadata:
        """Return a minimal ready-to-persist metadata record for a requested ID."""
        del force_refresh
        return ComicMetadata(
            comic_id=comic_id,
            title=f"Comic {comic_id}",
            description=None,
            author="Test author",
            page_count=1,
            published_at=None,
            views=None,
            likes=None,
            comments=None,
            tags=(),
            cover_url=None,
        )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Return a started application backed by a unique temporary SQLite database."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        admin_username="admin",
        admin_password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="x" * 32,
    )
    with TestClient(create_app(settings=settings, upstream=StubUpstream())) as test_client:
        yield test_client


def _login(client: TestClient) -> None:
    """Authenticate the test client using the fixture's configured administrator."""
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
        headers=_CSRF_HEADERS,
    )
    assert response.status_code == 200


def test_import_parser_normalizes_supported_tokens() -> None:
    """Accept case-insensitive JM IDs and count malformed or zero identifiers."""
    identifiers, invalid = parse_import_identifiers("JM1, 002; jm003 bad 0 JM0")
    assert identifiers == [1, 2, 3]
    assert invalid == 3


def test_protected_routes_reject_anonymous_requests(client: TestClient) -> None:
    """Require an authenticated session before exposing comic metadata."""
    assert client.get("/api/comics").status_code == 401
    assert client.get("/api/tags").status_code == 401
    assert client.get("/api/export/ids").status_code == 401


def test_unsafe_routes_require_non_simple_csrf_header(client: TestClient) -> None:
    """Reject cross-site form-compatible login and authenticated mutation requests."""
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
        headers={"Origin": "https://attacker.example"},
    )
    assert login.status_code == 403
    _login(client)
    unsafe_requests = (
        ("POST", "/api/auth/logout", None),
        ("POST", "/api/import", {"text": "1"}),
        ("POST", "/api/comics/1/refresh", None),
        ("POST", "/api/comics/bulk-refresh", {"ids": [1]}),
        ("DELETE", "/api/comics", {"ids": [1]}),
    )
    for method, path, payload in unsafe_requests:
        response = client.request(method, path, json=payload)
        assert response.status_code == 403, (method, path, response.text)


def test_conflicting_tag_preferences_prevent_application_start(tmp_path: Path) -> None:
    """Reject a liked/disliked overlap before the database or workers are started."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}",
        admin_password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="x" * 32,
        liked_tags=("shared",),
        disliked_tags=(" shared ",),
    )
    with pytest.raises(ValueError, match="tags cannot be both liked and disliked: shared"):
        with TestClient(create_app(settings=settings, upstream=StubUpstream())):
            pass


def test_lifespan_stops_started_workers_when_recovery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release workers during a startup exception before the app begins serving."""
    class FailingRecoveryQueue:
        """Expose lifecycle flags while failing restart recovery deterministically."""

        started = False
        stopped = False

        def __init__(self, *_: object, **__: object) -> None:
            """Accept the production queue constructor contract without retaining inputs."""

        async def start(self) -> None:
            """Record that startup reached worker creation."""
            type(self).started = True

        async def recover_unfinished(self) -> int:
            """Simulate a storage failure after workers have started."""
            raise RuntimeError("recovery failed")

        async def stop(self) -> None:
            """Record that lifespan cleanup released the started queue."""
            type(self).stopped = True

    monkeypatch.setattr("app.main.ComicTaskQueue", FailingRecoveryQueue)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}",
        admin_password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="q" * 32,
    )
    with pytest.raises(RuntimeError, match="recovery failed"):
        with TestClient(create_app(settings=settings, upstream=StubUpstream())):
            pass
    assert FailingRecoveryQueue.started
    assert FailingRecoveryQueue.stopped


def test_delete_removes_orphan_tag_facets(client: TestClient) -> None:
    """Remove tag facets that become unreferenced in the deletion transaction."""
    _login(client)

    async def seed_tagged_comic() -> None:
        """Persist one comic with one exclusive tag through the app session factory."""
        async with client.app.state.session_factory() as session:
            session.add(
                Comic(
                    id=99,
                    status=ComicStatus.READY,
                    tag_links=[ComicTag(tag=Tag(name="exclusive"), position=0)],
                )
            )
            await session.commit()

    assert client.portal is not None
    client.portal.call(seed_tagged_comic)
    assert client.get("/api/tags").json() == [
        {"name": "exclusive", "emphasis": "normal", "count": 1, "display_order": None}
    ]
    assert client.request(
        "DELETE", "/api/comics", json={"ids": [99]}, headers=_CSRF_HEADERS
    ).status_code == 204
    assert client.get("/api/tags").json() == []


def test_import_list_refresh_delete_and_export(client: TestClient) -> None:
    """Exercise core list operations with duplicate accounting and session cookies."""
    _login(client)
    imported = client.post(
        "/api/import", json={"text": "JM1 2, jm1; bad"}, headers=_CSRF_HEADERS
    )
    assert imported.status_code == 200
    assert imported.json() == {"added": 2, "duplicate": 1, "invalid": 1}

    listing = client.get("/api/comics", params={"sort": "id_asc"})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [1, 2]
    assert listing.json()["total"] == 2

    assert client.post(
        "/api/comics/bulk-refresh", json={"ids": [1, 2]}, headers=_CSRF_HEADERS
    ).status_code == 204
    exported = client.get("/api/export/ids")
    assert exported.status_code == 200
    assert exported.text == "JM1\nJM2\n"
    assert 'filename="favlist-ids.txt"' in exported.headers["content-disposition"]
    assert client.request(
        "DELETE", "/api/comics", json={"ids": [1, 2]}, headers=_CSRF_HEADERS
    ).status_code == 204
    assert client.get("/api/comics").json()["total"] == 0


def test_logout_revokes_a_copied_cookie(client: TestClient) -> None:
    """Make a copied signed cookie unusable after its server-side session is revoked."""
    _login(client)
    cookie_name = client.app.state.settings.session_cookie_name
    copied_cookie = client.cookies.get(cookie_name)
    assert copied_cookie
    assert client.post("/api/auth/logout", headers=_CSRF_HEADERS).status_code == 204
    client.cookies.set(cookie_name, copied_cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_session_persists_across_application_restart(tmp_path: Path) -> None:
    """Accept an active database-backed session after recreating the app process."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}",
        admin_username="admin",
        admin_password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="r" * 32,
    )
    first_app = create_app(settings=settings, upstream=StubUpstream())
    with TestClient(first_app) as first:
        _login(first)
        cookie = first.cookies.get(settings.session_cookie_name)
        assert cookie
    second_app = create_app(settings=settings, upstream=StubUpstream())
    with TestClient(second_app) as second:
        second.cookies.set(settings.session_cookie_name, cookie)
        assert second.get("/api/auth/me").json() == {"username": "admin"}
