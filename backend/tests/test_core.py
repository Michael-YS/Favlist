"""Unit tests for core tag rules, signed sessions, rate limits, and SQL filtering."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from sqlalchemy import select
from starlette.requests import Request

from app.auth import LoginRateLimiter, create_session_token, verify_admin_credentials, verify_session_token
from app.config import Settings
from app.database import ComicRepository, create_database_engine, create_session_factory, initialize_database
from app.jm_client import ComicMetadata, UpstreamTag
from app.models import Comic, ComicStatus
from app.schemas import TagRead
from app.security import client_ip
from app.tagging import TagPreferences, normalize_tag, normalize_tag_filter, sort_tag_reads


def _settings() -> Settings:
    """Create deterministic test settings with a real Argon2 password hash."""
    return Settings(
        admin_username="admin",
        admin_password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="a" * 32,
        session_max_age_seconds=60,
    )


def test_tag_normalization_conflict_and_priority() -> None:
    """Normalize tag spelling, reject cross-group conflicts, and preserve priority."""
    assert normalize_tag("  \uff21\u3000\uff22  ") == "A B"
    try:
        TagPreferences.from_lists(["alpha"], [" alpha "])
    except ValueError as error:
        assert "both liked and disliked" in str(error)
    else:
        raise AssertionError("a cross-group tag conflict must fail")
    preferences = TagPreferences.from_lists(["liked"], ["disliked"])
    ordered = sort_tag_reads(
        [
            TagRead(name="normal", emphasis="normal", display_order=0),
            TagRead(name="liked", emphasis="liked", display_order=2),
            TagRead(name="disliked", emphasis="disliked", display_order=1),
        ],
        preferences,
    )
    assert [tag.name for tag in ordered] == ["disliked", "liked", "normal"]


def test_tag_filter_normalizes_and_uses_and_query() -> None:
    """Construct an AND-semantics tag predicate without duplicate query values."""
    assert normalize_tag_filter([" a ", "a", "b"]) == ("a", "b")
    from app.tagging import filter_comics_by_all_tags

    statement = filter_comics_by_all_tags(select(Comic), ["one", "two"])
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "HAVING count(DISTINCT tags.name) = 2" in compiled


def test_signed_session_and_password_verification() -> None:
    """Accept valid administrator credentials and reject altered or expired tokens."""
    settings = _settings()
    assert verify_admin_credentials(settings, "admin", "correct horse battery staple")
    assert not verify_admin_credentials(settings, "admin", "wrong")
    token = create_session_token(settings, "admin", now=100)
    assert verify_session_token(settings, token, now=101) == "admin"
    assert verify_session_token(settings, token + "x", now=101) is None
    assert verify_session_token(settings, token, now=161) is None


def test_login_rate_limit_resets_after_window_and_success() -> None:
    """Limit repeated failed attempts and make an explicit successful reset possible."""
    now = [0.0]
    limiter = LoginRateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.is_allowed("127.0.0.1")
    limiter.register_failure("127.0.0.1")
    limiter.register_failure("127.0.0.1")
    assert not limiter.is_allowed("127.0.0.1")
    limiter.clear("127.0.0.1")
    assert limiter.is_allowed("127.0.0.1")
    limiter.register_failure("127.0.0.1")
    now[0] = 11.0
    assert limiter.is_allowed("127.0.0.1")


def test_client_ip_uses_forwarded_chain_only_behind_trusted_proxy() -> None:
    """Ignore spoofed forwarding headers unless the direct peer is explicitly trusted."""
    def request(peer: str, forwarded: str) -> Request:
        """Build a minimal Starlette request with controlled peer and proxy headers."""
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"x-forwarded-for", forwarded.encode("ascii"))],
                "client": (peer, 1234),
                "server": ("testserver", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )

    settings = Settings(trusted_proxy_cidrs=("10.0.0.0/8", "127.0.0.1/32"))
    assert client_ip(request("203.0.113.9", "198.51.100.7"), settings) == "203.0.113.9"
    assert client_ip(request("10.0.0.2", "198.51.100.7, 10.0.0.1"), settings) == "198.51.100.7"
    assert client_ip(
        request("10.0.0.2", "203.0.113.123, 198.51.100.7"), settings
    ) == "198.51.100.7"
    assert client_ip(request("10.0.0.2", "malformed"), settings) == "10.0.0.2"


def test_repository_persists_dto_tags_and_and_filter() -> None:
    """Persist upstream DTOs then return only comics containing every requested tag."""
    async def exercise() -> None:
        """Run a complete repository round trip against a temporary SQLite database."""
        settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
        engine = create_database_engine(settings)
        await initialize_database(engine)
        factory = create_session_factory(engine)
        async with factory() as session:
            session.add_all(
                [
                    Comic(id=1, status=ComicStatus.PENDING),
                    Comic(id=2, status=ComicStatus.PENDING),
                ]
            )
            await session.commit()
            repository = ComicRepository(session)
            await repository.save_metadata(
                1,
                ComicMetadata(
                    comic_id=1,
                    title="first",
                    description=None,
                    author=None,
                    page_count=None,
                    published_at=None,
                    views=None,
                    likes=None,
                    comments=None,
                    tags=(UpstreamTag("alpha", 0), UpstreamTag("beta", 1)),
                    cover_url="https://example.test/one.jpg",
                ),
            )
            await repository.save_metadata(
                2,
                ComicMetadata(
                    comic_id=2,
                    title="second",
                    description=None,
                    author=None,
                    page_count=None,
                    published_at=None,
                    views=None,
                    likes=None,
                    comments=None,
                    tags=(UpstreamTag("alpha", 0),),
                    cover_url=None,
                ),
            )
            from app.tagging import filter_comics_by_all_tags

            rows = await session.scalars(filter_comics_by_all_tags(select(Comic), ["alpha", "beta"]))
            assert [comic.id for comic in rows] == [1]
            saved = await repository.get_comic(1)
            assert saved is not None
            assert saved.cover_url == "https://example.test/one.jpg"
            assert [link.tag.name for link in saved.tag_links] == ["alpha", "beta"]
        await engine.dispose()

    asyncio.run(exercise())
