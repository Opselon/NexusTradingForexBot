"""API v1 SYSTEM + RUNTIME + CAPABILITIES routers (CHG-0043).

Purpose-backed, truthful, read-only system surface:
  GET /api/v1/system/status        - high-level operational status snapshot
  GET /api/v1/system/health        - structured health (reuses HealthEngine verdicts)
  GET /api/v1/system/version       - version/build identity (release.metadata)
  GET /api/v1/system/runtime       - runtime environment + mode info
  GET /api/v1/system/capabilities  - machine-readable API/platform discovery
  GET /api/v1/system/workers       - worker/daemon lifecycle status (real threads)
  GET /api/v1/runtime/mode         - configured/effective execution mode

Every value is derived from REAL state (engine, HealthEngine, release metadata,
thread enumerations). No fabricated health, no fake mode strings.

USED BY: web/api_v1_wiring.py.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Request

from nexus_scalp.api.v1.common import API_VERSION, clamp_limit, envelope, utc_now_iso
from nexus_scalp.api.v1.deps import get_engine, subsystem_block

router = APIRouter(prefix="/api/v1", tags=["system"])


# ---------------------------------------------------------------------------
# Internals (shared probes, real data only)
# ---------------------------------------------------------------------------


def _mode_info(engine: Any) -> dict[str, Any]:
    """Configured + effective execution mode from the REAL engine config."""
    configured = None
    effective = None
    running = False
    if engine is not None:
        try:
            configured = engine.config.execution.mode.value
        except Exception:
            configured = None
        try:
            running = bool(engine._running)
        except Exception:
            running = False
        runtime_mode = getattr(engine, "_runtime_mode", None)
        if isinstance(runtime_mode, str) and runtime_mode:
            effective = runtime_mode
    return {
        "configured_mode": configured,
        "effective_mode": effective or configured,
        "engine_running": running,
    }


def _engine_identity(engine: Any) -> dict[str, Any]:
    """Model/runtime identity actually loaded in the engine (no fake bundle)."""
    if engine is None:
        return {"available": False, "reason": "engine not attached"}
    bundle = getattr(engine, "_bundle", None)
    out: dict[str, Any] = {"available": bundle is not None}
    if bundle is not None:
        for attr in ("model_id", "artifact_id", "schema_id", "manifest", "metadata"):
            value = getattr(bundle, attr, None)
            if value is not None and attr != "manifest":
                out[attr] = value
        manifest = getattr(bundle, "manifest", None)
        if manifest is not None:
            get = getattr(manifest, "model_dump", None) or getattr(manifest, "as_dict", None)
            out["manifest"] = get() if get else str(manifest)[:2000]
    out["inference_enabled"] = bool(getattr(engine, "_inference_enabled", False))
    out["warmup_state"] = getattr(engine, "warmup_state", None)
    return out


def _worker_inventory(engine: Any) -> list[dict[str, Any]]:
    """Real daemon/worker threads of THIS process (python threading enum)."""
    workers: list[dict[str, Any]] = []
    for t in threading.enumerate():
        if t is threading.main_thread() or t.daemon is not True:
            continue
        workers.append(
            {
                "name": t.name,
                "alive": t.is_alive(),
                "daemon": True,
                "kind": "thread",
            }
        )
    workers.sort(key=lambda w: w["name"])
    # Engine-attached background workers with real lifecycle state (bounded set).
    if engine is not None:
        for attr, label in (
            ("accounting_worker", "accounting"),
            ("incident_worker", "incident"),
            ("intelligence_worker", "intelligence"),
        ):
            w = getattr(engine, attr, None)
            if w is None:
                continue
            state = getattr(w, "state", None) or getattr(w, "_state", None)
            running = getattr(w, "is_running", None)
            entry: dict[str, Any] = {
                "name": label,
                "kind": "engine_worker",
                "state": state.value if hasattr(state, "value") else state,
                "alive": bool(running()) if callable(running) else None,
            }
            workers.append(entry)
    return workers


def _build_capabilities(request: Request) -> dict[str, Any]:
    """Machine-readable v1 discovery block. Kept in one place; snapshot/diff
    tooling consumes this through the OpenAPI + this endpoint."""
    app = request.app
    paths = sorted({r.path for r in app.routes if getattr(r, "path", "").startswith("/api/v1/")})
    domains = sorted({p.split("/")[3] for p in paths if len(p.split("/")) > 3})
    return {
        "api_version": API_VERSION,
        "read_only": True,
        "domains": domains,
        "endpoints": paths,
        "pagination": {
            "model": "page",
            "page_param": "page",
            "page_size_param": "page_size",
            "max_page_size": 200,
        },
        "error_contract": {
            "envelope": "error.{code,message,details,request_id,retryable}",
            "request_id_header": "X-Request-ID",
        },
        "time_semantics": "ISO-8601, UTC in transport",
        "generated_at": utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/system/status", summary="High-level operational status snapshot")
def system_status(request: Request) -> dict[str, Any]:
    engine = get_engine(request)
    mode = _mode_info(engine)
    engine_running = mode["engine_running"]
    # Overall verdict: truthful degradation (never OK when the engine is absent).
    if engine is None:
        overall = "UNAVAILABLE"
    elif engine_running:
        overall = "OPERATIONAL"
    else:
        overall = "STOPPED"
    data = {
        "overall": overall,
        "mode": mode,
        "engine_identity": _engine_identity(engine),
        "freshness": None,
        "probed_at": utc_now_iso(),
    }
    if engine is not None and hasattr(engine, "compute_live_freshness"):
        try:
            data["freshness"] = engine.compute_live_freshness()
        except Exception as exc:
            from nexus_scalp.observability.logging import get_logger
            from nexus_scalp.web.errors import log_web_error

            log_web_error(
                get_logger("nexus_scalp.api.v1"),
                "/api/v1/system/status",
                None,
                exc,
                resource="freshness",
            )
            data["freshness"] = {"status": "UNAVAILABLE"}
    return envelope(request, data)


@router.get("/system/health", summary="Structured health state (HealthEngine verdicts)")
def system_health(request: Request) -> dict[str, Any]:
    def _probe() -> dict[str, Any]:
        from nexus_scalp.release.health import HealthEngine

        verdict, entries = HealthEngine().overall()
        return {
            "verdict": verdict,
            "checks": [e.to_dict() for e in entries],
        }

    block = subsystem_block("health_engine", _probe)
    data: dict[str, Any] = {"health": block}
    checks = block.get("detail", {}).get("checks", []) if block.get("detail") else []
    fails = [c.get("category") for c in checks if c.get("verdict") == "FAIL"]
    data["critical_failures"] = fails
    data["http_semantics"] = "200 with verdict; FAIL entries are reported, not hidden"
    return envelope(request, data)


@router.get("/system/version", summary="Version / build / revision identity")
def system_version(request: Request) -> dict[str, Any]:
    from nexus_scalp.release.metadata import get_version_info

    return envelope(request, get_version_info())


@router.get("/system/runtime", summary="Runtime environment and mode information")
def system_runtime(request: Request) -> dict[str, Any]:
    import platform
    import sys

    engine = get_engine(request)
    data = {
        "python": sys.version.split(" ")[0],
        "platform": platform.platform(),
        "mode": _mode_info(engine),
        "model_identity": _engine_identity(engine),
        "probed_at": utc_now_iso(),
    }
    return envelope(request, data)


@router.get("/system/capabilities", summary="Machine-readable API/platform discovery")
def system_capabilities(request: Request) -> dict[str, Any]:
    return envelope(request, _build_capabilities(request))


@router.get("/system/workers", summary="Worker / daemon lifecycle status")
def system_workers(request: Request, limit: int = 100) -> dict[str, Any]:
    engine = get_engine(request)
    workers = _worker_inventory(engine)
    bounded = workers[: clamp_limit(limit)]
    return envelope(
        request,
        {"workers": bounded, "total_daemon_threads": len(workers)},
        pagination={"count": len(bounded), "truncated": len(bounded) < len(workers)},
    )


@router.get("/runtime/mode", summary="Configured / effective execution mode")
def runtime_mode(request: Request) -> dict[str, Any]:
    return envelope(request, _mode_info(get_engine(request)))
