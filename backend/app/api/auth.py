"""Session-based administrator authentication endpoints."""

import inspect
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    cleanup_expired_sessions,
    clear_session_cookie,
    get_current_user,
    hash_session_id,
    new_session,
    parse_session_token,
    set_session_cookie,
    verify_admin_credentials,
)
from app.config import Settings, get_settings
from app.dependencies import get_db_session
from app.models import AuthSession
from app.schemas import AuthUser, LoginRequest
from app.security import client_ip, require_csrf_header

router = APIRouter(prefix="/auth", tags=["auth"])


async def _call_limiter(limiter: Any, names: tuple[str, ...], ip: str) -> Any:
    """Invoke the first supported rate-limiter method, including async methods."""
    for name in names:
        method = getattr(limiter, name, None)
        if method is not None:
            result = method(ip)
            return await result if inspect.isawaitable(result) else result
    return None


@router.post("/login", response_model=AuthUser)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_csrf_header),
) -> AuthUser:
    """Verify administrator credentials and issue a signed session cookie."""
    limiter = getattr(request.app.state, "login_rate_limiter", None)
    source_ip = client_ip(request, settings)
    if limiter is not None:
        allowed = await _call_limiter(limiter, ("allow", "is_allowed", "check"), source_ip)
        if allowed is False:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please try again later.",
            )

    verified = verify_admin_credentials(settings, payload.username, payload.password)
    if inspect.isawaitable(verified):
        verified = await verified
    if not verified:
        if limiter is not None:
            await _call_limiter(limiter, ("register_failure", "record_failure", "failed"), source_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    if limiter is not None:
        await _call_limiter(limiter, ("clear", "reset", "record_success"), source_ip)
    await cleanup_expired_sessions(session)
    token, record = new_session(settings, payload.username)
    session.add(record)
    await session.commit()
    set_session_cookie(response, settings, token)
    return AuthUser(username=payload.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
    _: None = Depends(require_csrf_header),
) -> Response:
    """Revoke the current server-side session and clear its browser cookie."""
    token = request.cookies.get(settings.session_cookie_name)
    claims = parse_session_token(settings, token) if token else None
    if claims is not None:
        record = await session.get(AuthSession, hash_session_id(claims.session_id))
        if record is not None:
            record.revoked_at = int(time.time())
            await session.commit()
    clear_session_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthUser)
async def me(current_user: str = Depends(get_current_user)) -> AuthUser:
    """Return the administrator identity associated with the current session."""
    return AuthUser(username=current_user)
