"""
Background Shadow Worker
========================
PHASE 11 isolated, restart-safe, cancellable worker for shadow evaluation
(spec 16 / 17 / 33).

CONTRACT
--------
1. NEVER blocks trading: invoked through asyncio.to_thread() from the
   LiveEngine periodic task; heavy aggregation/comparison NEVER runs inside
   `_process_tick_pipeline()`.
2. FAILURE-ISOLATED: every cycle is wrapped; a failure logs
   `[SHADOW_WORKER] event=FAILURE` and the worker continues; the Champion is
   never affected.
3. RESTART-SAFE: `start()`/`stop()` manage state; a crash mid-cycle resumes
   cleanly, and any RUNNING shadow run is marked INCOMPLETE on restart.
4. CANCELLABLE: `request_cancel()` halts the next aggregation cycle.
5. BOUNDED CPU/memory: aggregation runs on bounded in-memory slices.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.engine import ShadowEngine

logger = get_logger("nexus_scalp.shadow.worker")


class ShadowWorker:
    """
    Background shadow-aggregation loop.

    The worker does NOT record per-tick decisions (that happens on the live
    path via ShadowEngine, which is bounded and non-blocking). This worker
    periodically FINALISES the active shadow run into a persisted comparison.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        engine: ShadowEngine,
        interval_sec: float = 300.0,
        finalize_after_decisions: int = 30,
    ) -> None:
        self.audit_repo = audit_repo
        self.engine = engine
        self.interval_sec = float(interval_sec)
        self.finalize_after_decisions = int(finalize_after_decisions)

        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._cancel_requested: bool = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        self._mark_interrupted_runs()
        logger.info("[SHADOW_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        logger.info("[SHADOW_WORKER] event=STOP status=IDLE")

    def request_cancel(self) -> None:
        self._cancel_requested = True
        logger.info("[SHADOW_WORKER] event=CANCEL_REQUESTED")

    def _mark_interrupted_runs(self) -> None:
        """Restart safety: any RUNNING shadow run becomes INCOMPLETE."""
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT run_id FROM shadow_runs WHERE status='RUNNING' LIMIT 1;"
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE shadow_runs SET status='INCOMPLETE' WHERE run_id=?;",
                        (row[0],),
                    )
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("[SHADOW_WORKER] interrupted-run mark skipped", error=str(e))

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
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
            self._maybe_finalize()
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            logger.info(
                "[SHADOW_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[SHADOW_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    def _maybe_finalize(self) -> None:
        """Finalises the active shadow run when it has enough decisions."""
        if self._cancel_requested:
            self._cancel_requested = False
            return
        engine = self.engine
        if not engine.active_run_id:
            return
        if len(engine._decisions) >= self.finalize_after_decisions:
            engine.finish_run()
            logger.info(
                "[SHADOW] event=FINALIZED",
                run_id=engine.active_run_id or "",
                decisions=self.finalize_after_decisions,
            )


def format_shadow_worker_status(worker: ShadowWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry."""
    return {
        "running": worker.running,
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "status": "RUNNING" if worker.running else "IDLE",
        "last_error": worker.last_error or "",
        "active_run_id": worker.engine.active_run_id,
        "active_decisions": len(worker.engine._decisions),
        "challenger_loaded": worker.engine.active_challenger is not None,
        "last_cycle_duration_ms": round(worker.last_cycle_duration * 1000.0, 1)
        if worker.last_cycle_duration
        else None,
    }
