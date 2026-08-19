"""Incident background worker (TASK-12 spec 58/59).

The worker is:
- BACKGROUND: invoked via asyncio.to_thread from the live engine, never on
  the tick path (INV-001 intact).
- READ-ONLY + BOUNDED: bounded windows, bounded result sets, bounded writes
  (new incidents only). It performs NO trading mutation (spec 0) and NO
  automatic recovery execution (spec 29).
- DEDUPLICATING: repeated identical events merge into one incident with a
  repeat counter (spec 31/49).

The worker consumes structured evidence produced by the caller (log scan
summaries, DB anomaly counts) and correlates them into incidents.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.incidents.correlator import IncidentCorrelator, TelemetryEvent
from nexus_scalp.incidents.impact import ImpactAnalyzer, RecoveryPlanner
from nexus_scalp.incidents.models import EventSource
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.worker")

#: Default cadence (seconds). Off hot path; bounded per cycle.
DEFAULT_INTERVAL_SEC: float = 300.0

#: A full cycle must stay well under 30s (bounded by design).
CYCLE_BUDGET_SEC: float = 30.0

#: Max incident rows written per cycle (never flood the DB).
MAX_SAVES_PER_CYCLE: int = 50


class IncidentWorker:
    """Bounded, failure-isolated incident correlation worker."""

    def __init__(
        self,
        store: IncidentStore,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        windows_sec: dict[str, float] | None = None,
        auto_recovery_plan: bool = True,
        auto_impact: bool = True,
    ) -> None:
        self.store = store
        self.interval_sec = float(interval_sec)
        self.correlator = IncidentCorrelator(windows_sec=windows_sec)
        self.impact_analyzer = ImpactAnalyzer(db_path=self.store.db_path)
        self.recovery_planner = RecoveryPlanner()
        self.auto_recovery_plan = auto_recovery_plan
        self.auto_impact = auto_impact

        self.running = False
        self.cycle_count = 0
        self.cycle_duration_ms = 0.0
        self.last_cycle_start: datetime | None = None
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._backlog: list[dict[str, Any]] = []
        # telemetry
        self.incidents_created = 0
        self.incidents_updated = 0
        self.events_seen = 0

    # ------------------------------------------------------------------
    # Lifecycle (mirrors IntelligenceWorker conventions)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        logger.info("[INCIDENT_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        logger.info("[INCIDENT_WORKER] event=STOP status=IDLE")

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self, events: list[dict[str, Any]] | None = None) -> bool:
        """Runs one bounded correlation cycle if the interval has elapsed.

        ``events``: optional externally-supplied telemetry batch (log/DB
        evidence). When None, the worker uses its in-memory event backlog
        (empty by default — producers push events explicitly).
        """
        if not self.running:
            return False
        now = time.time()
        if now - self._last_run_ts < self.interval_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1
        self.last_cycle_start = datetime.now(UTC)
        started = time.perf_counter()
        try:
            self._cycle_once(events or [])
            self.cycle_duration_ms = (time.perf_counter() - started) * 1000.0
            self.last_error = ""
            return True
        except Exception as err:
            self.cycle_duration_ms = (time.perf_counter() - started) * 1000.0
            self.last_error = str(err)
            logger.error("[INCIDENT_WORKER] event=FAILURE", cycle=self.cycle_count, error=str(err))
            return False

    def ingest(self, telem: dict[str, Any]) -> None:
        """Queue-ish ingestion for producers (bounded ring).

        The worker correlates at the next tick; producers never block.
        """
        if not isinstance(telem, dict):
            return
        self._backlog.append(telem)
        if len(self._backlog) > 2000:
            self._backlog = self._backlog[-2000:]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def backlog(self) -> list[dict[str, Any]]:
        return list(self._backlog)

    def _cycle_once(self, events: list[dict[str, Any]]) -> None:
        backlog = list(self._backlog) + list(events)
        self._backlog.clear()
        if not backlog:
            return
        telemetry = [_to_telemetry(e) for e in backlog[:2000]]
        telemetry = [t for t in telemetry if t is not None]
        self.events_seen += len(telemetry)
        if not telemetry:
            return
        existing = self.store.list_incidents(limit=200, ordered_by="last_seen_at")
        result = self.correlator.correlate(telemetry, existing)
        saves = 0
        for inc in result.incidents:
            # impact + recovery plan (auto, read-only)
            if self.auto_impact:
                inc.impact = self.impact_analyzer.analyze(inc)
            if self.auto_recovery_plan and not inc.recovery_plan.options:
                inc.recovery_plan = self.recovery_planner.generate(inc)
            if saves >= MAX_SAVES_PER_CYCLE:
                continue
            self.store.save(inc)
            saves += 1
            if inc.incident_id not in {i.incident_id for i in existing}:
                self.incidents_created += 1
            else:
                self.incidents_updated += 1
        if result.incidents:
            logger.info(
                "[INCIDENT_WORKER] event=CYCLE",
                cycle=self.cycle_count,
                events=len(telemetry),
                created=result.new,
                merged=result.merged,
                saved=saves,
            )


def _to_telemetry(raw: dict[str, Any]) -> TelemetryEvent | None:
    """Converts a producer dict into a TelemetryEvent (lenient)."""
    try:
        ts_raw = raw.get("timestamp")
        ts: datetime
        if isinstance(ts_raw, datetime):
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
        elif isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(float(ts_raw), UTC)
        elif isinstance(ts_raw, str):
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        else:
            ts = datetime.now(UTC)
        return TelemetryEvent(
            timestamp=ts,
            event_type=str(raw.get("event_type") or raw.get("type") or "OBSERVATION"),
            component=str(raw.get("component") or "unknown"),
            error_code=str(raw.get("error_code") or ""),
            correlation_id=str(raw.get("correlation_id") or ""),
            ticket=str(raw.get("ticket") or raw.get("execution_id") or ""),
            execution_id=str(raw.get("execution_id") or ""),
            severity=raw.get("severity") if raw.get("severity") else None,
            payload={
                k: v
                for k, v in raw.items()
                if k
                not in (
                    "timestamp",
                    "event_type",
                    "type",
                    "component",
                    "error_code",
                    "correlation_id",
                    "ticket",
                    "execution_id",
                    "severity",
                )
            },
            source=EventSource(str(raw.get("source") or "TELEMETRY").upper())
            if str(raw.get("source") or "TELEMETRY").upper() in EventSource._value2member_map_
            else EventSource.TELEMETRY,
        )
    except Exception:
        return None


def format_incident_worker_status(worker: IncidentWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry for the REST layer."""
    return {
        "running": worker.running,
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "last_cycle_start": worker.last_cycle_start.isoformat()
        if worker.last_cycle_start
        else None,
        "last_cycle_duration_ms": round(worker.cycle_duration_ms, 1)
        if worker.cycle_duration_ms
        else 0.0,
        "last_error": worker.last_error or "",
        "events_seen": worker.events_seen,
        "incidents_created": worker.incidents_created,
        "incidents_updated": worker.incidents_updated,
        "backlog_size": len(worker.backlog),
        "status": "RUNNING" if worker.running else "IDLE",
    }


__all__ = [
    "CYCLE_BUDGET_SEC",
    "DEFAULT_INTERVAL_SEC",
    "MAX_SAVES_PER_CYCLE",
    "IncidentWorker",
    "format_incident_worker_status",
]
