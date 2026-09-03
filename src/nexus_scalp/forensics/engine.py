"""ForensicHealthEngine — the centralized POST-70D continuous health engine.

TASK-11 §3/§49/§50/§54. Orchestrates the read-only checks, classifies the
aggregate FORENSIC_HEALTH_SNAPSHOT (never averaging criticals away), keeps
an in-memory + on-disk health history for the dashboard, and throttles
alert severity classes (§54: schema mismatch immediate, feature drift
aggregated, performance periodic).

EXPLICITLY NOT a self-modifying system (§0/§55): the engine never changes
trading logic, features, models, labels or databases. It detects, diagnoses,
quarantines (by flagging), and blocks unsafe startup/deployment only when a
mandatory check is CRITICAL.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics import checks as C
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
    worst_status,
)
from nexus_scalp.forensics.references import (
    FEATURE_REFERENCES,
    FeatureReferenceRegistry,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.forensics.engine")

#: Alert severity classes for throttling (§54).
#: immediate = every failing run raises the alert; aggregated = at most once
#: per aggregation window; periodic = at most once per periodic window.
ALERT_POLICY: dict[str, str] = {
    "CHECK-FCS-01": "immediate",  # schema mismatch
    "CHECK-FCS-04": "immediate",  # vector contract
    "CHECK-MDL-01": "immediate",  # model incompatibility
    "CHECK-MDL-03": "immediate",  # model/schema dimension
    "CHECK-INT-01": "immediate",  # database corruption
    "CHECK-ACC-02": "immediate",  # duplicate economic outcome
    "CHECK-MIG-01": "immediate",  # migration drift
    "CHECK-LIQ-01": "aggregated",  # feature drift
    "CHECK-NWS-01": "aggregated",  # news source degradation
    "CHECK-RTP-01": "immediate",  # parity broken
    "CHECK-RTP-03": "immediate",  # future leakage
    "CHECK-GOV-02": "immediate",  # champion identity
    "CHECK-PER-01": "periodic",  # performance
}

#: Aggregation/periodic windows in seconds.
AGGREGATED_WINDOW_SEC: float = 15 * 60.0
PERIODIC_WINDOW_SEC: float = 60 * 60.0


@dataclass
class SnapshotRecord:
    """One FORENSIC_HEALTH_SNAPSHOT (aggregate) — persisted + served."""

    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    overall: str = HealthStatus.PASS.value
    groups: dict[str, str] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    critical_count: int = 0
    warning_count: int = 0
    degraded_count: int = 0
    unknown_count: int = 0
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall,
            "groups": self.groups,
            "checks": self.checks,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "degraded_count": self.degraded_count,
            "unknown_count": self.unknown_count,
            "correlation_id": self.correlation_id,
        }


class ForensicHealthEngine:
    """Runs the check matrix and produces snapshots/alerts."""

    def __init__(
        self,
        *,
        references: FeatureReferenceRegistry | None = None,
        history_dir: Path | None = None,
        max_history: int = 50,
    ) -> None:
        self.references = references or FEATURE_REFERENCES
        self.history_dir = history_dir or Path("artifacts") / "forensics"
        self.max_history = max_history
        self._history: list[SnapshotRecord] = []
        self._last_alert_at: dict[str, float] = {}
        self._active_blockers: list[str] = []
        # TASK-12 §23: when the proven golden baseline exists, load the frozen
        # liquidity references so drift/deadness checks become measurable.
        # The freeze is provenance-guarded (only the golden doc may load).
        self._auto_freeze_references()

    def _auto_freeze_references(self) -> None:
        try:
            from nexus_scalp.forensics.references import (
                GOLDEN_BASELINE_PATH,
                freeze_liquidity_references_from_golden,
            )

            if Path(GOLDEN_BASELINE_PATH).exists() and len(self.references) == 0:
                freeze_liquidity_references_from_golden(registry=self.references)
        except Exception as exc:
            logger.debug("[FORENSIC] golden reference auto-freeze skipped", error=str(exc))

    # ------------------------------------------------------------------
    # check matrix
    # ------------------------------------------------------------------
    def check_groups(self) -> dict[str, list[Callable[[], CheckResult]]]:
        """Named groups mirroring the TASK-11 §51 dashboard rows."""
        return {
            "FeatureContract": [
                C.check_feature_schema_registry,
                C.check_feature_contract_70d,
                lambda: C.check_feature_liquidity_contract(registry=self.references),
                lambda: C.check_feature_contract_vector(None),
            ],
            "Model": [
                C.check_model_artifact,
                C.check_model_semantic_health,
                C.check_model_dimension_contract,
            ],
            "Parity": [
                C.check_causal_canary,
                C.check_training_live_parity_canary,
            ],
            "Dataset": [
                C.check_dataset_manifest_health,
            ],
            "Accounting": [
                C.check_accounting_divergence,
                C.check_duplicate_economic_outcome,
                C.check_impossible_excursion,
                C.check_experience_outcome_gap,
            ],
            "Database": [
                C.check_database_integrity,
                C.check_migration_state,
                C.check_database_growth,
            ],
            "Liquidity": [
                lambda: C.check_liquidity_feature_health(references=self.references),
            ],
            "News": [
                C.check_news_health,
                C.check_news_worker_progress,
                C.check_news_availability_matrix,
            ],
            "Shadow": [
                C.check_shadow_health,
            ],
            "Governance": [
                C.check_governance_consistency,
                C.check_champion_identity,
            ],
            "UI": [
                C.check_ui_canonical_state,
                C.check_ui_bundle_drift,
            ],
            "API": [
                C.check_api_200_but_wrong,
                C.check_chart_semantic_health,
            ],
            "Telegram": [
                C.check_telegram_delivery,
            ],
            "Trace": [
                C.check_trace_completeness,
                C.check_correlation_propagation,
                C.check_silent_fallback,
            ],
            "Workers": [
                C.check_worker_progress,
            ],
            "Runtime": [
                C.check_runtime_mode_integrity,
            ],
            "Performance": [
                C.check_performance_regression,
            ],
        }

    def run_checks(self) -> dict[str, list[CheckResult]]:
        """Runs all checks, grouped; each result carries duration/evidence."""
        out: dict[str, list[CheckResult]] = {}
        for group, fns in self.check_groups().items():
            results: list[CheckResult] = []
            for fn in fns:
                start = time.perf_counter()
                try:
                    r = fn()
                except Exception as exc:  # isolation boundary
                    from nexus_scalp.forensics.models import new_correlation_id

                    r = CheckResult(
                        check_id="CHECK-RAISED",
                        status=HealthStatus.UNKNOWN,
                        evidence=f"group {group} raised: {exc!r}",
                        observed={"error": str(exc)},
                        expected="check completes",
                        correlation_id=new_correlation_id(),
                    )
                results.append(
                    CheckResult(
                        check_id=r.check_id,
                        status=r.status,
                        timestamp=r.timestamp,
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                        evidence=r.evidence,
                        observed=r.observed,
                        expected=r.expected,
                        correlation_id=r.correlation_id,
                        detail=r.detail,
                    )
                )
            out[group] = results
        return out

    # ------------------------------------------------------------------
    # snapshot (§49) without averaging (§50)
    # ------------------------------------------------------------------
    def snapshot(self, persist: bool = True) -> SnapshotRecord:
        grouped = self.run_checks()
        all_results: list[CheckResult] = [r for results in grouped.values() for r in results]
        group_status = {g: worst_status(results).value for g, results in grouped.items()}
        overall = worst_status(all_results)
        rec = SnapshotRecord(
            overall=overall.value,
            groups=group_status,
            checks=[r.to_dict() for r in all_results],
            critical_count=sum(1 for r in all_results if r.status is HealthStatus.CRITICAL),
            warning_count=sum(1 for r in all_results if r.status is HealthStatus.WARNING),
            degraded_count=sum(1 for r in all_results if r.status is HealthStatus.DEGRADED),
            unknown_count=sum(1 for r in all_results if r.status is HealthStatus.UNKNOWN),
            correlation_id=(
                all_results[0].correlation_id if all_results else datetime.now(UTC).isoformat()
            ),
        )
        self._history.append(rec)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]
        if persist:
            self._persist(rec)
        self._fire_alerts(rec)
        return rec

    # ------------------------------------------------------------------
    # alerts (§54 throttled)
    # ------------------------------------------------------------------
    def _fire_alerts(self, rec: SnapshotRecord) -> None:
        now = time.monotonic()
        blocking: list[str] = []
        for check in rec.checks:
            if check["status"] != HealthStatus.CRITICAL.value:
                continue
            cid = check["check_id"]
            policy = ALERT_POLICY.get(cid, "aggregated")
            if policy == "immediate":
                self._fire(cid, rec, now, force=True)
                blocking.append(cid)
            elif policy == "aggregated":
                if self._fire(cid, rec, now, window=AGGREGATED_WINDOW_SEC):
                    blocking.append(cid)
            else:  # periodic
                self._fire(cid, rec, now, window=PERIODIC_WINDOW_SEC)
        self._active_blockers = blocking

    def _fire(
        self,
        check_id: str,
        rec: SnapshotRecord,
        now: float,
        window: float = 0.0,
        force: bool = False,
    ) -> bool:
        last = self._last_alert_at.get(check_id, 0.0)
        if not force and window > 0 and (now - last) < window:
            return False
        self._last_alert_at[check_id] = now
        logger.error(
            "[FORENSIC_ALERT] check=%s status=CRITICAL snapshot=%s",
            check_id,
            rec.overall,
        )
        return True

    @property
    def blocking_checks(self) -> list[str]:
        return list(self._active_blockers)

    def can_deploy(self) -> tuple[bool, list[str]]:
        """Release pre-flight gate (§44): a failed mandatory check blocks."""
        rec = self.snapshot(persist=False)
        blockers = [c["check_id"] for c in rec.checks if c["status"] == HealthStatus.CRITICAL.value]
        return (not blockers, blockers)

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _persist(self, rec: SnapshotRecord) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            path = self.history_dir / "forensic_health_snapshot.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rec.to_dict(), fh, indent=2, default=str)
            # rolling history (bounded)
            hist_path = self.history_dir / "history.jsonl"
            with open(hist_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict(), default=str) + "\n")
        except OSError as exc:
            logger.warning("[FORENSIC] snapshot persistence failed", error=str(exc))

    def load_persisted(self) -> SnapshotRecord | None:
        path = self.history_dir / "forensic_health_snapshot.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SnapshotRecord(
                timestamp=data.get("timestamp", ""),
                overall=data.get("overall", HealthStatus.UNKNOWN.value),
                groups=data.get("groups", {}),
                checks=data.get("checks", []),
                critical_count=data.get("critical_count", 0),
                warning_count=data.get("warning_count", 0),
                degraded_count=data.get("degraded_count", 0),
                unknown_count=data.get("unknown_count", 0),
                correlation_id=data.get("correlation_id", ""),
            )
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------
    # dashboard data (§51/§52)
    # ------------------------------------------------------------------
    def dashboard(self) -> dict[str, Any]:
        """Central System Forensic Health — every item status/last check/evidence."""
        rec = self.snapshot(persist=True)
        rows: dict[str, Any] = {}
        for check in rec.checks:
            rows[check["check_id"]] = {
                "status": check["status"],
                "last_check": check["timestamp"],
                "last_error": check["evidence"]
                if check["status"]
                in (
                    HealthStatus.WARNING.value,
                    HealthStatus.DEGRADED.value,
                    HealthStatus.CRITICAL.value,
                )
                else "",
                "evidence": check["evidence"],
                "observed": check["observed"],
                "expected": check["expected"],
                "correlation_id": check["correlation_id"],
                "duration_ms": check["duration_ms"],
                # expandable detail view (§52)
                "detail_view": {
                    "CHECK": check["check_id"],
                    "EXPECTED": check["expected"],
                    "OBSERVED": check["observed"],
                    "TIMESTAMP": check["timestamp"],
                    "CORRELATION_ID": check["correlation_id"],
                    "RELATED_FILE": "",
                    "RELATED_BUG": "",
                    "RECOMMENDED_ACTION": _recommended_action(check["check_id"], check["status"]),
                },
            }
        return {
            "groups": rec.groups,
            "overall": rec.overall,
            "timestamp": rec.timestamp,
            "rows": rows,
            "critical_count": rec.critical_count,
            "warning_count": rec.warning_count,
            "degraded_count": rec.degraded_count,
            "unknown_count": rec.unknown_count,
        }


def _recommended_action(check_id: str, status: str) -> str:
    if status in (HealthStatus.PASS.value, HealthStatus.UNKNOWN.value):
        return "Continue monitoring."
    return {
        "CHECK-FCS-": "Review the feature schema change against INV-70D-001..006 and update the registry deliberately.",
        "CHECK-MDL-": "Inspect the model artifact and scaler chain; re-run the deterministic load gate (TASK-6).",
        "CHECK-RTP-": "Re-run the causal/parity canaries; investigate producer divergence before release.",
        "CHECK-ACC-": "Open a forensic ticket: do NOT auto-rewrite financial history (INV-007).",
        "CHECK-INT-": "Run `nexus db verify` and `nexus doctor`; do not mutate the DB from the monitor.",
        "CHECK-LIQ-": "Compare against the frozen reference; do NOT auto-rewrite the feature (TASK-11 §55).",
        "CHECK-NWS-": "Triage the news source/degradation with `nexus doctor` and the news health API.",
        "CHECK-SHD-": "Inspect shadow worker; differences must be explanation-visible.",
        "CHECK-GOV-": "Review governance event ledger; promotion requires operator approval (INV-015).",
        "CHECK-UI-": "Verify the canonical state endpoint and the Web bundle generation.",
        "CHECK-API-": "Probe the endpoint semantically — HTTP 200 is not health (§37).",
        "CHECK-TEL-": "Check telegram notifier config via settings service (INV-010).",
        "CHECK-TRC-": "Attach correlation ids / complete the trace for the failing subsystem.",
        "CHECK-RSW-": "Restart the worker or investigate why it reports progress-less RUNNING.",
        "CHECK-RTM-": "Reconcile configured mode vs operational mode.",
        "CHECK-PER-": "Re-baseline timings after the change; investigate regressions.",
        "CHECK-GRW-": "Check retention/hygiene; a bounded DB is a healthy DB.",
    }.get(check_id[:9], "Open a forensic ticket with the correlation id.")
