"""
Strategy Command Center API Routes
==================================
PHASE 3 implementation: canonical read-only API endpoints backing the
Command Center UI (overview, fleet, inspector, execution safety, validation
panel, timeline). All data comes from the authoritative registry + event
projection. No UI-side state fabrication.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.event_projection import LifecycleEventProjection
from nexus_scalp.research.models import CandidateLifecycle
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.snapshot import build_snapshot

logger = get_logger("nexus_scalp.web.command_center_routes")

#: All lifecycle states in pipeline order (spatial zone ordering).
PIPELINE_ORDER: list[str] = [
    "DISCOVERED",
    "BACKTESTING",
    "VALIDATING",
    "OOS_TESTING",
    "ROBUSTNESS_TESTING",
    "VALIDATED",
    "SHADOW",
    "ACTIVE",
]


def _serialize_enums(obj: Any) -> Any:
    """Recursively converts StrEnum values to plain strings for JSON."""
    if hasattr(obj, "value") and isinstance(obj.value, str):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize_enums(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_enums(x) for x in obj]
    return obj


class CommandCenterAPI:
    """
    Read-only application service exposing the Strategy Command Center data.

    Every method returns JSON-safe dicts built strictly from authoritative
    domain sources.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        registry: StrategyRegistry | None = None,
        projection: LifecycleEventProjection | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.registry = registry or StrategyRegistry(audit_repo)
        self.projection = projection or LifecycleEventProjection(audit_repo)

    # ------------------------------------------------------------------
    # System overview
    # ------------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """Global strategy counts by lifecycle + anomaly/stuck summaries."""
        entries = self.registry.list(limit=2000)
        by_state: dict[str, int] = {s: 0 for s in PIPELINE_ORDER}
        terminal: dict[str, int] = {"REJECTED": 0, "DEGRADED": 0, "RETIRED": 0}
        blocked = 0
        eligible = 0
        stuck: list[dict[str, Any]] = []
        now_ok = True
        for e in entries:
            state = e.lifecycle.value
            if state in by_state:
                by_state[state] += 1
            elif state in terminal:
                terminal[state] += 1
            snap = build_snapshot(e)
            ee = snap.execution_eligibility
            if ee.eligibility_state == "BLOCKED":
                blocked += 1
            elif ee.eligibility_state == "YES":
                eligible += 1
            if e.updated_at is not None:
                from datetime import UTC, datetime

                age_h = (
                    datetime.now(UTC) - e.updated_at.astimezone(UTC)
                ).total_seconds() / 3600.0
                if state not in ("ACTIVE", "SHADOW", "VALIDATED", "REJECTED", "RETIRED", "DEGRADED"):
                    stuck.append({
                        "strategy_id": e.strategy_id,
                        "state": state,
                        "hours_in_state": round(age_h, 1),
                    })
        stuck.sort(key=lambda s: -s["hours_in_state"])
        return _serialize_enums({
            "available": True,
            "total_strategies": len(entries),
            "by_lifecycle": by_state,
            "terminal": terminal,
            "execution_eligible_count": eligible,
            "blocked_count": blocked,
            "stuck_strategies": stuck[:10],
        })

    # ------------------------------------------------------------------
    # Fleet view
    # ------------------------------------------------------------------

    def fleet(
        self,
        lifecycle: str | None = None,
        execution_filter: str | None = None,
        limit: int = 2000,
    ) -> dict[str, Any]:
        """Fleet table rows with health/eligibility/evidence columns."""
        entries = self.registry.list(lifecycle=lifecycle, limit=min(limit, 2000))
        rows: list[dict[str, Any]] = []
        for e in entries:
            snap = build_snapshot(e)
            ee = snap.execution_eligibility
            if execution_filter and ee.eligibility_state != execution_filter:
                continue
            rows.append(_serialize_enums({
                "strategy_id": e.strategy_id,
                "strategy_version": e.strategy_version,
                "lifecycle": e.lifecycle.value,
                "confidence": round(e.confidence, 3),
                "sample_count": e.sample_count,
                "health_final": snap.health_score.get("final"),
                "eligibility_state": ee.eligibility_state,
                "eligibility_reason": ee.reason,
                "evidence": snap.evidence_summary,
                "updated_at": e.updated_at.isoformat(),
            }))
        return {"available": True, "count": len(rows), "rows": rows}

    # ------------------------------------------------------------------
    # Inspector
    # ------------------------------------------------------------------

    def inspector(self, strategy_id: str) -> dict[str, Any]:
        """Full snapshot + events + evidence completeness for one strategy."""
        entry = self.registry.get(strategy_id)
        if entry is None:
            return {"available": False, "error": "STRATEGY_NOT_FOUND"}
        snap = build_snapshot(entry)
        events = self.projection.events_for_strategy(strategy_id)
        completeness = self.projection.evidence_completeness(strategy_id)
        invariant = self.registry.invariant_check(entry)
        out = snap.model_dump()
        out["events"] = events[-100:]
        out["evidence_completeness"] = completeness
        out["invariant_check"] = invariant
        return _serialize_enums({"available": True, **out})

    # ------------------------------------------------------------------
    # Execution safety panel
    # ------------------------------------------------------------------

    def execution_safety(self, strategy_id: str) -> dict[str, Any]:
        """Explicit CAN-THIS-TRADE answer backed only by domain authority."""
        entry = self.registry.get(strategy_id)
        if entry is None:
            return {
                "available": False,
                "eligibility_state": "UNKNOWN",
                "reason": "Strategy not found in registry.",
            }
        snap = build_snapshot(entry)
        ee = snap.execution_eligibility
        return _serialize_enums({
            "available": True,
            "strategy_id": strategy_id,
            "lifecycle": entry.lifecycle.value,
            "eligibility_state": ee.eligibility_state,
            "can_trade": ee.can_trade,
            "reason": ee.reason,
            "required_gate": ee.required_gate,
            "blockers": ee.blockers,
            "invariant_check": self.registry.invariant_check(entry),
        })

    # ------------------------------------------------------------------
    # Validation panel
    # ------------------------------------------------------------------

    def validation_pipeline(self, strategy_id: str) -> dict[str, Any]:
        """Per-gate status/result/sample/duration summary."""
        entry = self.registry.get(strategy_id)
        if entry is None:
            return {"available": False, "error": "STRATEGY_NOT_FOUND"}
        gates: list[dict[str, Any]] = []
        bt = entry.backtest
        gates.append({
            "gate": "BACKTEST",
            "status": ("PASS" if bt and bt.total_trades > 0 else "FAIL" if bt else "NOT_RUN"),
            "expectancy_r": bt.expectancy_r if bt else None,
            "total_trades": bt.total_trades if bt else None,
            "profit_factor": bt.profit_factor if bt else None,
        })
        wf = entry.walkforward
        gates.append({
            "gate": "WALK_FORWARD",
            "status": ("PASS" if wf and wf.passed else "FAIL" if wf else "NOT_RUN"),
            "fold_count": wf.fold_count if wf else None,
            "degradation": wf.degradation if wf else None,
            "avg_oos_expectancy_r": wf.avg_oos_expectancy_r if wf else None,
        })
        oos = entry.oos
        gates.append({
            "gate": "OOS",
            "status": (oos.status if oos else "NOT_RUN"),
            "oos_expectancy_r": oos.oos_expectancy_r if oos else None,
            "oos_samples": oos.oos_samples if oos else None,
            "reason": oos.reason if oos else "",
        })
        rob = entry.robustness
        gates.append({
            "gate": "ROBUSTNESS",
            "status": (rob.status if rob else "NOT_RUN"),
            "max_degradation": rob.max_degradation if rob else None,
            "reason": rob.reason if rob else "",
        })
        score = entry.score
        gates.append({
            "gate": "SCORE_VERDICT",
            "status": (score.verdict if score else "NOT_RUN"),
            "final_score": score.final_score if score else None,
            "reasons": score.reasons if score else [],
        })
        return _serialize_enums({
            "available": True,
            "strategy_id": strategy_id,
            "lifecycle": entry.lifecycle.value,
            "gates": gates,
        })

    # ------------------------------------------------------------------
    # Decision timeline
    # ------------------------------------------------------------------

    def timeline(self, strategy_id: str, limit: int = 200) -> dict[str, Any]:
        """Chronological decision timeline (lifecycle + validation runs)."""
        events = self.projection.events_for_strategy(
            strategy_id, include_runs=True, limit=limit
        )
        return {
            "available": True,
            "strategy_id": strategy_id,
            "events": events,
            "count": len(events),
        }
