"""Background broker-history sync worker (bounded, throttled, restart-safe).

Responsibilities: fetch MT5 history_orders_get / history_deals_get over a
watermark window (+overlap), normalize, deduplicate, persist into the
audit_broker_* tables, then refresh accounting. NEVER runs on the tick hot
path — only via asyncio.to_thread from the run loop kick.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.broker_history_sync")


class BrokerHistoryFetchError(RuntimeError):
    """Raised when a zero-row history window is NOT a genuinely-empty window.

    MT5-PARITY-FIX-2 (F17-3): the port answers ``[]`` for both "no history in
    this window" and "broker/transport unreachable" (F17-4). Raising here lets
    ``BrokerHistorySyncWorker.tick`` route the cycle to its existing
    SYNC_FAILED branch (already isolated, retried on the next interval) while
    leaving the watermark untouched. It is deliberately a RuntimeError
    subclass so the existing ``except Exception`` handler still catches it and
    the worker keeps running.
    """


#: Overlap window protects against late/out-of-order broker records.
OVERLAP_DAYS = 1
#: First sync looks back this far (bounded, read-only).
INITIAL_DAYS = 14


class BrokerHistorySyncWorker:
    """Throttled background history synchronizer (never on the hot path)."""

    def __init__(
        self,
        audit: Any,
        adapter: Any,
        symbol: str = "XAUUSD",
        interval_sec: float = 300.0,
        initial_days: int = INITIAL_DAYS,
        overlap_days: int = OVERLAP_DAYS,
    ) -> None:
        self.audit = audit
        self.adapter = adapter
        self.symbol = symbol
        self.interval_sec = float(interval_sec)
        self.initial_days = int(initial_days)
        self.overlap_days = int(overlap_days)
        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self.last_result: dict[str, Any] = {}
        self._last_run_ts: float = 0.0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._last_run_ts = 0.0
        logger.info("[ACCOUNT_HISTORY] event=SYNC_START status=RUNNING")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        logger.info("[ACCOUNT_HISTORY] event=SYNC_STOP status=IDLE")

    def tick(self) -> bool:
        """One bounded sync cycle if interval elapsed; True when a cycle ran."""
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
            self.last_result = self._sync_once()
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            logger.info(
                "[ACCOUNT_HISTORY] event=SYNC_COMPLETE",
                cycle=self.cycle_count,
                orders=self.last_result.get("orders_total", 0),
                deals=self.last_result.get("deals_total", 0),
                trades=self.last_result.get("trades_total", 0),
                inserted=self.last_result.get("deals_inserted", 0)
                + self.last_result.get("orders_inserted", 0),
                duplicates=self.last_result.get("deals_duplicates", 0)
                + self.last_result.get("orders_duplicates", 0),
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            self._warm_accounting()
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[ACCOUNT_HISTORY] event=SYNC_FAILED",
                cycle=self.cycle_count,
                error=str(err),
                retry_in=self.interval_sec,
                exc_info=True,
            )
            return False

    def _fetch_is_available(self) -> bool:
        """True when the broker/transport can actually be queried right now.

        MT5-PARITY-FIX-2 / F17-3: the port's history readers answer ``[]`` for
        BOTH "no rows in this window" and "broker/transport is down"
        (mt5_adapter.py:822-825,927-928 — F17-4). Syncing a zero-row window
        therefore advanced ``last_sync_to`` through an outage, so any outage
        longer than OVERLAP_DAYS (or a gap in the broker response itself)
        became a PERMANENT hole while the watermark claimed full coverage.
        This probe separates failure from genuine emptiness before the worker
        is allowed to mark the window as covered. It is intentionally
        read-only and cheap (a connected-terminal check, no state mutation).
        """
        probe = getattr(self.adapter, "is_connected", None)
        if probe is None:
            # MT5-PARITY-FIX-2 / F17-3: adapters that do not publish ANY
            # connectivity signal cannot distinguish "broker down" from "no
            # history", so an empty window stays unsafe and the watermark is
            # not advanced (fail closed — a quiet window is the cheap case to
            # re-fetch; a silent hole is the expensive one to discover). Tests
            # that only want to assert window arithmetic set the probe.
            return False
        try:
            ok = probe() if callable(probe) else bool(probe)
        except Exception as exc:
            logger.debug(
                "[ACCOUNT_HISTORY] connectivity probe raised; treating as unavailable",
                error=str(exc),
            )
            return False
        return bool(ok)

    def _sync_once(self) -> dict[str, Any]:
        """Fetches + persists one bounded history window (idempotent)."""
        meta = None
        try:
            meta = self.audit.get_broker_history_meta(self.symbol)
        except Exception:
            meta = None

        to_dt = datetime.now(UTC)
        if meta and meta.get("last_sync_to"):
            # Anchor on the last COMPLETED sync (inclusive watermark), not the
            # first-ever window start: fetching from last_sync_to - overlap is
            # the only correct incremental window. Using last_sync_from made
            # every cycle re-fetch months and (combined with the meta upsert
            # writing last_sync_from=excluded.last_sync_from) regressed the
            # watermark, leaving newly-closed trades NEVER syncable until a
            # full historical reset.
            from_dt = datetime.fromisoformat(meta["last_sync_to"])
            if from_dt.tzinfo is None:
                from_dt = from_dt.replace(tzinfo=UTC)
            from_dt = from_dt - timedelta(days=self.overlap_days)
        elif meta and meta.get("last_sync_from"):
            # Legacy row without a completed-to watermark: full re-fetch.
            from_dt = datetime.fromisoformat(meta["last_sync_from"])
            if from_dt.tzinfo is None:
                from_dt = from_dt.replace(tzinfo=UTC)
            from_dt = from_dt - timedelta(days=self.overlap_days)
        else:
            from_dt = to_dt - timedelta(days=self.initial_days)

        logger.info(
            "[MT5_HISTORY] event=FETCH",
            from_=from_dt.isoformat(),
            to=to_dt.isoformat(),
        )
        orders = self.adapter.get_history_orders(from_dt, to_dt, symbol=self.symbol)
        deals = self.adapter.get_history_deals(from_dt, to_dt, symbol=self.symbol)
        logger.info(
            "[MT5_HISTORY] event=FETCH_RESULT",
            orders=len(orders),
            deals=len(deals),
        )

        # MT5-PARITY-FIX-2 / F17-3: [] cannot be trusted as "no history". The
        # port answers [] for a quiet window and for a dead transport alike
        # (F17-4), and persisting that as progress advanced last_sync_to past
        # an outage — a silent permanent data hole. Only an empty window from a
        # REACHABLE broker is genuinely empty. When [] and the broker is NOT
        # reachable, refuse to advance the watermark (leave it untouched) and
        # surface the cycle as a failure so the next tick re-fetches the
        # window in full.
        if not orders and not deals and not self._fetch_is_available():
            raise BrokerHistoryFetchError(
                f"broker history returned 0 rows for a window it could not be "
                f"queried from [{from_dt.isoformat()} .. {to_dt.isoformat()}] "
                f"(transport/broker unavailable); watermark left unchanged"
            )

        order_rows = [_snapshot_dict(o) for o in orders]
        deal_rows = [_snapshot_dict(d) for d in deals]

        result = self.audit.sync_broker_history(
            orders=order_rows,
            deals=deal_rows,
            symbol=self.symbol,
            sync_from=from_dt,
            sync_to=to_dt,
        )
        return result

    def _warm_accounting(self) -> None:
        """Kicks the derived accounting cache so dashboard numbers refresh."""
        try:
            core = getattr(self.audit, "_accounting_core", None)
            if core is not None:
                core.live_state()
                core.period_report(
                    __import__(
                        "nexus_scalp.accounting.periods", fromlist=["PeriodKind"]
                    ).PeriodKind.DAY,
                    use_cache=False,
                )
                core.cumulative_pnl_curve(limit=500)
                core.strategy_contributions(limit=50)
        except Exception as err:
            logger.debug("[ACCOUNT_HISTORY] accounting warm failed", error=str(err))


def _snapshot_dict(snap: Any) -> dict[str, Any]:
    """Flattens a typed snapshot (HistoryOrderSnapshot/DealSnapshot) to a dict."""
    if isinstance(snap, dict):
        return snap
    out: dict[str, Any] = {}
    for name in getattr(snap, "__dataclass_fields__", {}):
        try:
            out[name] = getattr(snap, name)
        except Exception:
            out[name] = None
    return out
