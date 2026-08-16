"""
Background Research Worker
==========================
PHASE 09B isolated, restart-safe, observable background worker for the
Strategy Research / Validation pipeline (spec 31 / 32 / 42).

Responsibilities:
    * rebuild the research dataset from the immutable ledger
    * run candidate discovery (bounded)
    * validate new candidates through the full gate chain (backtest ->
      walk-forward -> OOS -> robustness -> score -> registry)

CONTRACT
--------
1. NEVER blocks trading. Invoked through `asyncio.to_thread()` from the
   LiveEngine periodic task; only bounded reads and queued writes.
2. FAILURE-ISOLATED. Every cycle is wrapped; a failure is logged with the
   `[RESEARCH_WORKER] event=FAILURE` contract and the worker continues next
   cycle. It can never crash the live engine.
3. RESTART-SAFE. `start()`/`stop()` manage a cycle counter and persisted
   checkpoint (`research_worker_state`).
4. IDEMPOTENT. Re-running a cycle with no new data is a no-op.
5. NEVER executes, modifies or closes an order. No adapter, no order manager,
   no risk engine.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.pipeline import ResearchPipeline

logger = get_logger("nexus_scalp.research.worker")

#: Max candidates validated per cycle (bounded, cancellable).
MAX_VALIDATIONS_PER_CYCLE: int = 5


class ResearchWorker:
    """
    Background research refresher driving the full validation pipeline.

    Attributes:
        interval_sec: Minimum wall-clock gap between cycles.
        pipeline: the ResearchPipeline it drives.
        cycle_count / last_cycle_duration / last_error: observability.
        running: start/stop state machine.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        ledger: ExperienceLedger,
        pipeline: ResearchPipeline,
        interval_sec: float = 60.0,
        max_validations_per_cycle: int = MAX_VALIDATIONS_PER_CYCLE,
    ) -> None:
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.pipeline = pipeline
        self.interval_sec = float(interval_sec)
        self.max_validations_per_cycle = int(max_validations_per_cycle)

        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._validated_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Marks the worker active (idempotent)."""
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        self._load_checkpoint()
        logger.info("[RESEARCH_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        """Marks the worker inactive (idempotent) and persists checkpoint."""
        if not self.running:
            return
        self.running = False
        self._save_checkpoint()
        logger.info("[RESEARCH_WORKER] event=STOP status=IDLE")

    # ------------------------------------------------------------------
    # Restart-safe checkpoint
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> None:
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT cycle_count, last_checkpoint FROM research_worker_state "
                    "WHERE scope = 'research' LIMIT 1;"
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                self.cycle_count = max(self.cycle_count, int(row["cycle_count"] or 0))
                prior = str(row["last_checkpoint"] or "")
                if prior:
                    self._validated_count = max(self._validated_count, int(prior))
        except Exception as e:
            logger.debug("[RESEARCH_WORKER] checkpoint load skipped", error=str(e))

    def _save_checkpoint(self) -> None:
        try:
            query = """
                INSERT INTO research_worker_state
                (scope, last_checkpoint, last_cycle_at, last_error, cycle_count)
                VALUES ('research', ?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    last_checkpoint=excluded.last_checkpoint,
                    last_cycle_at=excluded.last_cycle_at,
                    last_error=excluded.last_error,
                    cycle_count=excluded.cycle_count;
            """
            args = (
                str(self._validated_count),
                self.last_cycle_start.isoformat() if self.last_cycle_start else "",
                self.last_error or "",
                self.cycle_count,
            )
            self.audit_repo._queue.put_nowait((query, args))
        except Exception as e:
            logger.debug("[RESEARCH_WORKER] checkpoint save skipped", error=str(e))

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """
        Runs one bounded research cycle if `interval_sec` has elapsed.

        Returns True when a cycle actually ran, False when throttled.
        Fully exception-isolated: failure is logged, never propagated.
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
            self._refresh_once()
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            logger.info(
                "[RESEARCH_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[RESEARCH_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Cycle internals (each step isolated)
    # ------------------------------------------------------------------

    def _refresh_once(self) -> None:
        self._run("dataset", self._refresh_dataset)
        self._run("discovery", self._refresh_discovery)
        self._run("validation", self._refresh_validation)
        logger.debug("[RESEARCH_WORKER] event=UPDATE", cycle=self.cycle_count)

    def _run(self, name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as e:
            logger.error(
                "[RESEARCH_WORKER] event=FAILURE",
                sub_step=name,
                error=str(e),
                exc_info=True,
            )

    def _refresh_dataset(self) -> None:
        """Rebuilds the research dataset from the immutable ledger (derived state)."""
        dataset = self.pipeline.dataset_builder.build()
        self._dataset = dataset
        logger.info(
            "[STRATEGY_RESEARCH] event=DATASET_REBUILT",
            dataset_id=dataset.dataset_id,
            samples=len(dataset.samples),
        )

    def _refresh_discovery(self) -> None:
        """Runs bounded discovery; candidates never touch live trading."""
        dataset = getattr(self, "_dataset", None)
        if dataset is None:
            dataset = self.pipeline.dataset_builder.build()
            self._dataset = dataset
        candidates = self.pipeline.discover(dataset)
        self._candidates = candidates
        if candidates:
            logger.info(
                "[STRATEGY_RESEARCH] event=CANDIDATE_DISCOVERED",
                count=len(candidates),
            )

    def _refresh_validation(self) -> None:
        """
        Validates newly-discovered candidates through the full gate chain,
        bounded per cycle. Registered results never become LIVE automatically.
        """
        candidates = getattr(self, "_candidates", []) or []
        if not candidates:
            return
        for candidate in candidates[: self.max_validations_per_cycle]:
            try:
                self.pipeline.validate_candidate(
                    candidate,
                    getattr(self, "_dataset", self.pipeline.dataset_builder.build()),
                )
                self._validated_count += 1
            except Exception as e:
                logger.error(
                    "[STRATEGY_VALIDATION] event=FAILURE",
                    strategy_id=candidate.strategy_id,
                    error=str(e),
                    exc_info=True,
                )
        self._candidates = []


def format_research_worker_status(worker: ResearchWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry for the REST layer."""
    return {
        "running": worker.running,
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "last_cycle_start": worker.last_cycle_start.isoformat()
        if worker.last_cycle_start
        else None,
        "last_cycle_duration_ms": round(worker.last_cycle_duration * 1000.0, 1)
        if worker.last_cycle_duration
        else None,
        "last_error": worker.last_error or "",
        "status": "RUNNING" if worker.running else "IDLE",
        "validated_count": getattr(worker, "_validated_count", 0),
        "dataset_id": getattr(worker, "_dataset", None)
        and getattr(getattr(worker, "_dataset", None), "dataset_id", ""),
    }
