"""Request-boundary protections for proxy identity and CSRF enforcement."""

from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request, status

from .config import Settings

CSRF_HEADER_NAME = "X-Favlist-CSRF"
CSRF_HEADER_VALUE = "1"


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse a bare proxy address while tolerating surrounding whitespace."""
    return ipaddress.ip_address(value.strip())


def client_ip(request: Request, settings: Settings) -> str:
    """Resolve the client IP through only explicitly trusted direct proxy hops."""
    direct = request.client.host if request.client is not None else "unknown"
    try:
        direct_address = _parse_address(direct)
        trusted = tuple(ipaddress.ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs)
    except ValueError:
        return direct
    if not any(direct_address in network for network in trusted):
        return direct
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return direct
    try:
        chain = [_parse_address(value) for value in forwarded.split(",")]
    except ValueError:
        return direct
    for address in reversed(chain):
        if not any(address in network for network in trusted):
            return str(address)
    return str(chain[0]) if chain else direct


def require_csrf_header(request: Request) -> None:
    """Reject unsafe browser requests missing Favlist's non-simple CSRF header."""
    if request.headers.get(CSRF_HEADER_NAME) != CSRF_HEADER_VALUE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{CSRF_HEADER_NAME}: {CSRF_HEADER_VALUE} is required.",
        )
