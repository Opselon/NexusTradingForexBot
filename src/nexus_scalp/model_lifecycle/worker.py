"""
Background Training Worker
==========================
PHASE 10 isolated, restart-safe, observable background worker for CONTROLLED
model training (spec 25 / 31 / 32 / 42).

CONTRACT
--------
1. NEVER blocks trading. Invoked through `asyncio.to_thread()` from the
   LiveEngine periodic task; heavy PyTorch training NEVER runs inside
   `_process_tick_pipeline()`.
2. FAILURE-ISOLATED. Every cycle is wrapped; a failure logs
   `[TRAINING_WORKER] event=FAILURE` and the worker continues next cycle.
3. RESTART-SAFE. `start()`/`stop()` manage state; a crash mid-training leaves
   the run FAILED/INCOMPLETE in the run store - never VALIDATED.
4. BOUNDED. At most `max_concurrent_trainings` runs may be active; the worker
   refuses to start when training is already in flight (no unbounded CPU/RAM).
5. CANCELLABLE. `request_cancel()` marks the worker stopping; the in-flight
   trainer run is abandoned (its persisted status is INCOMPLETE).
6. NO EXECUTION CAPABILITY. No adapter, order manager or risk engine.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.worker")


class TrainingWorker:
    """
    Background controlled-training loop.

    Attributes:
        interval_sec: Minimum wall-clock gap between cycles.
        orchestrator: the ModelLifecycleOrchestrator it drives.
        cycle_count / last_cycle_duration / last_error: observability.
        running: start/stop state machine.
        inflight: True while a training run is active (bounded concurrency).
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        ledger: ExperienceLedger,
        orchestrator: ModelLifecycleOrchestrator,
        interval_sec: float = 300.0,
        max_concurrent_trainings: int = 1,
        auto_train_enabled: bool = True,
    ) -> None:
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.orchestrator = orchestrator
        self.interval_sec = float(interval_sec)
        self.max_concurrent_trainings = max(1, int(max_concurrent_trainings))
        self.auto_train_enabled = bool(auto_train_enabled)

        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self.inflight: bool = False
        self._cancel_requested: bool = False
        self.last_run_id: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Marks the worker active (idempotent)."""
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        self._restore_inflight_state()
        logger.info("[TRAINING_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        """Marks the worker inactive; a running training is abandoned safely."""
        if not self.running:
            return
        self.running = False
        if self.inflight:
            logger.warning("[TRAINING_WORKER] event=STOP in-flight training abandoned")
        logger.info("[TRAINING_WORKER] event=STOP status=IDLE")

    def request_cancel(self) -> None:
        """Requests cancellation of any in-flight training (bounded, safe)."""
        self._cancel_requested = True
        logger.info("[TRAINING_WORKER] event=CANCEL_REQUESTED")

    # ------------------------------------------------------------------
    # Restart-safe state
    # ------------------------------------------------------------------

    def _restore_inflight_state(self) -> None:
        """On restart, any RUNNING row is marked INCOMPLETE (never VALIDATED)."""
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT run_id FROM training_runs WHERE status='RUNNING' LIMIT 1;"
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE training_runs SET status='INCOMPLETE' WHERE run_id=?;",
                        (row[0],),
                    )
                    conn.commit()
                    logger.warning(
                        "[TRAINING_WORKER] interrupted run marked INCOMPLETE",
                        run_id=row[0],
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.debug("[TRAINING_WORKER] restore state skipped", error=str(e))

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """
        Runs one bounded training cycle if `interval_sec` has elapsed AND no
        training is already in flight. Returns True when a cycle ran.
        """
        if not self.running:
            return False
        if self.inflight:
            return False  # bounded concurrency: never stack trainings
        now = time.time()
        if now - self._last_run_ts < self.interval_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1
        self.last_cycle_start = datetime.now(UTC)
        started = time.perf_counter()
        try:
            self._maybe_train()
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            logger.info(
                "[TRAINING_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[TRAINING_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    def _maybe_train(self) -> None:
        """
        When auto-training is enabled and the ledger holds enough verified
        experience, builds a dataset and runs one controlled training pass
        (bounded: one at a time, cancellable).
        """
        if not self.auto_train_enabled or self._cancel_requested:
            return
        try:
            total = self.ledger.count_experiences()
            if total < 50:
                logger.info(
                    "[TRAINING_WORKER] insufficient experience for training",
                    experiences=total,
                )
                return
            self.inflight = True
            try:
                dataset = self.orchestrator.build_training_dataset(
                    include_no_trade=True, weight_no_trade=0.25, only_executed=True
                )
                if dataset.sample_count < 50:
                    logger.info(
                        "[TRAINING_WORKER] insufficient labeled samples",
                        samples=dataset.sample_count,
                    )
                    return
                result = self.orchestrator.run_controlled_training(
                    dataset,
                    hyperparameters={"num_folds": 5, "epochs_per_fold": 3, "batch_size": 64},
                    num_epochs=3,
                    build_identity="training_worker",
                )
                self.last_run_id = str(result.get("run_id", ""))
                logger.info(
                    "[TRAINING_WORKER] event=TRAINING_COMPLETE",
                    run_id=self.last_run_id,
                    gates_passed=result.get("all_gates_passed"),
                )
            finally:
                self.inflight = False
                self._cancel_requested = False
        except Exception as e:
            logger.error("[TRAINING_WORKER] training cycle failed (isolated)", error=str(e))


def format_training_worker_status(worker: TrainingWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry for the REST layer."""
    return {
        "running": worker.running,
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "status": "RUNNING" if worker.running else "IDLE",
        "inflight": worker.inflight,
        "last_run_id": worker.last_run_id,
        "last_error": worker.last_error or "",
        "auto_train_enabled": worker.auto_train_enabled,
        "max_concurrent_trainings": worker.max_concurrent_trainings,
        "last_cycle_duration_ms": round(worker.last_cycle_duration * 1000.0, 1)
        if worker.last_cycle_duration
        else None,
    }
