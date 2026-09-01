"""API v1 wiring into the existing FastAPI app (CHG-0043, TASK-API-PLATFORM).

WHERE/WHY: single, minimal integration point with the existing web server.
``register_api_v1(app)`` is called ONCE at the END of ``web.server.create_app``
(one line there). It:

1. mounts all v1 routers under /api/v1,
2. registers the v1 exception handlers (path-guarded; legacy routes unaffected),
3. keeps the FULL route tree additive - zero existing routes touched.

Also provides ``create_v1_app()`` (standalone FastAPI app with ONLY /api/v1)
used by the developer CLI and tests so the contract surface is testable
without the dashboard server.

USED BY: web/server.py (one call), cli/api_commands.py, tests/unit/test_api_v1_*.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from nexus_scalp.api.v1.errors import register_v1_exception_handlers

API_V1_PREFIX = "/api/v1"


def _include_routers(app: FastAPI) -> None:
    from nexus_scalp.api.v1 import gateway, market_signals, positions_model, stores, system

    app.include_router(system.router)
    app.include_router(market_signals.router)
    app.include_router(positions_model.router)
    app.include_router(stores.router)
    app.include_router(gateway.router)


def register_api_v1(app: FastAPI) -> None:
    """Mounts the versioned read-only API platform on the EXISTING app."""
    register_v1_exception_handlers(app)
    _include_routers(app)


def create_v1_app() -> FastAPI:
    """Standalone app exposing ONLY /api/v1 (CLI + tests + OpenAPI snapshot).

    Shares the same routers/handlers as the dashboard-mounted surface, so the
    contract is identical wherever it runs.
    """
    app = FastAPI(
        title="Nexus Scalp Engine API",
        description=(
            "Versioned read-only API platform (v1) for the Nexus Scalp Engine: "
            "system/runtime, market, signals/decisions, positions/execution, "
            "model/features, research, shadow, incidents, observability, database."
        ),
        version="1.0.0",
    )
    register_api_v1(app)
    return app


def _iter_effective_routes(routes: Any) -> Any:
    """Yields leaf routes, flattening _IncludedRouter wrappers (FastAPI 0.141+)."""
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            yield from _iter_effective_routes(original.routes)
        else:
            yield r


def v1_route_count(app: Any) -> int:
    """Number of distinct v1 API operations (used by contract checks)."""
    count = 0
    for r in _iter_effective_routes(getattr(app, "router", app).routes):
        path = getattr(r, "path", "") or ""
        if not path.startswith(API_V1_PREFIX):
            continue
        methods = getattr(r, "methods", None) or set()
        if methods and not methods.issubset({"HEAD", "OPTIONS"}):
            count += 1
    return count
