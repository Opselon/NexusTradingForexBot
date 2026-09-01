"""Trade intelligence — REST API routes (PHASE 09).

Extracted VERBATIM from the former monolith ``server.py`` (CHG-0032 Step 3B,
behavior-preserving). READ-ONLY views over derived intelligence tables
(lifecycle timeline, trade autopsies, behavior detections, evolution
candidates) plus the suitability verdict and self-heal trigger. Nothing here
mutates financial truth or executes orders.

Surface (paths unchanged): /api/intelligence/{summary,positions/{id}/timeline,
autopsies,autopsies/{id},behavior,anomalies,evolution,evolution/scan,
evolution/validate,self-heal}.

BOUNDARY: closures over ``app.state.engine`` only; no live-path imports;
follows the register(app) pattern of model_governance_routes (Step 3A).

USED BY: server.create_app.
DO-NOT-PUT-HERE: governance/research routes (model_governance_routes.py /
research surfaces), news+liquidity+mslie (still in server.py slice).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.web.errors import log_web_error, new_request_id, safe_error_payload

logger = get_logger("nexus_scalp.web.intelligence_routes")

router = APIRouter()


def register_intelligence_routes(app: Any) -> None:
    """Attach trade-intelligence routes (closures over ``app``)."""
    from nexus_scalp.web.server import serialize_enums  # local import: cycle-safe

    def _err(code: str = "INTERNAL_ERROR", **kw: Any) -> dict[str, Any]:
        return safe_error_payload(code=code, request_id=new_request_id(), **kw)

    def _intelligence_worker_status(worker: Any) -> dict[str, Any]:
        from nexus_scalp.intelligence.worker import format_intelligence_worker_status

        if worker is None:
            return {}
        try:
            return format_intelligence_worker_status(worker)
        except Exception as e:
            log_web_error(
                logger, "/api", None, e, context={"msg": "Intelligence worker status failed"}
            )
            return {}

    # PHASE 09: TRADE INTELLIGENCE REST APIs
    # -------------------------------------------------------------------------
    # READ-ONLY views over the derived intelligence tables (lifecycle timeline,
    # trade autopsies, behavior detections, evolution candidates) plus the
    # suitability verdict. Nothing here mutates financial truth or executes.
    # =========================================================================

    def _intelligence() -> tuple[Any, Any] | None:
        """Returns (engine, intelligence_worker) when available."""
        engine = app.state.engine
        if not engine or not hasattr(engine, "intelligence_worker"):
            return None
        return (
            engine,
            getattr(engine, "intelligence_worker", None),
        )

    @router.get("/api/intelligence/summary")
    def get_intelligence_summary() -> dict[str, Any]:
        """Aggregate Trade Intelligence Brain telemetry + worker status."""
        pair = _intelligence()
        if pair is None:
            return {"available": False, "reasons": "ENGINE_UNAVAILABLE"}
        engine, worker = pair
        try:
            from nexus_scalp.intelligence.store import (
                count_autopsies,
                count_lifecycle_events,
            )

            summary = {
                "available": True,
                "lifecycle_events": count_lifecycle_events(engine.audit),
                "autopsies": count_autopsies(engine.audit),
                "worker": _intelligence_worker_status(worker),
                "fetch_time": datetime.now(UTC).isoformat(),
            }
            # Suitability verdict for the last proposal (live explainability).
            verdict = getattr(engine, "_last_suitability_verdict", None)
            if verdict is not None:
                summary["last_suitability"] = verdict.to_dict()
            return serialize_enums(summary)
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Intelligence summary failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/positions/{ticket}/timeline")
    def get_position_timeline(ticket: int) -> dict[str, Any]:
        """Immutable position lifecycle timeline for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_lifecycle_events

            events = load_lifecycle_events(engine.audit, ticket=str(ticket), limit=500)
            return serialize_enums(
                {
                    "available": True,
                    "ticket": str(ticket),
                    "events": [e.model_dump() for e in events],
                }
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Timeline read failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/autopsies")
    def get_intelligence_autopsies(
        strategy_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Bounded listing of trade autopsies (why did each trade win/lose)."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_autopsies

            rows = list_autopsies(engine.audit, strategy_id=strategy_id, limit=limit)
            return serialize_enums({"available": True, "autopsies": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Autopsy list failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/autopsies/{ticket}")
    def get_intelligence_autopsy(ticket: str) -> dict[str, Any]:
        """Single forensic autopsy for one ticket."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_autopsy

            row = load_autopsy(engine.audit, ticket)
            if row is None:
                return {"available": False, "reason": "NO_AUTOPSY"}
            return serialize_enums({"available": True, "autopsy": row})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Autopsy read failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/behavior")
    def get_intelligence_behavior(ticket: int | None = None, limit: int = 100) -> dict[str, Any]:
        """Measurable behavioral-pattern detections."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_behavior_detections

            rows = list_behavior_detections(engine.audit, ticket=ticket, limit=limit)
            return serialize_enums({"available": True, "detections": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Behavior list failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/anomalies")
    def get_intelligence_anomalies(ticket: int | None = None, limit: int = 100) -> dict[str, Any]:
        """Evidence-based anomaly events (TASK-2)."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import list_anomaly_events

            rows = list_anomaly_events(engine.audit, ticket=ticket, limit=limit)
            return serialize_enums({"available": True, "anomalies": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Anomaly list failed"})
            return _err("INTERNAL_ERROR")

    @router.get("/api/intelligence/evolution")
    def get_intelligence_evolution(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Discovered-but-unvalidated strategy evolution candidates."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            from nexus_scalp.intelligence.store import load_evolution_candidates

            rows = load_evolution_candidates(engine.audit, status=status, limit=limit)
            return serialize_enums({"available": True, "candidates": rows})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution list failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/intelligence/evolution/scan")
    def trigger_evolution_scan() -> dict[str, Any]:
        """Runs a bounded evolution discovery pass; candidates are never live."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidates = engine.intelligence_evolution.scan()
            return serialize_enums(
                {"available": True, "candidates": [c.model_dump() for c in candidates]}
            )
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution scan failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/intelligence/evolution/validate")
    def validate_evolution_candidate(
        candidate_id: str, backtest_expectancy_r: float, backtest_sample_count: int
    ) -> dict[str, Any]:
        """Records a backtest result; a candidate becomes VALIDATED only on positive
        evidence over a sample floor, and even then is never live until promoted."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            candidate = engine.intelligence_evolution.validate_candidate(
                candidate_id=candidate_id,
                backtest_expectancy_r=backtest_expectancy_r,
                backtest_sample_count=backtest_sample_count,
            )
            if candidate is None:
                return {"available": False, "reason": "CANDIDATE_NOT_FOUND"}
            return serialize_enums({"available": True, "candidate": candidate.model_dump()})
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Evolution validate failed"})
            return _err("INTERNAL_ERROR")

    @router.post("/api/intelligence/self-heal")
    def trigger_intelligence_self_heal() -> dict[str, Any]:
        """Rebuilds all derived strategy intelligence from the immutable ledger."""
        pair = _intelligence()
        if pair is None:
            return {"available": False}
        engine, _ = pair
        try:
            count = engine.rebuild_experience_intelligence()
            return {"available": True, "rebuilt_strategies": int(count)}
        except Exception as e:
            log_web_error(logger, "/api", None, e, context={"msg": "Intelligence self-heal failed"})
            return _err("INTERNAL_ERROR")

    # =========================================================================
    # PHASE 09B: STRATEGY RESEARCH, BACKTEST & VALIDATION ENGINE (read + gates)
    # -------------------------------------------------------------------------
    # Research consumes the immutable experience ledger ONLY. Every endpoint is
    # bounded; validation runs live in the background worker or are triggered
    # explicitly by an operator. Research NEVER places, modifies or closes an
    # order, and a candidate can NEVER become ACTIVE automatically.
    # =========================================================================
