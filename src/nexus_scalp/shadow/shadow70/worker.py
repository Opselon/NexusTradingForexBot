"""70D Shadow Worker — bounded async persistence (TASK-05-70D-SHADOW).

Spec 17 / 18 / 39 / 40:

* shadow work never blocks the tick path (Champion continues normally);
* bounded queue with drop/coalesce (SHADOW_BACKPRESSURE telemetry);
* persistence is batched and asynchronous through the existing
  AuditRepository background writer (no synchronous DB on the tick path);
* bounded in-memory buffers (no unbounded historical accumulation).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.shadow70.models import Shadow70Observation
from nexus_scalp.shadow.shadow70.store import Shadow70Store

logger = get_logger("nexus_scalp.shadow.shadow70.worker")

DEFAULT_MAX_QUEUE: int = 2000
DEFAULT_BATCH_SIZE: int = 100
DEFAULT_FLUSH_INTERVAL_SEC: float = 5.0


@dataclass
class Shadow70QueueItem:
    """One queued observation (bounded; never unbounded growth)."""

    observation: Shadow70Observation
    enqueued_at: float = field(default_factory=time.time)


class Shadow70Worker:
    """Thread-based bounded writer: coalesces observations into batches.

    ``enqueue()`` is called from the tick path and NEVER blocks for more
    than a bounded put_nowait; a full queue drops the snapshot with
    SHADOW_BACKPRESSURE telemetry (spec 18).
    """

    def __init__(
        self,
        store: Shadow70Store,
        max_queue: int = DEFAULT_MAX_QUEUE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC,
    ) -> None:
        self.store = store
        self.max_queue = int(max_queue)
        self.batch_size = int(batch_size)
        self.flush_interval_sec = float(flush_interval_sec)
        import queue

        self._queue: queue.Queue[Shadow70QueueItem] = queue.Queue(maxsize=self.max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_observations: list[Shadow70Observation] = []
        self._pending_events: list[dict[str, Any]] = []
        self._pending_health: list[dict[str, Any]] = []
        self._pending_drift: list[dict[str, Any]] = []
        self._last_flush: float = time.time()
        self.enqueued: int = 0
        self.persisted: int = 0
        self.dropped: int = 0
        self.persist_errors: int = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="shadow70-writer", daemon=True)
        self._thread.start()

    def stop(self, flush: bool = True) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        if flush:
            self.flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
                self._pending_observations.append(item.observation)
                if len(self._pending_observations) >= self.batch_size:
                    self.flush()
            except Exception:
                # timeout / isolated: flush what we have periodically
                if time.time() - self._last_flush >= self.flush_interval_sec:
                    self.flush()

    # ------------------------------------------------------------------
    # Tick-path entry (bounded, non-blocking)
    # ------------------------------------------------------------------

    def enqueue(self, obs: Shadow70Observation) -> bool:
        """Non-blocking enqueue from the hot path (spec 17/18)."""
        try:
            if self._queue.qsize() >= self.max_queue:
                self.dropped += 1
                logger.warning(
                    "[SHADOW70] event=SHADOW_BACKPRESSURE",
                    dropped_snapshots=self.dropped,
                    queue_size=self._queue.qsize(),
                )
                return False
            self._queue.put_nowait(Shadow70QueueItem(observation=obs))
            self.enqueued += 1
            return True
        except Exception as e:
            self.dropped += 1
            logger.error("[SHADOW70] enqueue failed (isolated)", error=str(e))
            return False

    # ------------------------------------------------------------------
    # Batch persistence (off the hot path; queued writer)
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Persists pending batches through the AuditRepository queue."""
        with self._lock:
            obs = self._pending_observations
            evts = self._pending_events
            health = self._pending_health
            drift = self._pending_drift
            self._pending_observations = []
            self._pending_events = []
            self._pending_health = []
            self._pending_drift = []
            self._last_flush = time.time()
        if not (obs or evts or health or drift):
            return
        try:
            saved = 0
            for o in obs:
                if self.store.save_observation(o):
                    saved += 1
                else:
                    self.persist_errors += 1
            for e in evts:
                if not self.store.record_event(e):
                    self.persist_errors += 1
            if health and self.store.save_feature_health(health):
                pass
            if drift and self.store.save_drift_alerts(drift):
                pass
            self.persisted += saved
        except Exception as e:
            self.persist_errors += 1
            logger.error("[SHADOW70] flush failed (isolated)", error=str(e))

    def queue_health_callback(self, state: dict[str, Any]) -> None:
        """Optional: record a structured event for queue state changes."""
        self._pending_events.append(state)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "enqueued": self.enqueued,
            "persisted": self.persisted,
            "dropped": self.dropped,
            "persist_errors": self.persist_errors,
            "queue_size": self._queue.qsize(),
            "max_queue": self.max_queue,
            "running": self._thread is not None and self._thread.is_alive(),
            "last_flush_at": datetime.fromtimestamp(self._last_flush, UTC).isoformat(),
        }


def format_shadow70_status(worker: Shadow70Worker | None) -> dict[str, Any]:
    """Truthful worker status for the UI (never fake values)."""
    if worker is None:
        return {"available": False}
    return {"available": True, **worker.status()}
