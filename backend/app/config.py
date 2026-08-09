"""Environment and YAML-backed configuration for the single-user service."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


def _as_bool(value: str) -> bool:
    """Parse a conventional environment-variable boolean."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_positive_int(value: str, setting_name: str) -> int:
    """Parse a positive integer setting and raise a useful error if invalid."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{setting_name} must be an integer") from error
    if parsed <= 0:
        raise ValueError(f"{setting_name} must be greater than zero")
    return parsed


def _read_tag_config(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read the ordered liked and disliked tag lists from a YAML file."""
    if not path.exists():
        return (), ()
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required when TAG_CONFIG_PATH is configured") from error
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
    liked = ui.get("liked_tags", []) if isinstance(ui, dict) else []
    disliked = ui.get("disliked_tags", []) if isinstance(ui, dict) else []
    if not isinstance(liked, list) or not isinstance(disliked, list):
        raise ValueError("ui.liked_tags and ui.disliked_tags must be YAML lists")
    if not all(isinstance(item, str) for item in [*liked, *disliked]):
        raise ValueError("configured tag names must be strings")
    return tuple(liked), tuple(disliked)


def _split_tags(value: str) -> tuple[str, ...]:
    """Split a comma-separated tag environment variable while preserving order."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _split_csv(value: str) -> tuple[str, ...]:
    """Split a comma-separated setting into trimmed non-empty values."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """All runtime settings needed by the core application services.

    Tests may instantiate this class directly; production generally uses
    :meth:`from_env` so secrets remain outside the source tree.
    """

    database_url: str = "sqlite+aiosqlite:///./data/favlist.db"
    admin_username: str = "admin"
    admin_password_hash: str = ""
    session_secret: str = ""
    session_cookie_name: str = "favlist_session"
    session_max_age_seconds: int = 1_209_600
    cookie_secure: bool = False
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    trusted_proxy_cidrs: tuple[str, ...] = ()
    cors_allowed_origins: tuple[str, ...] = ()
    liked_tags: tuple[str, ...] = ()
    disliked_tags: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from an environment mapping and the optional YAML file."""
        values = os.environ if environ is None else environ
        config_path = Path(values.get("TAG_CONFIG_PATH", "config.yaml"))
        yaml_liked, yaml_disliked = _read_tag_config(config_path)
        liked = _split_tags(values["APP_LIKED_TAGS"]) if "APP_LIKED_TAGS" in values else yaml_liked
        disliked = (
            _split_tags(values["APP_DISLIKED_TAGS"])
            if "APP_DISLIKED_TAGS" in values
            else yaml_disliked
        )
        return cls(
            database_url=values.get("DATABASE_URL", "sqlite+aiosqlite:///./data/favlist.db"),
            admin_username=values.get("ADMIN_USERNAME", "admin"),
            admin_password_hash=values.get("ADMIN_PASSWORD_HASH", ""),
            session_secret=values.get("SESSION_SECRET", ""),
            session_cookie_name=values.get("SESSION_COOKIE_NAME", "favlist_session"),
            session_max_age_seconds=_as_positive_int(
                values.get("SESSION_MAX_AGE_SECONDS", "1209600"),
                "SESSION_MAX_AGE_SECONDS",
            ),
            cookie_secure=_as_bool(values.get("COOKIE_SECURE", "false")),
            login_rate_limit_attempts=_as_positive_int(
                values.get("LOGIN_RATE_LIMIT_ATTEMPTS", "5"),
                "LOGIN_RATE_LIMIT_ATTEMPTS",
            ),
            login_rate_limit_window_seconds=_as_positive_int(
                values.get(
                    "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
                    "300",
                ),
                "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
            ),
            trusted_proxy_cidrs=_split_csv(values.get("TRUSTED_PROXY_CIDRS", "")),
            cors_allowed_origins=_split_csv(values.get("CORS_ALLOWED_ORIGINS", "")),
            liked_tags=liked,
            disliked_tags=disliked,
        )

    def validate_secrets(self) -> None:
        """Ensure authentication and request-boundary settings are safe and valid."""
        if not self.admin_password_hash:
            raise ValueError("ADMIN_PASSWORD_HASH must be configured")
        if len(self.session_secret) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        for value in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as error:
                raise ValueError(f"invalid TRUSTED_PROXY_CIDRS entry: {value}") from error
        if "*" in self.cors_allowed_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS cannot contain '*' when credentials are enabled")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide production settings instance."""
    return Settings.from_env()
