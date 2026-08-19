"""Incident background worker (TASK-12 spec 58/59, TASK-13 STEP-01/02/03).

The worker is:
- BACKGROUND: invoked via asyncio.to_thread from the live engine, never on
  the tick path (INV-001 intact).
- READ-ONLY + BOUNDED: bounded windows, bounded result sets, bounded writes
  (new incidents only). It performs NO trading mutation (spec 0) and NO
  automatic recovery execution (spec 29).
- DEDUPLICATING: repeated identical events merge into one incident with a
  repeat counter (spec 31/49).

TASK-13 additions:
- Explicit state machine: STARTING / RUNNING / DEGRADED / STOPPING / STOPPED /
  FAILED (spec 7). A worker is not healthy merely because the process is
  alive — it must demonstrate progress (last_useful_work, incidents_*).
- Latency percentiles (p50/p95/p99) for cycle duration.
- Structured telemetry ingestion: producers push canonical runtime events as
  dicts via `ingest()`; the worker correlates at the next tick.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.incidents.correlator import IncidentCorrelator, TelemetryEvent
from nexus_scalp.incidents.impact import ImpactAnalyzer, RecoveryPlanner
from nexus_scalp.incidents.models import EventSource
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.worker")

#: Default cadence (seconds). Off hot path; bounded per cycle.
DEFAULT_INTERVAL_SEC: float = 60.0

#: A full cycle must stay well under 30s (bounded by design).
CYCLE_BUDGET_SEC: float = 30.0

#: Max incident rows written per cycle (never flood the DB).
MAX_SAVES_PER_CYCLE: int = 50

#: Max telemetry events consumed per cycle.
MAX_EVENTS_PER_CYCLE: int = 2000

#: Max in-memory backlog (bounded ring).
MAX_BACKLOG: int = 2000

#: Latency percentiles retained (bounded ring of recent cycle durations).
LATENCY_WINDOW: int = 200


class IncidentWorkerState(StrEnum):
    """Worker state machine (TASK-13 spec 7)."""

    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class IncidentWorker:
    """Bounded, failure-isolated incident correlation worker.

    State machine:
        STARTING -> RUNNING -> STOPPING -> STOPPED
        RUNNING  -> DEGRADED (transient cycle failure, recovers to RUNNING)
        RUNNING  -> FAILED   (persistent failure)
    """

    def __init__(
        self,
        store: IncidentStore,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        windows_sec: dict[str, float] | None = None,
        auto_recovery_plan: bool = True,
        auto_impact: bool = True,
        telegram_notifier: Any | None = None,
        telegram_min_severity: str = "HIGH",
    ) -> None:
        self.store = store
        self.interval_sec = float(interval_sec)
        self.correlator = IncidentCorrelator(windows_sec=windows_sec)
        self.impact_analyzer = ImpactAnalyzer(db_path=self.store.db_path)
        self.recovery_planner = RecoveryPlanner()
        self.auto_recovery_plan = auto_recovery_plan
        self.auto_impact = auto_impact
        # TASK-13 STEP-08: Telegram alerting (throttled, deduplicated)
        self._telegram = None
        if telegram_notifier is not None:
            from nexus_scalp.incidents.telegram import IncidentTelegramNotifier

            self._telegram = IncidentTelegramNotifier(
                notifier=telegram_notifier,
                cooldown_sec=900.0,
                repeat_cooldown_sec=3600.0,
            )
        self._telegram_min_severity = str(telegram_min_severity).upper()

        # State machine (TASK-13 spec 7)
        self.state: IncidentWorkerState = IncidentWorkerState.STOPPED
        self.cycle_count = 0
        self.last_start: datetime | None = None
        self.last_success: datetime | None = None
        self.last_failure: datetime | None = None
        self.last_useful_work: datetime | None = None
        self.cycle_duration_ms = 0.0
        self.last_error: str = ""
        self.consecutive_failures = 0
        self._last_run_ts: float = 0.0
        self._backlog: list[dict[str, Any]] = []
        self._durations_ms: list[float] = []

        # telemetry
        self.incidents_created = 0
        self.incidents_updated = 0
        self.incidents_deduplicated = 0
        self.events_seen = 0
        self.events_dropped = 0

    # ------------------------------------------------------------------
    # Lifecycle (state machine)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.state in (IncidentWorkerState.RUNNING, IncidentWorkerState.STARTING):
            return
        self.state = IncidentWorkerState.STARTING
        self.last_start = datetime.now(UTC)
        self._last_run_ts = 0.0
        self.state = IncidentWorkerState.RUNNING
        logger.info("[INCIDENT_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        if self.state in (IncidentWorkerState.STOPPED, IncidentWorkerState.STOPPING):
            return
        self.state = IncidentWorkerState.STOPPING
        self.state = IncidentWorkerState.STOPPED
        logger.info("[INCIDENT_WORKER] event=STOP status=STOPPED")

    def fail(self, reason: str) -> None:
        """Persistent failure — state FAILED (recoverable via start())."""
        self.state = IncidentWorkerState.FAILED
        self.last_error = reason
        self.last_failure = datetime.now(UTC)
        logger.error("[INCIDENT_WORKER] event=FAIL state=FAILED", error=reason)

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self, events: list[dict[str, Any]] | None = None) -> bool:
        """Runs one bounded correlation cycle if the interval has elapsed.

        ``events``: optional externally-supplied telemetry batch (log/DB
        evidence). When None, the worker uses its in-memory event backlog
        (producers push events explicitly via ingest()).
        """
        if self.state not in (IncidentWorkerState.RUNNING, IncidentWorkerState.DEGRADED):
            return False
        now = time.time()
        if now - self._last_run_ts < self.interval_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1
        started = time.perf_counter()
        try:
            useful = self._cycle_once(events or [])
            self.cycle_duration_ms = (time.perf_counter() - started) * 1000.0
            self._durations_ms.append(self.cycle_duration_ms)
            if len(self._durations_ms) > LATENCY_WINDOW:
                self._durations_ms = self._durations_ms[-LATENCY_WINDOW:]
            self.last_success = datetime.now(UTC)
            if useful:
                self.last_useful_work = datetime.now(UTC)
            self.last_error = ""
            self.consecutive_failures = 0
            if self.state == IncidentWorkerState.DEGRADED:
                self.state = IncidentWorkerState.RUNNING  # recovered
            return True
        except Exception as err:
            self.cycle_duration_ms = (time.perf_counter() - started) * 1000.0
            self.last_error = str(err)
            self.last_failure = datetime.now(UTC)
            self.consecutive_failures += 1
            if self.consecutive_failures >= 5:
                self.state = IncidentWorkerState.FAILED
            elif self.consecutive_failures >= 2:
                self.state = IncidentWorkerState.DEGRADED
            logger.error("[INCIDENT_WORKER] event=FAILURE", cycle=self.cycle_count, error=str(err))
            return False

    def ingest(self, telem: dict[str, Any]) -> None:
        """Queue-ish ingestion for producers (bounded ring).

        The worker correlates at the next tick; producers never block.
        """
        if not isinstance(telem, dict):
            return
        if len(self._backlog) >= MAX_BACKLOG:
            self._backlog = self._backlog[-(MAX_BACKLOG - 1) :]
            self.events_dropped += 1
        self._backlog.append(telem)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def backlog(self) -> list[dict[str, Any]]:
        return list(self._backlog)

    @property
    def queue_size(self) -> int:
        return len(self._backlog)

    def latency_percentiles(self) -> dict[str, float]:
        """Cycle latency p50/p95/p99 (ms) over the recent window."""
        if not self._durations_ms:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        d = sorted(self._durations_ms)
        n = len(d)

        def p(q):
            return float(d[min(n - 1, max(0, int(q * n)))])

        return {"p50": round(p(0.50), 2), "p95": round(p(0.95), 2), "p99": round(p(0.99), 2)}

    def _cycle_once(self, events: list[dict[str, Any]]) -> bool:
        """Runs one correlation pass. Returns True when useful work happened
        (events consumed and/or incidents created/updated)."""
        backlog = list(self._backlog) + list(events)
        self._backlog.clear()
        if not backlog:
            return False
        telemetry = [_to_telemetry(e) for e in backlog[:MAX_EVENTS_PER_CYCLE]]
        telemetry = [t for t in telemetry if t is not None]
        self.events_seen += len(telemetry)
        if len(backlog) > MAX_EVENTS_PER_CYCLE:
            self.events_dropped += len(backlog) - MAX_EVENTS_PER_CYCLE
        if not telemetry:
            return False
        existing = self.store.list_incidents(limit=200, ordered_by="last_seen_at")
        before = {i.incident_id for i in existing}
        result = self.correlator.correlate(telemetry, existing)
        saves = 0
        created = 0
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
            if inc.incident_id not in before:
                created += 1
            else:
                self.incidents_updated += 1
        self.incidents_created += created
        # Dedup accounting: events that merged into an existing incident
        # (result.merged) are deduplicated observations, not new incidents.
        self.incidents_deduplicated += result.merged + result.unchanged
        if self._telegram is not None:
            for inc in result.incidents:
                if inc.severity.value in ("CRITICAL", "HIGH") or (
                    inc.severity.value == "MEDIUM" and self._telegram_min_severity == "MEDIUM"
                ):
                    try:
                        self._telegram.maybe_alert(inc)
                    except Exception as tg_err:
                        logger.debug("[INCIDENT_WORKER] telegram alert failed", error=str(tg_err))
        if result.incidents:
            logger.info(
                "[INCIDENT_WORKER] event=CYCLE",
                cycle=self.cycle_count,
                events=len(telemetry),
                created=result.new,
                merged=result.merged,
                saved=saves,
            )
        return bool(telemetry) or created > 0


def _to_telemetry(raw: dict[str, Any]) -> TelemetryEvent | None:
    """Converts a producer dict into a TelemetryEvent (lenient)."""
    try:
        ts_raw = raw.get("timestamp")
        ts: datetime
        if isinstance(ts_raw, datetime):
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
        elif isinstance(ts_raw, (int, float)):
            # Unix milliseconds vs seconds (TEST-TIMEBASE-03/04): values above
            # ~1e12 are milliseconds (2026 epoch); values around 1e9 are
            # seconds. Ambiguous small values are treated as seconds.
            val = float(ts_raw)
            if val >= 1e12:
                val = val / 1000.0
            ts = datetime.fromtimestamp(val, UTC)
        elif isinstance(ts_raw, str):
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        else:
            ts = datetime.now(UTC)
        source = str(raw.get("source") or "TELEMETRY").upper()
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
            source=EventSource(source)
            if source in EventSource._value2member_map_
            else EventSource.TELEMETRY,
        )
    except Exception:
        return None


def format_incident_worker_status(worker: IncidentWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry for the REST layer (TASK-13 spec 7)."""
    return {
        "state": worker.state.value,
        "running": worker.state in (IncidentWorkerState.RUNNING, IncidentWorkerState.STARTING),
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "last_start": worker.last_start.isoformat() if worker.last_start else None,
        "last_success": worker.last_success.isoformat() if worker.last_success else None,
        "last_failure": worker.last_failure.isoformat() if worker.last_failure else None,
        "last_useful_work": worker.last_useful_work.isoformat()
        if worker.last_useful_work
        else None,
        "last_cycle_duration_ms": round(worker.cycle_duration_ms, 1)
        if worker.cycle_duration_ms
        else 0.0,
        "latency": worker.latency_percentiles(),
        "queue_size": worker.queue_size,
        "last_error": worker.last_error or "",
        "consecutive_failures": worker.consecutive_failures,
        "events_seen": worker.events_seen,
        "events_dropped": worker.events_dropped,
        "incidents_created": worker.incidents_created,
        "incidents_updated": worker.incidents_updated,
        "incidents_deduplicated": worker.incidents_deduplicated,
    }


__all__ = [
    "CYCLE_BUDGET_SEC",
    "DEFAULT_INTERVAL_SEC",
    "MAX_SAVES_PER_CYCLE",
    "IncidentWorker",
    "IncidentWorkerState",
    "format_incident_worker_status",
]
