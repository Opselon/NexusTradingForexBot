"""API v1 — SYSTEM domain (status/health/readiness/version/runtime/capabilities/
workers/refresh/diagnostics). Sources verified in docs/api/API_PLATFORM_V1.md §7."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request

from nexus_scalp.web.api_v1.common import (
    adapter_or_503,
    fail,
    get_engine,
    get_engine_adapter,
    get_versioner,
    ok,
    utc_now_iso,
)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _health_block() -> tuple[str | None, list[dict[str, Any]], list[str]]:
    """(verdict, checks, critical_failures) from the real HealthEngine."""
    try:
        from nexus_scalp.release.health import HealthEngine

        verdict, entries = HealthEngine().overall()
        checks = [e.to_dict() for e in entries]
        critical = [c["category"] for c in checks if c.get("verdict") == "FAIL"]
        return verdict, checks, critical
    except Exception:
        return None, [], []


@router.get("/status", summary="High-level operational status (health+version+mode+freshness)")
def system_status(request: Request) -> Any:
    verdict, checks, critical = _health_block()
    engine = get_engine(request)
    mode = None
    running = False
    freshness_overall = None
    if engine is not None:
        try:
            mode = engine.config.execution.mode.value
        except Exception:
            mode = None
        running = bool(getattr(engine, "_running", False))
        try:
            fresh = engine.compute_live_freshness()
            freshness_overall = fresh.get("overall") if isinstance(fresh, dict) else None
        except Exception:
            freshness_overall = None
    version: dict[str, Any] = {}
    try:
        from nexus_scalp.release.metadata import get_version_info

        version = get_version_info()
    except Exception:
        version = {}
    if verdict is None:
        # Health engine itself failed: do not manufacture a verdict.
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="health engine unavailable")
    return ok(
        request,
        {
            "health_verdict": verdict,
            "critical_failures": critical,
            "version": {
                "product": version.get("product"),
                "version": version.get("version"),
                "commit": version.get("commit"),
                "channel": version.get("channel"),
            },
            "runtime": {
                "engine_attached": engine is not None,
                "engine_running": running,
                "mode": mode,
                "freshness_overall": freshness_overall,
            },
            "checks_count": len(checks),
        },
    )


@router.get("/health", summary="Structured health state (HealthEngine contract)")
def system_health(request: Request) -> Any:
    verdict, checks, critical = _health_block()
    if verdict is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="health engine unavailable")
    return ok(
        request,
        {
            "verdict": verdict,
            "checks": checks,
            "critical_failures": critical,
        },
    )


@router.get("/readiness", summary="Readiness: required layers must PASS")
def system_readiness(request: Request) -> Any:
    verdict, checks, _critical = _health_block()
    if verdict is None:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="health engine unavailable")
    required = [
        c
        for c in checks
        if c.get("category")
        in {"SYSTEM", "RUNTIME", "CONFIGURATION", "DATABASE", "MODEL", "FEATURE_SCHEMA"}
    ]
    ready = verdict != "NOT READY" and not any(c.get("verdict") == "FAIL" for c in required)
    return ok(
        request,
        {
            "ready": ready,
            "verdict": verdict,
            "required_layers": required,
            "optional_layers": [
                c for c in checks if c.get("category") not in {r.get("category") for r in required}
            ],
        },
    )


@router.get("/version", summary="Version/build/revision identity")
def system_version(request: Request) -> Any:
    try:
        from nexus_scalp.release.metadata import get_version_info

        return ok(request, get_version_info())
    except Exception:
        return fail(request, "DEPENDENCY_UNAVAILABLE", message="build metadata unavailable")


@router.get("/runtime", summary="Runtime environment and mode information")
def system_runtime(request: Request) -> Any:
    engine = get_engine(request)
    fresh: dict[str, Any] | None = None
    if engine is not None:
        try:
            fresh = engine.compute_live_freshness()
        except Exception:
            fresh = None
    runtime: dict[str, Any] = {
        "engine_attached": engine is not None,
        "engine_running": bool(getattr(engine, "_running", False)) if engine else False,
        "warmup_state": getattr(engine, "warmup_state", None) if engine else None,
        "inference_enabled": bool(getattr(engine, "_inference_enabled", False))
        if engine
        else False,
        "freshness": fresh,
    }
    if engine is not None:
        try:
            runtime["mode"] = engine.config.execution.mode.value
            runtime["effective_mode"] = getattr(engine, "_runtime_mode", None)
        except Exception:
            runtime["mode"] = None
    return ok(request, runtime)


@router.get("/capabilities", summary="API platform discovery (domains + counts)")
def system_capabilities(request: Request) -> Any:
    """Built from the REAL mounted route table (no static fabrications)."""
    from nexus_scalp.web.api_v1_wiring import API_V1_PREFIX, _iter_effective_routes

    domains: dict[str, int] = {}
    endpoints: list[dict[str, Any]] = []
    try:
        for r in _iter_effective_routes(request.app.routes):
            path = getattr(r, "path", "") or ""
            if not path.startswith(API_V1_PREFIX):
                continue
            methods = sorted(getattr(r, "methods", None) or set() - {"HEAD", "OPTIONS"})
            if not methods:
                continue
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
                domain = parts[2]
                domains[domain] = domains.get(domain, 1)
                endpoints.append({"method": methods[0], "path": path, "domain": domain})
    except Exception:
        domains = {}
        endpoints = []
    return ok(
        request,
        {
            "api_version": "v1",
            "spec": "docs/api/API_PLATFORM_V1.md",
            "read_only": True,
            "domains": dict(sorted(domains.items())),
            "domain_count": len(domains),
            "endpoint_count": len(endpoints),
            "pagination": {
                "model": "page",
                "page_param": "page",
                "page_size_param": "page_size",
                "max_page_size": 200,
            },
            "generated_at": utc_now_iso(),
        },
    )


@router.get("/workers", summary="Worker status and lifecycle information")
def system_workers(request: Request) -> Any:
    engine = get_engine(request)
    if engine is None:
        return ok(request, {"workers": [], "engine_attached": False})
    workers: list[dict[str, Any]] = []
    for name in (
        "accounting_worker",
        "incident_worker",
        "hygiene_worker",
        "model_lifecycle_worker",
    ):
        w = getattr(engine, name, None)
        if w is None:
            workers.append({"name": name, "state": "NOT_ATTACHED"})
            continue
        state = getattr(w, "state", None)
        if state is None and hasattr(w, "_running"):
            state = "RUNNING" if w._running else "STOPPED"
        workers.append(
            {
                "name": name,
                "state": str(state) if state is not None else "UNKNOWN",
                "attached": True,
            }
        )
    return ok(request, {"workers": workers, "engine_attached": True})


@router.post("/refresh", summary="Safe refresh of runtime-observable state")
def system_refresh(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    adapter, _adapter_resp = adapter_or_503(request)
    if adapter is None:
        return _adapter_resp
    refreshed: dict[str, Any] = {"account": None, "symbol": None}
    try:
        snap = adapter.get_account_snapshot()
        refreshed["account"] = {"available": bool(getattr(snap, "available", False))}
    except Exception:
        refreshed["account"] = {"available": False}
    versioner = get_versioner(request)
    if versioner is not None:
        try:
            versioner.next_version()
        except Exception:
            pass
    meta = {"idempotency_key": idempotency_key} if idempotency_key else None
    return ok(request, {"refreshed": refreshed, "at": utc_now_iso()}, meta_extra=meta)


@router.post("/diagnostics/run", summary="Run the bounded observability selftest")
def system_diagnostics_run(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    try:
        from nexus_scalp.observability.logging import get_logger
        from nexus_scalp.observability.selftest import run_observability_selftest

        result = run_observability_selftest()
        request.app.state.v1_last_selftest = {
            "result": result,
            "at": utc_now_iso(),
        }
        meta = {"idempotency_key": idempotency_key} if idempotency_key else None
        return ok(request, result, meta_extra=meta)
    except Exception as exc:
        from nexus_scalp.observability.logging import get_logger

        return _fail_logged(request, get_logger(__name__), "/api/v1/system/diagnostics/run", exc)


def _fail_logged(request: Request, logger: Any, endpoint: str, exc: BaseException) -> Any:
    from nexus_scalp.web.api_v1.common import fail_internal_logged

    return fail_internal_logged(request, logger, endpoint, exc)


@router.get("/diagnostics", summary="Latest diagnostics (adapter + selftest + db facts)")
def system_diagnostics(request: Request) -> Any:
    # GET diagnostics is truthful without MT5: mt5=None, selftest + version still real.
    adapter = get_engine_adapter(request)
    diag: dict[str, Any] = {}
    if adapter is not None:
        try:
            diag["mt5"] = adapter.diagnostics_summary()
        except Exception:
            diag["mt5"] = None
    else:
        diag["mt5"] = None
    last = getattr(request.app.state, "v1_last_selftest", None)
    diag["last_selftest"] = last
    try:
        from nexus_scalp.release.metadata import get_version_info

        diag["version"] = get_version_info()
    except Exception:
        diag["version"] = None
    return ok(request, diag)
