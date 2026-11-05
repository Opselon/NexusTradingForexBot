"""Configuration for the platform link subsystem.

All values come from environment with safe defaults. There is deliberately NO
"skip verification" switch: disabling the link only ever TIGHTENS behavior
(official downloads are refused), never loosens it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://subscribe.nexusverse.space"
DEFAULT_REPOSITORY = "Opselon/NexusTradingForexBot"


class ConfigError(ValueError):
    """Raised when the platform link configuration is invalid."""


@dataclass(frozen=True)
class PlatformLinkConfig:
    base_url: str = DEFAULT_BASE_URL
    repository: str = DEFAULT_REPOSITORY
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    poll_interval: float = 5.0
    await_timeout: float = 600.0
    # Offline grace: a cached STARRED_VERIFIED older than this never grants a
    # download when the platform cannot be reached (no indefinite caching).
    cache_ttl_hours: float = 24.0

    @property
    def owner(self) -> str:
        return self.repository.split("/", 1)[0] if "/" in self.repository else self.repository

    @property
    def repo(self) -> str:
        return self.repository.split("/", 1)[1] if "/" in self.repository else ""

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.repository}"

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            # Plain HTTP is only allowed with the explicit dev/test opt-in;
            # production always pins HTTPS (mirrors official_contract https_url).
            if os.environ.get("NEXUS_PLATFORM_ALLOW_HTTP") != "1":
                raise ConfigError("platform base_url must be HTTPS")
        if not parsed.netloc:
            raise ConfigError("platform base_url has no host")
        if "/" not in self.repository or not self.owner or not self.repo:
            raise ConfigError("repository must be 'owner/name'")

    @classmethod
    def from_env(cls) -> PlatformLinkConfig:
        cfg = cls(
            base_url=os.environ.get("NEXUS_PLATFORM_URL", DEFAULT_BASE_URL).rstrip("/"),
            repository=os.environ.get("NEXUS_PLATFORM_REPOSITORY", DEFAULT_REPOSITORY),
            connect_timeout=_env_float("NEXUS_PLATFORM_CONNECT_TIMEOUT", 5.0),
            read_timeout=_env_float("NEXUS_PLATFORM_READ_TIMEOUT", 15.0),
            poll_interval=max(1.0, _env_float("NEXUS_PLATFORM_POLL_INTERVAL", 5.0)),
            await_timeout=_env_float("NEXUS_PLATFORM_AWAIT_TIMEOUT", 600.0),
            cache_ttl_hours=max(0.0, _env_float("NEXUS_PLATFORM_CACHE_TTL_HOURS", 24.0)),
        )
        cfg.validate()
        return cfg


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
