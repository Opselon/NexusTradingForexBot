"""
Strategy Command Center Web Integration (Phase 8a)
==================================================
Wires the Command Center API endpoints, spatial layout, explainability,
debug intelligence, and historical time machine into the FastAPI web server.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus_scalp.web.command_center_routes import CommandCenterAPI
from nexus_scalp.research.spatial_layout import SpatialLayout
from nexus_scalp.research.attribution import AIAttributionEngine
from nexus_scalp.research.debug_intelligence import (
    compute_anomaly_score,
    compute_debug_priority,
    compute_validation_consistency,
    decompose_strategy_health,
    generate_debug_hints,
)
from nexus_scalp.research.time_machine import TimeMachine


def register_command_center_routes(app: Any, get_research_engine: Any, serialize_enums: Any, _err: Any) -> None:
    """Registers /api/command-center/* endpoints on the FastAPI app."""

    def _get_api() -> CommandCenterAPI | None:
        eng = get_research_engine()
        if eng is None or not hasattr(eng, "audit"):
            return None
        return CommandCenterAPI(eng.audit)

    @app.get("/api/command-center/overview")
    def cc_overview() -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            return serialize_enums(api.overview())
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/fleet")
    def cc_fleet(lifecycle: str | None = None, execution_filter: str | None = None, limit: int = 2000) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            return serialize_enums(api.fleet(lifecycle=lifecycle, execution_filter=execution_filter, limit=limit))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/inspector/{strategy_id}")
    def cc_inspector(strategy_id: str) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            res = api.inspector(strategy_id)
            if not res.get("available"):
                return res
            # Enrich with debug intelligence + AI explainability
            entry = api.registry.get(strategy_id)
            if entry is not None:
                attr_eng = AIAttributionEngine(api.audit_repo)
                res["ai_attribution"] = attr_eng.attribution(entry)
                res["debug_intelligence"] = {
                    "anomaly_score": compute_anomaly_score(entry),
                    "validation_consistency": compute_validation_consistency(entry),
                    "health_decomposition": decompose_strategy_health(entry),
                    "debug_priority": compute_debug_priority(entry),
                    "hints": generate_debug_hints(entry),
                }
            return serialize_enums(res)
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/execution-safety/{strategy_id}")
    def cc_execution_safety(strategy_id: str) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            return serialize_enums(api.execution_safety(strategy_id))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/spatial")
    def cc_spatial(max_columns: int = 6, limit: int = 2000) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            entries = api.registry.list(limit=limit)
            snapshots = {e.strategy_id: api.inspector(e.strategy_id) for e in entries}
            layout = SpatialLayout(max_columns=max_columns)
            return serialize_enums(layout.compute(entries, snapshots=snapshots))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/timeline/{strategy_id}")
    def cc_timeline(strategy_id: str, limit: int = 200) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            return serialize_enums(api.timeline(strategy_id, limit=limit))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/timemachine/frame")
    def cc_timemachine_frame(at: str, limit: int = 2000) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
            entries = api.registry.list(limit=limit)
            tm = TimeMachine(api.audit_repo)
            return serialize_enums(tm.frame_at(entries, dt))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})

    @app.get("/api/command-center/timemachine/bounds")
    def cc_timemachine_bounds(limit: int = 2000) -> dict[str, Any]:
        api = _get_api()
        if api is None:
            return {"available": False, "reason": "RESEARCH_ENGINE_UNAVAILABLE"}
        try:
            entries = api.registry.list(limit=limit)
            tm = TimeMachine(api.audit_repo)
            return serialize_enums(tm.bounds(entries))
        except Exception as e:
            return _err("INTERNAL_ERROR", extra={"error": str(e)})
