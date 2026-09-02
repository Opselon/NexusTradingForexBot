"""
Dedicated Accounting & Performance Worker
==========================================
Background derived-aggregation worker for the Accounting Core.

WHY IT EXISTS (spec section 17-18)
----------------------------------
The canonical accounting core reads authoritative records on demand. This
worker keeps the DERIVED view (period reports, curves, drawdown, strategy
contributions) warm and incremental so a dashboard refresh never triggers a
full-history recompute, and so Experience Intelligence always sees fresh
attribution.

HARD RULES
----------
1. NEVER touches the trading hot path. It is invoked through
   `asyncio.to_thread()` from LiveEngine's periodic task; it performs only
   bounded reads and in-process cache updates.
2. NEVER writes financial truth. It owns NO tables: raw snapshots, ledger rows
   and outcomes are written exclusively by the existing audit queue worker.
   Its only side effect is the in-process derived-report cache of
   `AccountingCore`, which is rebuildable at any time.
3. IDEMPOTENT. Re-running a cycle with no new data is a no-op. No duplicate
   financial records can be created because none are written.
4. FAILURE-ISOLATED. Every cycle is wrapped; a failure is logged with the
   [ACCOUNTING_WORKER] event=FAILURE contract and the worker continues on the
   next tick. It can never crash LiveEngine.
5. RESTARTABLE / DETERMINISTIC. `start()`/`stop()` manage a cycle counter and
   last-cycle telemetry; repeated restarts resume cleanly.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from nexus_scalp.accounting.core import AccountingCore
from nexus_scalp.accounting.periods import PeriodKind, utc_now
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.accounting.worker")


class AccountingWorker:
    """
    Background accounting/performance refresher.

    Attributes:
        core: The canonical accounting facade this worker refreshes.
        interval_sec: Minimum wall-clock gap between cycles.
        lookback_days: Snapshot window used for curves/drawdown refreshes.
        last_cycle_start / last_cycle_duration / last_error: observability.
        cycle_count: Monotonic restart-safe cycle counter.
    """

    def __init__(
        self,
        core: AccountingCore,
        interval_sec: float = 30.0,
        lookback_days: int = 90,
    ) -> None:
        self.core = core
        self.interval_sec = float(interval_sec)
        self.lookback_days = int(lookback_days)
        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._last_trade_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Marks the worker active (idempotent)."""
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        logger.info("[ACCOUNTING_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        """Marks the worker inactive (idempotent)."""
        if not self.running:
            return
        self.running = False
        logger.info("[ACCOUNTING_WORKER] event=STOP status=IDLE")

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        """
        Runs one bounded refresh cycle if `interval_sec` has elapsed.

        Returns:
            True when a cycle actually ran, False when throttled.

        Safe to call from any thread; all reads open short-lived read-only
        SQLite connections.
        """
        if not self.running:
            return False
        now = time.time()
        if now - self._last_run_ts < self.interval_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1
        self.last_cycle_start = utc_now()
        started = time.perf_counter()
        try:
            self._refresh_once()
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            logger.info(
                "[ACCOUNTING_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:  # failure isolation: never propagates
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[ACCOUNTING_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Refresh internals
    # ------------------------------------------------------------------

    def _refresh_once(self) -> None:
        """
        Refreshes the derived view incrementally:

          1. live account state (broker adapter)
          2. current DAY/WEEK/MONTH/YEAR period reports
          3. a bounded recent period series for chart history
          4. drawdown report + equity curve envelope
          5. strategy contributions (attribution joined to Intelligence)
          6. cumulative PnL curve

        Every consumer (dashboard, API, forensics) reads the same refreshed
        objects, so no consumer can disagree with another.
        """
        core = self.core

        # 1. Live state (never cached long; just warms the adapter read path).
        core.live_state()

        # 2. Current-period reports, all four granularities.
        periods_updated: list[str] = []
        now = utc_now()
        for kind in PeriodKind:
            report = core.period_report(kind, at=now, use_cache=False)
            if report.has_data:
                periods_updated.append(kind.value)

        # 3. Bounded history series (oldest -> newest) for charts.
        for kind in (PeriodKind.DAY, PeriodKind.WEEK, PeriodKind.MONTH, PeriodKind.YEAR):
            core.period_series(kind, count=min(30, _SERIES_COUNT[kind]), at=now)

        # 4. Drawdown + equity curve (bounded window).
        core.drawdown_report(lookback_days=self.lookback_days)
        core.equity_curve(lookback_days=self.lookback_days)

        # 5. Strategy attribution (joined to the strategy registry).
        core.strategy_contributions(limit=50)

        # 6. Cumulative realized PnL curve (bounded, rebuildable).
        core.cumulative_pnl_curve(limit=500)

        logger.debug(
            "[ACCOUNTING_WORKER] event=UPDATE",
            account="live",
            periods_updated=",".join(periods_updated) or "NONE",
        )


#: How many past periods each granularity keeps warm for chart history.
_SERIES_COUNT: dict[PeriodKind, int] = {
    PeriodKind.DAY: 30,
    PeriodKind.WEEK: 12,
    PeriodKind.MONTH: 12,
    PeriodKind.YEAR: 3,
}


def format_worker_status(worker: AccountingWorker) -> dict[str, Any]:
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
    }
