"""Password checks, revocable signed sessions, and login rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .dependencies import get_db_session
from .models import AuthSession

_SESSION_SALT = b"favlist.session.v2"


@dataclass(frozen=True, slots=True)
class SessionClaims:
    """Validated identity and opaque identifier carried by a signed cookie."""

    username: str
    issued_at: int
    session_id: str


def _b64encode(value: bytes) -> str:
    """Encode bytes into URL-safe base64 without nonessential padding."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    """Decode an unpadded URL-safe base64 string."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: bytes, session_secret: str) -> bytes:
    """Calculate the versioned HMAC signature for a session payload."""
    return hmac.new(session_secret.encode("utf-8"), _SESSION_SALT + payload, hashlib.sha256).digest()


def create_session_token(
    settings: Settings,
    username: str,
    now: int | None = None,
    session_id: str | None = None,
) -> str:
    """Create a signed token containing an unpredictable server-session identifier."""
    issued_at = int(time.time() if now is None else now)
    opaque_id = session_id or secrets.token_urlsafe(32)
    payload = json.dumps(
        {"u": username, "iat": issued_at, "jti": opaque_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{_b64encode(payload)}.{_b64encode(_sign(payload, settings.session_secret))}"


def parse_session_token(
    settings: Settings,
    token: str,
    now: int | None = None,
) -> SessionClaims | None:
    """Return claims only when a token's signature, identity, and age are valid."""
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _b64decode(encoded_payload)
        received_signature = _b64decode(encoded_signature)
        expected_signature = _sign(payload, settings.session_secret)
        decoded: dict[str, Any] = json.loads(payload)
        username = decoded["u"]
        issued_at = decoded["iat"]
        session_id = decoded["jti"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(username, str)
        or not isinstance(issued_at, int)
        or not isinstance(session_id, str)
        or len(session_id) < 32
    ):
        return None
    current_time = int(time.time() if now is None else now)
    if issued_at > current_time or current_time - issued_at > settings.session_max_age_seconds:
        return None
    if not hmac.compare_digest(received_signature, expected_signature):
        return None
    if not hmac.compare_digest(username, settings.admin_username):
        return None
    return SessionClaims(username=username, issued_at=issued_at, session_id=session_id)


def verify_session_token(settings: Settings, token: str, now: int | None = None) -> str | None:
    """Return a valid signed token's username for low-level compatibility checks."""
    claims = parse_session_token(settings, token, now)
    return claims.username if claims is not None else None


def hash_session_id(session_id: str) -> str:
    """Hash an opaque session identifier before database persistence or lookup."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def new_session(
    settings: Settings,
    username: str,
    now: int | None = None,
) -> tuple[str, AuthSession]:
    """Create a cookie token and its hashed server-side persistence record."""
    issued_at = int(time.time() if now is None else now)
    session_id = secrets.token_urlsafe(32)
    token = create_session_token(settings, username, issued_at, session_id)
    record = AuthSession(
        token_hash=hash_session_id(session_id),
        username=username,
        expires_at=issued_at + settings.session_max_age_seconds,
    )
    return token, record


async def cleanup_expired_sessions(session: AsyncSession, now: int | None = None) -> int:
    """Delete expired session rows and return the affected row count."""
    current_time = int(time.time() if now is None else now)
    result = await session.execute(delete(AuthSession).where(AuthSession.expires_at < current_time))
    await session.commit()
    return int(result.rowcount or 0)


def verify_admin_credentials(settings: Settings, username: str, password: str) -> bool:
    """Verify credentials with Argon2 while avoiding username timing leaks."""
    if not hmac.compare_digest(username, settings.admin_username):
        return False
    if not settings.admin_password_hash:
        return False
    try:
        return PasswordHasher().verify(settings.admin_password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


class LoginRateLimiter:
    """A thread-safe fixed-window limiter for failed login attempts by IP address."""

    def __init__(
        self,
        attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the limiter with an injectable monotonic clock for tests."""
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip_address: str, current_time: float) -> deque[float]:
        """Discard failures outside the configured window and return the remaining queue."""
        failures = self._failures[ip_address]
        cutoff = current_time - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def is_allowed(self, ip_address: str) -> bool:
        """Report whether an IP has fewer than the maximum recent failures."""
        with self._lock:
            return len(self._prune(ip_address, self._clock())) < self.attempts

    def register_failure(self, ip_address: str) -> None:
        """Record one failed login attempt for the supplied client IP address."""
        with self._lock:
            self._prune(ip_address, self._clock()).append(self._clock())

    def clear(self, ip_address: str) -> None:
        """Clear a client's failures after a successful login."""
        with self._lock:
            self._failures.pop(ip_address, None)


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Attach the signed, HttpOnly, SameSite session cookie to a response."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Expire the authentication cookie using the same security attributes."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> str:
    """Require a signed cookie backed by a live, unrevoked database session."""
    token = request.cookies.get(settings.session_cookie_name)
    claims = parse_session_token(settings, token) if token else None
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    record = await session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_id(claims.session_id))
    )
    current_time = int(time.time())
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at < current_time
        or not hmac.compare_digest(record.username, claims.username)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return claims.username
