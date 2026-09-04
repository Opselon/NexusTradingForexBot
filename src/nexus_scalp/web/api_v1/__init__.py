"""API Platform v1 package (CHG-0043 / TASK-API-PLATFORM).

Package marker: the canonical v1 routers live in these domain modules and are
mounted by ``web.api_v1_wiring.register_api_v1``. Spec of record:
docs/api/API_PLATFORM_V1.md. Importing the package itself must never raise.
"""

from __future__ import annotations

__all__ = [
    "common",
    "decisions",
    "errors",
    "incidents",
    "market",
    "positions",
    "research",
    "risk",
    "runtime",
    "shadow",
    "signals",
    "system",
]
