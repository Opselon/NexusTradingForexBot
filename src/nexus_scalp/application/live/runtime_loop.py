"""RuntimeLoop — the live engine's async run loop.

P1 seam L8 (god-file decomposition, extraction 8 of the live-engine wave).
``run_loop`` leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction). Responsibilities moved:
  * resilient MT5 startup connect (3 attempts, operator-visible retries)
  * PHASE 08 startup sequence (reconcile, worker starts)
  * the ``while self._running`` loop: tick-stagnation watchdog (BUGFIX-G29
    stalled-stream resubscribe), 5s-throttled account refresh (perf02),
    duplicate-tick early return (BUG-169), per-tick pipeline invocation,
    shutdown hand-off (``_shutdown_async``).

State ownership: ``_running`` / adapter / throttle timestamps / worker
handles stay at the composition root (LiveEngine), reached through
``self.om``; this module owns the LOOP LOGIC only. Loop exceptions are
logged + notified and the loop continues (existing semantics preserved).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.runtime_loop")


class RuntimeLoop:
    """Async run loop (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om

async def run(self) -> None:
        """
        Main tick ingestion loop.
        """
        # Resilient MT5 startup connect: the adapter itself retries
        # initialize() (bounded, backoff); the loop adds up to 3 OUTER attempts
        # so a transient IPC timeout (-10005) while the terminal is still
        # launching never kills the engine at boot.
        # Every attempt is surfaced to the console + Telegram so the operator
        # SEES the retry in progress (perfect-UI-UX requirement).
        import time as _time  # noqa: F401 - reserved for backoff timing telemetry
    
        mt5_connected = False
        for attempt in range(1, 4):
            logger.info(
                "[MT5_CONNECT] event=ATTEMPT attempt=%s/3 msg=connecting_to_terminal",
                attempt,
            )
            try:
                mt5_connected = self.om.adapter.connect()
            except Exception as conn_err:
                logger.warning(
                    "[MT5_CONNECT] event=EXCEPTION attempt=%s/3 msg=connect_raised error=%s",
                    attempt,
                    str(conn_err),
                )
                mt5_connected = False
            if mt5_connected:
                break
            if attempt < 3:
                wait_s = 1.5 * attempt
                logger.warning(
                    "[MT5_CONNECT] event=RETRY_ENGINE attempt=%s/3 msg=terminal_unavailable "
                    "wait_s=%s — retrying...",
                    attempt,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
    
        if not mt5_connected:
            logger.critical("MT5 connect() failed after 3 attempts. Engine shutting down.")
            self.om.emit_incident_telemetry(
                event_type="MT5_CONNECT_FAILED",
                component="mt5",
                severity="HIGH",
                correlation_id="startup",
            )
            with contextlib.suppress(Exception):
                self.om.notifier.notify_error(
                    "MT5 Connectivity",
                    "MT5 connect() failed after retries. Engine shutting down.",
                )
            return

        # =====================================================================
        # PERSISTED SAFETY STATE RESTORE-FIRST (P0, runtime-safety mission).
        # The persisted safety decision (HALTED / KILL_SWITCH / RUNNING) is
        # resolved BEFORE the engine arms _running or touches any trading
        # authority. A persisted halt keeps trading REFUSED for this whole
        # process: only `nexus risk release` can clear it. Restart,
        # reconnect, reload and Windows updates can never release it.
        # =====================================================================
        boot_decision = self.om._restore_runtime_risk_state()
        if not boot_decision.trading_allowed:
            # Fail closed: never enter the trading loop. The engine stays
            # observable (web/UI reflect the persisted state) and the
            # operator must explicitly release before LIVE resumes.
            logger.critical(
                "[SAFETY_STATE] startup trading REFUSED state=%s detail=%s — "
                "engine idle until explicit release (nexus risk release --confirm)",
                boot_decision.state,
                boot_decision.detail,
            )
            while self.om._runtime_risk_state in ("HALTED", "KILL_SWITCH"):
                await asyncio.sleep(1.0)
            await self.om._shutdown_async()
            return

        self.om._running = True
        symbol = self.om.config.execution.symbol

        # MISSION 5: start the Telegram command bus (if constructed) - the
        # operator control surface runs for the engine's whole lifetime.
        # Fail-isolated: a bus fault never affects trading.
        bus = getattr(self.om, "_command_bus", None)
        if bus is not None:
            try:
                bus.start()
            except Exception as bus_err:
                logger.warning(
                    "[TG_CMD] event=BUS_START_FAILED (isolated)", error=str(bus_err)
                )
    
        account = self.om.adapter.get_account_info()
        self.om._symbol_info = self.om.adapter.get_symbol_info(symbol)
    
        # PHASE 14: refresh the typed broker-aware account snapshot and derive
        # the REAL runtime mode from connection state + account permissions.
        try:
            self.om._account_snapshot = self.om.adapter.get_account_snapshot()
        except Exception:
            self.om._account_snapshot = None
        self.om._update_runtime_mode()
    
        self.om._restore_peak_equity(account)
        self.om._notify_startup(account)
    
        # BUG-072/073 restart safety: reconcile internal pending/position
        # state against broker truth at startup. The broker wins — a stale
        # internal pending (or a broker order the engine never tracked) is
        # repaired before any new entry can be considered. Isolated.
        try:
            om = self.om.order_manager
            if om is None:
                raise RuntimeError("order_manager not constructed yet (startup ordering)")
            rep = om.reconcile_pending_state(
                symbol=symbol, current_tick=self.om.adapter.get_last_tick(symbol)
            )
            logger.info(
                "[EXECUTION_RECONCILIATION] event=STARTUP "
                "pending_internal=%s pending_broker=%s mismatch=%s repaired=%s",
                rep["pending_internal"],
                rep["pending_broker"],
                rep["mismatch"],
                rep["repaired"],
            )
        except Exception as startup_rec_err:
            logger.error(
                "[EXECUTION_RECONCILIATION] event=STARTUP_FAILED (isolated)",
                error=str(startup_rec_err),
            )
            self.om.emit_incident_telemetry(
                event_type="EXECUTION_RECONCILIATION_FAILED",
                component="execution",
                severity="HIGH",
                correlation_id="startup",
            )
    
        await self.om._cold_start_warmup(symbol)
    
        await self.om._bootstrap_train_if_ready()
    
        # PHASE 08 STARTUP SEQUENCE (model-independent):
        #   1. immutable experiences already loaded from disk (SQLite)
        #   2. verify schema/provenance census
        #   3. rebuild derived intelligence off the event loop
        #   4. the active model was registered during construction
        # A missing/rebuilt model artifact does NOT reset any of this.
        await self.om._startup_experience_self_heal()
    
        # PHASE 08: start the accounting worker (background derived refresh).
        # It never touches the tick path; the periodic kick below only ever
        # schedules `to_thread` refreshes.
        self.om._start_accounting_worker()
    
        # ACCOUNT HISTORY: start the bounded broker-history sync worker
        # (watermark + overlap, idempotent, kicked via to_thread).
        self.om._start_history_sync_worker()
    
        # PHASE 09: start the background intelligence worker. Fully isolated:
        # a failure inside it can never stop trading.
        self.om._start_intelligence_worker()
    
        # PHASE 09B: start the background strategy research worker. Research is
        # OFFLINE / BACKGROUND (dataset rebuild, discovery, validation gates).
        # Fully isolated: it can never stop trading and never places orders.
        self.om._start_research_worker()
        self.om._start_factory_worker()
    
        # PHASE 10: start the controlled training worker. Heavy training runs
        # ONLY in worker threads, never in the tick pipeline; fully isolated.
        self.om._start_training_worker()
    
        # PHASE 11: start the shadow-aggregation worker. Shadow evaluation is
        # bounded + isolated; it can never stop trading or touch orders.
        self.om._start_shadow_worker()
    
        # PHASE 12: start the news intelligence worker (isolated, optional).
        self.om._start_news_worker()
    
        logger.info(
            "LIVE CONNECTED",
            login=getattr(account, "login", 0) if account else 0,
            balance=getattr(account, "balance", 0.0) if account else 0.0,
            equity=getattr(account, "equity", 0.0) if account else 0.0,
            symbol=symbol,
            digits=self.om._symbol_info.digits if self.om._symbol_info else 2,
            model_path=str(self.om.config.model.model_artifact_path),
        )
    
        self.om._last_tick_processed_time = time.time()
    
        while self.om._running:
            try:
                # Tick Stagnation Watchdog: If no ticks/bars are processed for > 15 seconds, trigger healthcheck & reconnect.
                current_time = time.time()
                if (current_time - self.om._last_tick_processed_time) > 15.0:
                    # Avoid spamming reconnects if connected but market is closed (e.g. weekend or holidays)
                    if not self.om.adapter.is_connected():
                        logger.warning(
                            "[WARNING] Tick stream stalled and MT5 disconnected. Triggering MT5 adapter healthcheck & auto-reconnect"
                        )
                        self.om.emit_incident_telemetry(
                            event_type="MT5_DISCONNECTED",
                            component="mt5",
                            severity="HIGH",
                            correlation_id="tick-stream",
                        )
                        try:
                            self.om.adapter.disconnect()
                            await asyncio.sleep(1.0)
                            self.om.adapter.connect()
                            # RESYNC (BUG-054): after a reconnect the broker may
                            # have advanced 5-6h; reseed the aggregator from
                            # broker history so the chart/features/regime all
                            # rebuild from real candles instead of the stale
                            # pre-disconnect series.
                            try:
                                await self.om._resync_from_broker(symbol)
                            except Exception as resync_err:
                                logger.error(
                                    "Watchdog reconnect resync failed",
                                    error=str(resync_err),
                                    exc_info=True,
                                )
                        except Exception as conn_err:
                            logger.error(
                                "Error during auto-reconnect in watchdog",
                                error=str(conn_err),
                                exc_info=True,
                            )
                    else:
                        # BUGFIX-G29: connection is *live* but the tick stream is
                        # quiet (is_connected()==True while no new ticks arrive).
                        # The old branch simply reset the timer and declared the
                        # connection active, which masked a dead feed behind
                        # health=READY for 26 minutes in production. Now we treat
                        # a >15s quiet stream as a stalled ingestion: emit a
                        # STALE incident and force a market-data resubscribe /
                        # tick re-poll so ingestion actually restarts instead of
                        # being hidden. This never trades — it only restores the
                        # data feed; execution remains gated by the freshness
                        # contract (live_freshness_gate).
                        logger.warning(
                            "[WATCHDOG] Tick stream stalled while MT5 reports "
                            "connected (is_connected=True). Forcing market-data "
                            "resubscribe / tick re-poll to restart ingestion."
                        )
                        self.om.emit_incident_telemetry(
                            event_type="MT5_TICK_STREAM_STALLED",
                            component="mt5",
                            severity="HIGH",
                            correlation_id="tick-stream",
                        )
                        try:
                            # Re-subscribe symbols + re-poll fresh market state.
                            if hasattr(self.om.adapter, "resubscribe_symbol") and callable(
                                self.om.adapter.resubscribe_symbol
                            ):
                                self.om.adapter.resubscribe_symbol(symbol)
                            elif hasattr(self.om.adapter, "subscribe_symbols") and callable(
                                self.om.adapter.subscribe_symbols
                            ):
                                self.om.adapter.subscribe_symbols([symbol])
                            # Probe a fresh tick so the aggregator/feature path
                            # sees movement on the very next iteration.
                            with contextlib.suppress(Exception):
                                self.om.adapter.get_tick(symbol)
                            try:
                                await self.om._resync_from_broker(symbol)
                            except Exception as resync_err:
                                logger.error(
                                    "Watchdog stalled-stream resync failed",
                                    error=str(resync_err),
                                    exc_info=True,
                                )
                        except Exception as recon_err:
                            logger.error(
                                "Error during stalled-stream resubscribe",
                                error=str(recon_err),
                                exc_info=True,
                            )
                    self.om._last_tick_processed_time = time.time()
    
                # Account/tick refresh cadence: the account snapshot is
                # refreshed at most every 5s (it is only used for position
                # sizing / runtime mode / survival state — none of which need
                # per-tick freshness), but the account info + last tick are
                # needed for the decision loop. Between refreshes we reuse the
                # last snapshot to avoid a per-tick remote RPC (~4ms at
                # loopback, more over a real gateway).
                _now = time.time()
                if getattr(self, "_last_account_refresh", 0.0) + 5.0 < _now:
                    try:
                        live_account = self.om.adapter.get_account_info()
                    except Exception:
                        live_account = getattr(self, "_last_account_info", None)
                    self.om._last_account_info = live_account
                    self.om._last_account_refresh = _now
                else:
                    # Cache hit: reuse last successful account info (the tick
                    # still advances every iteration).
                    live_account = getattr(self, "_last_account_info", None)
                tick = self.om.adapter.get_last_tick(symbol)
    
                if live_account is None or tick is None:
                    await asyncio.sleep(0.2)
                    continue
    
                if self.om._symbol_info is None:
                    self.om._symbol_info = self.om.adapter.get_symbol_info(symbol)
    
                # PHASE 14: periodically refresh the typed broker-aware account
                # snapshot + REAL runtime mode (throttled - never per tick).
                if getattr(self, "_last_snapshot_refresh", 0.0) + 5.0 < time.time():
                    with contextlib.suppress(Exception):
                        self.om._account_snapshot = self.om.adapter.get_account_snapshot()
                    self.om._update_runtime_mode()
                    self.om._last_snapshot_refresh = time.time()
    
                # BUG-169: duplicate-tick early return. The MT5 last-tick poll
                # returns the SAME quote between feed updates; re-running the
                # full pipeline (features + policy + telemetry + audit) on it
                # burns the loop thread and logs NO_TRADE conf=0.0
                # (TICK_DUPLICATE_SUPPRESSED) as if it were a fresh decision,
                # which is what the UI then displays. A duplicate carries ZERO
                # new information: keep the previous proposal/state untouched
                # and service the heartbeat workers below.
                if (
                    tick.timestamp == getattr(self, "_pipeline_last_ts", None)
                    and float(tick.bid) == getattr(self, "_pipeline_last_bid", 0.0)
                    and float(tick.ask) == getattr(self, "_pipeline_last_ask", 0.0)
                ):
                    await self.om._service_pipeline_workers(now_t=time.time())
                    await asyncio.sleep(0.05)
                    continue
                self.om._pipeline_last_ts = tick.timestamp
                self.om._pipeline_last_bid = float(tick.bid)
                self.om._pipeline_last_ask = float(tick.ask)
    
                self.om._process_tick_pipeline(tick=tick, account=live_account)
                self.om._last_tick_processed_time = time.time()
                # PHASE 08: accounting worker kick (throttled internally). This
                # is the ONLY touch point and it schedules bounded to_thread
                # work; it can never block the tick loop.
                if self.om._accounting_worker_started:
                    try:
                        self.om._kick_worker("ACCOUNTING", self.om.accounting_worker.tick)
                    except Exception:
                        # Worker failure is fully isolated; never disturb ticks.
                        pass
    
                # P1 seam L3: periodic maintenance cycle (purge / hygiene /
                # incidents / daily summary / worker kicks / governance health)
                # moved to application/live/maintenance.py (single owner).
                await self.om._maintenance.run_cycle(now_t=time.time())
                await asyncio.sleep(0.05)
    
            except Exception as e:
                logger.error("Error in live loop", error=str(e), exc_info=True)
                with contextlib.suppress(Exception):
                    self.om.notifier.notify_error("Real-Time Execution Loop", str(e))
                await asyncio.sleep(1.0)
    
        await self.om._shutdown_async()
