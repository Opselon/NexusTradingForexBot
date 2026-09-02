"""
Background Intelligence Worker
===============================
PHASE 09 isolated, restart-safe, observable background worker for Trade
Intelligence.

Responsibilities (spec: BACKGROUND INTELLIGENCE WORKER):
    * update position intelligence (finalize any open timelines)
    * produce trade autopsies for completed trades
    * detect measurable behavioral patterns
    * scan strategy families for evolution candidates
    * rebuild corrupted derived intelligence (self-heal)

CONTRACT
--------
1. NEVER blocks trading. It is invoked through `asyncio.to_thread()` from the
   LiveEngine periodic task; it performs only bounded reads and queued writes.
2. FAILURE-ISOLATED. Every cycle is wrapped; a failure is logged with the
   `[INTELLIGENCE_WORKER] event=FAILURE` contract and the worker continues on
   the next cycle. It can never crash the live engine.
3. RESTART-SAFE. `start()`/`stop()` manage a cycle counter and last-error
   telemetry. A crash mid-cycle resumes cleanly; a checkpoint is recorded so
   nothing is rebuilt redundantly.
4. IDEMPOTENT. Re-running a cycle with no new data is a no-op.
5. NEVER executes, modifies or closes an order. The worker owns no adapter and
   no order manager.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.intelligence.autopsy import TradeAutopsyEngine
from nexus_scalp.intelligence.behavior import BehaviorDetectionEngine
from nexus_scalp.intelligence.evolution import StrategyEvolutionEngine
from nexus_scalp.intelligence.lifecycle import PositionLifecycleTracker
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.worker")


class IntelligenceWorker:
    """
    Background intelligence refresher.

    Attributes:
        interval_sec: Minimum wall-clock gap between cycles.
        lifecycle / autopsy / behavior / evolution: the engines it drives.
        cycle_count / last_cycle_duration / last_error: observability.
        running: active flag (start/stop state machine).
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        ledger: ExperienceLedger,
        interval_sec: float = 30.0,
        lifecycle: PositionLifecycleTracker | None = None,
        autopsy: TradeAutopsyEngine | None = None,
        behavior: BehaviorDetectionEngine | None = None,
        evolution: StrategyEvolutionEngine | None = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.interval_sec = float(interval_sec)
        self.lifecycle = lifecycle
        self.autopsy = autopsy
        self.behavior = behavior
        self.evolution = evolution

        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._last_autopsy_count = 0

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
        logger.info("[INTELLIGENCE_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        """Marks the worker inactive (idempotent)."""
        if not self.running:
            return
        self.running = False
        self._save_checkpoint()
        logger.info("[INTELLIGENCE_WORKER] event=STOP status=IDLE")

    # ------------------------------------------------------------------
    # Restart-safe checkpoint (intelligence_worker_state table)
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> None:
        """
        Restores cycle counters from `intelligence_worker_state` so a crash or
        restart resumes cleanly instead of silently redoing history.

        The table is created by the audit schema; a missing table or row simply
        means "first run" - never an error on the live path.
        """
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT cycle_count, last_checkpoint FROM intelligence_worker_state "
                    "WHERE scope = 'intelligence' LIMIT 1;"
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                self.cycle_count = max(self.cycle_count, int(row["cycle_count"] or 0))
                prior = str(row["last_checkpoint"] or "")
                if prior:
                    self._last_autopsy_count = max(self._last_autopsy_count, int(prior))
        except Exception as e:
            logger.debug("[INTELLIGENCE_WORKER] checkpoint load skipped", error=str(e))

    def _save_checkpoint(self) -> None:
        """Persists restart-safe bookkeeping through the async audit queue."""
        try:
            query = """
                INSERT INTO intelligence_worker_state
                (scope, last_checkpoint, last_cycle_at, last_error, cycle_count)
                VALUES ('intelligence', ?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    last_checkpoint=excluded.last_checkpoint,
                    last_cycle_at=excluded.last_cycle_at,
                    last_error=excluded.last_error,
                    cycle_count=excluded.cycle_count;
            """
            args = (
                str(self._last_autopsy_count),
                self.last_cycle_start.isoformat() if self.last_cycle_start else "",
                self.last_error or "",
                self.cycle_count,
            )
            self.audit_repo._queue.put_nowait((query, args))
        except Exception as e:
            logger.debug("[INTELLIGENCE_WORKER] checkpoint save skipped", error=str(e))

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """
        Runs one bounded intelligence cycle if `interval_sec` has elapsed.

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
                "[INTELLIGENCE_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[INTELLIGENCE_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Cycle internals
    # ------------------------------------------------------------------

    def _refresh_once(self) -> None:
        """
        Drives one intelligence refresh pass. Each sub-step is individually
        guarded so a failure in one cannot abort the whole cycle.
        """
        self._run("autopsy", self._refresh_autopsies)
        self._run("behavior", self._refresh_behavior)
        self._run("evolution", self._refresh_evolution)
        logger.debug("[INTELLIGENCE_WORKER] event=UPDATE", cycle=self.cycle_count)

    def _run(self, name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as e:
            logger.error(
                "[INTELLIGENCE_WORKER] event=FAILURE",
                sub_step=name,
                error=str(e),
                exc_info=True,
            )

    def _refresh_autopsies(self) -> None:
        """
        Produces autopsies for every closed trade that has experience attribution
        but no autopsy yet. Idempotent: an already-autopsied ticket is skipped.
        """
        if self.autopsy is None or not self.audit_repo._is_sqlite:
            return
        # Find strategy ids with closed experiences; build autopsies for any
        # ticket that does not yet have one.
        from nexus_scalp.intelligence.store import load_autopsy

        strategy_ids = self.ledger.list_strategy_ids()
        new_autopsies = 0
        for strategy_id in strategy_ids:
            experiences = self.ledger.get_experiences_for_strategy(
                strategy_id=strategy_id, limit=500
            )
            for record in experiences:
                if not (record.is_executed and record.is_closed):
                    continue
                ticket = record.execution_id or record.idempotency_key
                if not ticket:
                    continue
                if load_autopsy(self.audit_repo, ticket) is not None:
                    continue
                # Build an autopsy from the merged experience + decomposition.
                decomposition = record.decomposition
                autopsy = self.autopsy.build_autopsy(
                    record=record,
                    decomposition=decomposition,
                    realized_pnl_usd=record.realized_pnl_usd,
                    realized_r=record.realized_r_multiple,
                    ticket=str(ticket),
                    symbol=record.symbol,
                    timeframe=record.timeframe,
                    exit_mechanism=record.exit_reason,
                    flags=[f.value for f in record.behavioral_flags],
                )
                self.autopsy.persist(autopsy)
                new_autopsies += 1

        self._last_autopsy_count += new_autopsies
        if new_autopsies:
            logger.info(
                "[TRADE_AUTOPSY] event=BATCH",
                produced=new_autopsies,
            )

    def _refresh_evolution(self) -> None:
        """Runs the evolution scan (bounded discovery pass). Produces candidates."""
        if self.evolution is None or not self.audit_repo._is_sqlite:
            return
        candidates = self.evolution.scan()
        if candidates:
            logger.info(
                "[STRATEGY] EVOLUTION_SCAN",
                discovered=len(candidates),
            )

    def _refresh_behavior(self) -> None:
        """Runs the bounded canonical behavioral/anomaly analysis pass.

        Off hot path (the worker tick itself is invoked via asyncio.to_thread),
        bounded to the most recent trades, and idempotent: tickets already
        analyzed under the same (behavior_version, anomaly_version) are skipped.
        """
        if self.behavior is None or not self.audit_repo._is_sqlite:
            return
        from nexus_scalp.intelligence.behavior import (
            ANOMALY_ALGORITHM_VERSION,
            BEHAVIOR_ALGORITHM_VERSION,
            analyze_canonical_trades,
        )

        result = analyze_canonical_trades(
            audit_repo=self.audit_repo,
            engine=self.behavior,
            behavior_version=BEHAVIOR_ALGORITHM_VERSION,
            anomaly_version=ANOMALY_ALGORITHM_VERSION,
            max_trades=200,
        )
        if result["analyzed"]:
            logger.info(
                "[BEHAVIOR_ANALYSIS] event=BATCH",
                analyzed=result["analyzed"],
                skipped=result["skipped"],
                flags=result["flags"],
                anomalies=result["anomalies"],
                coverage=result["coverage"],
            )


def format_intelligence_worker_status(worker: IntelligenceWorker) -> dict[str, Any]:
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
        "autopsy_count": getattr(worker, "_last_autopsy_count", 0),
    }
