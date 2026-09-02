"""API v1 wiring into the existing FastAPI app (CHG-0043, TASK-API-PLATFORM).

WHERE/WHY: single, minimal integration point with the existing web server.
``register_api_v1(app)`` is called ONCE at the END of ``web.server.create_app``
(one small block there). It:

1. registers the v1 exception handlers (path-guarded; legacy routes unaffected),
2. mounts all v1 domain routers under /api/v1,
3. keeps the FULL route tree additive - zero existing routes touched.

CANONICAL TREE (consolidation): the routers live in ``nexus_scalp.web.api_v1``
per the spec of record (docs/api/API_PLATFORM_V1.md §2/§7). The earlier
``nexus_scalp.api.v1`` tree (45 routes, superseded envelope) was removed in the
consolidation step; this wiring is the only mount point either tree ever had.

Domain file layout follows §7; tightly-coupled domains share cohesive modules:
- positions.py   → positions + execution + model + features
- incidents.py   → incidents + observability/audit + database + config
- system.py, runtime.py, market.py, signals.py, decisions.py, research.py,
  shadow.py → one domain each.

Also provides ``create_v1_app()`` (standalone FastAPI app with ONLY /api/v1)
used by the developer CLI, smoke/benchmark tools and tests so the contract
surface is testable without the dashboard server.

USED BY: web/server.py (one call), cli/api_commands.py, scripts/dev/api_*.py,
tests/unit/test_api_v1_*.py, tests/integration/test_api_v1_platform.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from nexus_scalp.web.api_v1.errors import register_v1_exception_handlers

API_V1_PREFIX = "/api/v1"


def _include_routers(app: FastAPI) -> None:
    from nexus_scalp.web.api_v1 import (
        decisions,
        incidents,
        market,
        positions,
        research,
        risk,
        runtime,
        shadow,
        signals,
        system,
    )

    for module in (
        system,
        runtime,
        market,
        signals,
        decisions,
        positions,
        risk,
        research,
        shadow,
        incidents,
    ):
        app.include_router(module.router)


def register_api_v1(app: FastAPI) -> None:
    """Mounts the versioned read-dominant API platform on the EXISTING app."""
    register_v1_exception_handlers(app)
    _include_routers(app)
    _ensure_correlation_middleware(app)


def _ensure_correlation_middleware(app: FastAPI) -> None:
    """Guarantees request_id plumbing on ANY app hosting /api/v1.

    The dashboard app already registers ``attach_request_id_middleware`` in
    create_app; the standalone v1 app does not. Middleware are appended once,
    keyed by a marker attribute, so double-registration is impossible.
    """
    if getattr(app.state, "v1_correlation_middleware", False):
        return
    from nexus_scalp.web.errors import attach_request_id_middleware

    @app.middleware("http")
    async def _v1_correlation(request: Any, call_next: Any):  # type: ignore[no-untyped-def]
        return await attach_request_id_middleware(request, call_next)

    app.state.v1_correlation_middleware = True


def create_v1_app() -> FastAPI:
    """Standalone app exposing ONLY /api/v1 (CLI + tests + OpenAPI snapshot).

    Shares the same routers/handlers as the dashboard-mounted surface, so the
    contract is identical wherever it runs.
    """
    app = FastAPI(
        title="Nexus Scalp Engine API",
        description=(
            "Versioned API platform (v1) for the Nexus Scalp Engine: system, "
            "runtime, market, signals, decisions, positions, risk, execution, "
            "model, features, research, shadow, observability, incidents, "
            "database, config."
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
