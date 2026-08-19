"""Structured incident telemetry collector (TASK-13 STEP-02).

Bridges canonical runtime events into the IncidentWorker's telemetry stream.

Design principles:
- PREFER structured events over parsing human-readable log text (spec 8).
- Producers call `emit()` with a small typed dict; the collector normalizes
  it into the worker's ingestion format. Bounded, non-blocking, never on the
  tick path.
- The collector NEVER mutates trading state — it only observes.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.telemetry")

#: Bounded ring for events awaiting the next worker tick.
MAX_PENDING: int = 5000


class IncidentTelemetryCollector:
    """Thread-safe collector feeding one IncidentWorker.

    Usage (engine side):
        collector = IncidentTelemetryCollector(worker)
        collector.emit(event_type="MT5_CALL_FAILED", component="mt5",
                       error_code="MT5_CALL_FAILED", correlation_id=...,
                       ticket=..., severity="HIGH")
    """

    def __init__(
        self,
        worker: Any | None = None,
        *,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self.worker = worker
        self.max_pending = int(max_pending)
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self.emitted = 0
        self.dropped = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def attach(self, worker: Any) -> None:
        self.worker = worker

    def emit(
        self,
        *,
        event_type: str,
        component: str,
        error_code: str = "",
        correlation_id: str = "",
        ticket: str = "",
        execution_id: str = "",
        severity: str | None = None,
        timestamp: datetime | str | float | None = None,
        **payload: Any,
    ) -> bool:
        """Queues one structured telemetry event for the worker.

        Returns True when accepted, False when dropped (bounded ring).
        NEVER raises; never blocks the caller.
        """
        if timestamp is None:
            ts: Any = datetime.now(UTC)
        else:
            ts = timestamp
        event: dict[str, Any] = {
            "event_type": str(event_type),
            "component": str(component),
            "error_code": str(error_code),
            "correlation_id": str(correlation_id),
            "ticket": str(ticket),
            "execution_id": str(execution_id),
            "timestamp": ts,
        }
        if severity:
            event["severity"] = str(severity).upper()
        event.update({k: v for k, v in payload.items() if k not in event})
        with self._lock:
            if len(self._pending) >= self.max_pending:
                self.dropped += 1
                return False
            self._pending.append(event)
            self.emitted += 1
        # Opportunistic push when a worker is attached (never blocks).
        worker = self.worker
        if worker is not None and hasattr(worker, "ingest"):
            try:
                worker.ingest(event)
                with self._lock:
                    if event in self._pending:
                        self._pending.remove(event)
            except Exception as err:
                logger.debug("[INCIDENT_TELEMETRY] ingest failed (isolated)", error=str(err))
        return True

    def flush_to_worker(self) -> int:
        """Pushes any pending events to the worker (called at worker tick)."""
        worker = self.worker
        if worker is None:
            return 0
        with self._lock:
            pending = list(self._pending)
            self._pending.clear()
        pushed = 0
        for ev in pending:
            try:
                worker.ingest(ev)
                pushed += 1
            except Exception:
                break
        return pushed

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def stats(self) -> dict[str, Any]:
        return {
            "emitted": self.emitted,
            "dropped": self.dropped,
            "pending": self.pending_count(),
        }


# ---------------------------------------------------------------------------
# Canonical engine event -> telemetry mapping (spec 8)
# ---------------------------------------------------------------------------

#: Known engine error classes -> (error_code, severity) for structured events.
ENGINE_EVENT_MAP: dict[str, tuple[str, str]] = {
    "EXECUTION_RECONCILIATION_FAILED": ("SILENT_EXCEPTION", "HIGH"),
    "ORDER_REJECTED": ("ORDER_REJECTED", "MEDIUM"),
    "POSITION_CLOSE_FAILED": ("ORDER_REJECTED", "HIGH"),
    "MT5_CONNECT_FAILED": ("MT5_CALL_FAILED", "HIGH"),
    "MT5_CALL_FAILED": ("MT5_CALL_FAILED", "HIGH"),
    "DEAL_LOOKUP_FAILED": ("DEAL_LOOKUP_FAILED", "HIGH"),
    "BROKER_SYNC_FAILED": ("MT5_CALL_FAILED", "MEDIUM"),
    "ACCOUNTING_DIVERGENCE": ("ACCOUNTING_DIVERGENCE", "CRITICAL"),
    "LEDGER_WRITE_FAILED": ("SILENT_FINANCIAL_CORRUPTION", "CRITICAL"),
    "OUTCOME_DISCARDED": ("OUTCOME_DISCARDED", "HIGH"),
    "LEARNING_DATA_LOSS": ("LEARNING_DATA_LOSS", "HIGH"),
    "MODEL_INFERENCE_FAILED": ("MODEL_CONTRACT_MISMATCH", "HIGH"),
    "MODEL_LOAD_REJECTED": ("MODEL_CONTRACT_MISMATCH", "HIGH"),
    "FEATURE_INVALID": ("FEATURE_ALL_MISSING", "MEDIUM"),
    "NEWS_FETCH_FAILED": ("NEWS_SOURCE_EMPTY", "LOW"),
    "NEWS_PARSE_FAILED": ("NEWS_PARSER_FAILED", "MEDIUM"),
    "WORKER_STALLED": ("WORKER_STALLED", "MEDIUM"),
    "WORKER_ZERO_PROGRESS": ("WORKER_RUNNING_ZERO_PROGRESS", "MEDIUM"),
    "TELEGRAM_SEND_FAILED": ("TELEGRAM_SEND_FAILED", "LOW"),
    "TELEGRAM_CONFIG_ERROR": ("TELEGRAM_SILENT_FAILURE", "MEDIUM"),
    "TIMEBASE_DIVERGENCE": ("TIMEBASE_DIVERGENCE", "HIGH"),
    "CACHE_STALE": ("EXPOSURE_CACHE_STALE", "MEDIUM"),
    "MAX_EXPOSURE_FALSE_BLOCK": ("MAX_EXPOSURE_FALSE_BLOCK", "HIGH"),
    "MIGRATION_FAILED": ("MIGRATION_FAILED", "HIGH"),
    "SCHEMA_MISMATCH": ("SCHEMA_MISMATCH", "CRITICAL"),
    "SHADOW_ISOLATION_FAILURE": ("SHADOW_ISOLATION_FAILURE", "HIGH"),
    "VERSION_INCONSISTENCY": ("VERSION_INCONSISTENCY", "HIGH"),
}


def engine_event_to_telemetry(
    *,
    event_type: str,
    component: str,
    correlation_id: str = "",
    ticket: str = "",
    execution_id: str = "",
    detail: str = "",
    severity: str | None = None,
    timestamp: datetime | str | float | None = None,
) -> dict[str, Any]:
    """Maps a canonical engine event name into the worker's telemetry format.

    Falls back to the raw event type when the class is unknown (lenient).
    """
    known = ENGINE_EVENT_MAP.get(str(event_type).upper())
    error_code = known[0] if known else str(event_type).upper()
    sev = severity or (known[1] if known else "MEDIUM")
    out: dict[str, Any] = {
        "event_type": str(event_type).upper(),
        "component": str(component),
        "error_code": error_code,
        "correlation_id": str(correlation_id),
        "ticket": str(ticket),
        "execution_id": str(execution_id),
        "severity": sev,
        "timestamp": timestamp or datetime.now(UTC),
    }
    if detail:
        out["detail"] = str(detail)
    return out


__all__ = [
    "ENGINE_EVENT_MAP",
    "MAX_PENDING",
    "IncidentTelemetryCollector",
    "engine_event_to_telemetry",
]
