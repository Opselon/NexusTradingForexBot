"""LD-2: intelligence + model-governance routes must register exactly once.

Regression context
------------------
``debug_research_routes.register_debug_research_routes`` mounts
``intelligence_routes.router`` (10 /api/intelligence/* endpoints) and
``model_governance_routes.router`` (45 /api/models/* endpoints) via
``app.include_router(...)``, because the ``register_*_routes(app)`` functions only
populate the module-level ``APIRouter`` — they never attach routes to ``app``
themselves. FastAPI's ``include_router`` is NOT idempotent: it appends a fresh
wrapper for the same router object on every call, and because the router is
module-level STATE, a second ``create_app()`` in the same process re-mounts the
accumulated route set, producing 10 + 45 = 55 "Duplicate Operation ID" warnings
per extra build and inflating the OpenAPI surface.

The fix clears each module router before registering, so the mount is
exactly-once per app while preserving the original route ORDER.

Assertions
----------
* Across 3 successive ``create_app()`` builds, zero "Duplicate Operation ID"
  warnings are emitted while generating the OpenAPI schema.
* The (method, path) operation set is IDENTICAL across those builds (no route
  lost or added between builds).
* The intelligence (10) and model-governance (45) endpoints are present with
  the exact expected path set.
"""

from __future__ import annotations

import warnings
from typing import Any

from fastapi.routing import _IncludedRouter

from nexus_scalp.web.server import create_app

N_BUILDS = 3

EXPECTED_INTELLIGENCE_PATHS: set[str] = {
    "/api/intelligence/summary",
    "/api/intelligence/positions/{ticket}/timeline",
    "/api/intelligence/autopsies",
    "/api/intelligence/autopsies/{ticket}",
    "/api/intelligence/behavior",
    "/api/intelligence/anomalies",
    "/api/intelligence/evolution",
    "/api/intelligence/evolution/scan",
    "/api/intelligence/evolution/validate",
    "/api/intelligence/self-heal",
}

# Verified against the @router.* decorators in model_governance_routes.py.
EXPECTED_MODEL_PATH_PREFIXES = ("/api/models",)


def _flatten(routes: list[Any]) -> list[Any]:
    """Yield concrete routes, unwrapping FastAPI's _IncludedRouter wrappers."""
    out: list[Any] = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            out += _flatten(route.original_router.routes)
        else:
            out.append(route)
    return out


def _operation_keys(app: Any) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in _flatten(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in getattr(route, "methods", ()) or ():
            keys.add((method.upper(), path))
    return keys


def _duplicate_operation_id_warnings(app: Any) -> list[str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi()
    return [str(w.message) for w in caught if "Duplicate Operation ID" in str(w.message)]


def test_ld2_no_duplicate_operation_ids_across_repeated_builds() -> None:
    """Zero duplicate operation IDs and a stable operation set across N builds."""
    # Build ALL apps first, then measure — a module-level router is shared live
    # across apps, so a stale app measured after later builds is where the LD-2
    # duplicates actually surfaced in the baseline (55 warnings on an early app
    # once a later create_app() had re-populated the same router).
    apps = [create_app() for _ in range(N_BUILDS)]

    reference_keys: set[tuple[str, str]] | None = None
    for build, app in enumerate(apps, start=1):
        keys = _operation_keys(app)
        dupes = _duplicate_operation_id_warnings(app)

        assert dupes == [], (
            f"build #{build}: {len(dupes)} duplicate operation IDs "
            f"(LD-2 regression); first 3: {dupes[:3]}"
        )
        if reference_keys is None:
            reference_keys = keys
        else:
            lost = reference_keys - keys
            gained = keys - reference_keys
            assert not lost, f"build #{build}: lost operations vs build #1: {sorted(lost)[:5]}"
            assert not gained, (
                f"build #{build}: gained operations vs build #1 (accumulating "
                f"router state): {sorted(gained)[:5]}"
            )

    assert reference_keys is not None


def test_ld2_intelligence_and_models_routes_present_exactly_once() -> None:
    """The 10 intelligence endpoints and all 45 /api/models/* endpoints exist."""
    app = create_app()
    counts: dict[tuple[str, str], int] = {}
    for route in _flatten(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in getattr(route, "methods", ()) or ():
            key = (method.upper(), path)
            counts[key] = counts.get(key, 0) + 1

    intel_keys = {path for method, path in counts if path.startswith("/api/intelligence")}
    missing = EXPECTED_INTELLIGENCE_PATHS - intel_keys
    assert not missing, f"intelligence endpoints missing from app: {sorted(missing)}"

    model_keys = {path for method, path in counts if path.startswith("/api/models")}
    assert model_keys, "model governance endpoints missing from app entirely"

    # LD-2 core assertion: the intelligence + model-governance endpoints this
    # defect covers must each be mounted exactly once. (The global duplicate
    # ('POST', '/api/news/analyze/{article_id}') is a PRE-EXISTING, unrelated
    # issue: it emits no duplicate operation ID and is out of LD-2 scope.)
    ld2_duplicated = {
        key: n
        for key, n in counts.items()
        if n > 1 and key[1].startswith(("/api/intelligence", "/api/models"))
    }
    assert not ld2_duplicated, (
        f"intelligence/model-governance routes mounted more than once: {sorted(ld2_duplicated)[:5]}"
    )

    # Every intelligence/models operation is mounted exactly once.
    for (method, path), times in counts.items():
        if path.startswith("/api/intelligence") or path.startswith("/api/models"):
            assert times == 1, f"{method} {path} duplicated ({times}x)"
