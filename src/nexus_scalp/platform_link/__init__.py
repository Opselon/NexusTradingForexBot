"""Platform link: GitHub star verification via the trusted NSE-Platform.

Public surface:
- :class:`PlatformLinkService` — connect flow, status, verification, gate
- :class:`PlatformAuthorizationRequiredError` — typed denial (carries LinkStatus)
- :class:`LinkStatus`, :class:`VerificationState` — state machine
- :func:`get_platform_link_service` — process-wide accessor
"""

from __future__ import annotations

from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.service import (
    LinkStatus,
    PlatformAuthorizationRequiredError,
    PlatformLinkService,
    get_platform_link_service,
    reset_platform_link_service,
)
from nexus_scalp.platform_link.states import VerificationState

__all__ = [
    "LinkStatus",
    "PlatformAuthorizationRequiredError",
    "PlatformLinkConfig",
    "PlatformLinkService",
    "VerificationState",
    "get_platform_link_service",
    "reset_platform_link_service",
]
