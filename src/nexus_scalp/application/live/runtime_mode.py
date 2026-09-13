"""RuntimeMode — operator hot mode-switch and cross-mode state invalidation.

P1 seam L13 (god-file decomposition, extraction 13 of the live-engine wave).
The mode pair leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction):

    set_execution_mode            : operator authority (BUG-148): records the
        override, swaps the adapter across the PAPER/SHADOW<->LIVE boundary,
        re-derives the runtime badge, invalidates cross-mode state.
    _invalidate_cross_mode_state  : hard state invalidation on boundary change
        (BUG-231/232): session generation bump, price-lock purge, aggregator
        reseed, warmup reset, tick-time reset. Isolation contract: a failing
        stage never breaks the swap.

CRITICAL INVARIANTS (tests enforce): a mode change must never submit an
order, never bypass risk, never reuse stale model/feature state; PAPER<->SHADOW
share the simulation boundary and do NOT invalidate (generation kept).

State ownership: session generation, warmup_state, adapter, throttles stay at
the composition root (LiveEngine) — mode transitions are BY DEFINITION
cross-service coordination; this module owns the TRANSITION LOGIC. Methods
are invoked UNBOUND with the engine as the state surface.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:  # import-cycle breaker
    from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.application.live.runtime_mode")


class RuntimeModeService:
    """Operator mode transitions (composition root: LiveEngine).

    UNBOUND-DELEGATION CONTRACT (seam L13): methods are invoked as
    ``RuntimeModeService.method(engine, ...)`` — ``self`` IS the
    LiveEngine instance; attribute access resolves against the engine.
    The structural declaration below gives mypy the engine surface these
    methods touch (no circular import at runtime).
    """

    if TYPE_CHECKING:
        config: Any  # AppConfig (engine.config)
        adapter: Any  # IMT5Port (engine.adapter)
        order_manager: Any  # OrderLifecycleManager (engine.order_manager)
        _mode_override: ExecutionMode | None
        _runtime_mode: ExecutionMode | None

        def _invalidate_cross_mode_state(self, *args: Any, **kwargs: Any) -> None: ...
        def _update_runtime_mode(self, *args: Any, **kwargs: Any) -> None: ...

    def __init__(self, om: Any) -> None:
        self.om = om

    def set_execution_mode(self: Any, mode: ExecutionMode, *, source: str = "WEB_UI") -> dict:
        """BUG-148: HOT execution-mode switch (operator authority, UI + CLI).

        Records the explicit operator choice (beats any persisted value for
        this process lifetime), re-derives the runtime badge truthfully, and
        swaps the execution adapter when the new mode requires a different
        execution boundary (PAPER/SHADOW -> simulation; LIVE -> real broker).

        Trading safety: swapping the adapter NEVER enables live order
        dispatch by itself — order authority remains RiskEngine +
        OrderLifecycleManager. In PAPER the adapter is a simulation, so no
        real order can ever be placed regardless of what the UI shows.
        """
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        if not isinstance(mode, ExecutionMode):
            return {"success": False, "reason": "INVALID_MODE"}
        old_mode = self.config.execution.mode
        self._mode_override = mode
        self.config.execution.mode = mode
        logger.info(
            "[MODE] HOT_SWAP_REQUESTED source=%s old=%s new=%s",
            source,
            old_mode.value,
            mode.value,
        )

        # Adapter boundary swap: PAPER/SHADOW => simulation adapter (safe);
        # LIVE => real MT5 adapter. The adapter is rebuilt only when its
        # execution boundary actually changes (never mid-order: dispatch
        # runs on this same loop thread, so the swap is sequential).
        wants_simulation = mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        is_simulation = isinstance(self.adapter, PaperMT5Adapter)
        swapped = False
        try:
            if wants_simulation and not is_simulation:
                old_adapter = self.adapter
                if hasattr(old_adapter, "disconnect"):
                    old_adapter.disconnect()
                # BUG-266: the hot-swap must land on the CONFIGURED paper data
                # substrate, not always synthetic. Degrade loudly (never raise):
                # a failed REPLAY build must not turn a PAPER switch into
                # ADAPTER_SWAP_FAILED (which would leave the real adapter bound).
                from nexus_scalp.adapters.paper.paper_data import (
                    ON_REPLAY_UNAVAILABLE_SYNTHETIC,
                    build_paper_adapter,
                )

                new_adapter = build_paper_adapter(
                    initial_balance=float(getattr(self, "_last_balance", 0.0) or 0.0) or 10000.0,
                    # BUG-232: the simulation must track the ACTIVE symbol.
                    # The old hot-swap built the paper adapter without a
                    # symbol, so it fell back to EURUSD conventions while the
                    # engine traded XAUUSD (wrong digits/spread/seed).
                    symbol=self.config.execution.symbol,
                    paper_data=getattr(self.config, "paper_data", None),
                    allow_replay=mode == ExecutionMode.PAPER,
                    on_replay_unavailable=ON_REPLAY_UNAVAILABLE_SYNTHETIC,
                )
                self.adapter = new_adapter
                self.order_manager.adapter = new_adapter
                self.order_manager.mt5_adapter = new_adapter
                # BUG-226: provenance follows the adapter so ledger rows and
                # account snapshots written under simulation are tagged PAPER.
                new_adapter.current_account_source = "PAPER"
                new_adapter.connect()
                swapped = True
            elif not wants_simulation and is_simulation:
                if hasattr(self.adapter, "disconnect"):
                    self.adapter.disconnect()
                from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

                mt5_cfg = getattr(self.config, "mt5", None)
                new_adapter_direct: IMT5Port = DirectMT5Adapter(
                    account=getattr(mt5_cfg, "account", None),
                    password=getattr(mt5_cfg, "password", None),
                    server=getattr(mt5_cfg, "server", None),
                    timeout=getattr(mt5_cfg, "timeout_ms", 5000),
                    retries=getattr(mt5_cfg, "retries", 3),
                )
                self.adapter = new_adapter_direct
                self.order_manager.adapter = new_adapter_direct
                self.order_manager.mt5_adapter = new_adapter_direct
                # BUG-226: back to the real broker — provenance returns to LIVE.
                new_adapter_direct.current_account_source = "LIVE"
                new_adapter_direct.connect()
                swapped = True
        except Exception as swap_err:
            logger.error("[MODE] adapter swap failed (isolated): %s", swap_err)
            return {
                "success": False,
                "reason": "ADAPTER_SWAP_FAILED",
                "detail": str(swap_err),
                "mode": mode.value,
            }

        # BUG-232: ATOMIC STATE TRANSITION — a hot-swap must invalidate every
        # piece of state derived from the OLD adapter's market data before the
        # new pipeline is allowed to act. The BUG-231 production incident
        # (stale PAPER-geometry SELL_LIMIT at 2000.08 dispatched to the real
        # 4442 broker) is exactly this hole: the adapter changed but the
        # signal policy's cached price/last-order state, the aggregator's
        # paper bars, and in-flight proposals survived the swap.
        if swapped:
            try:
                self._invalidate_cross_mode_state(old_mode, mode)
            except Exception as invalidation_err:
                logger.error(
                    "[MODE] cross-mode state invalidation failed (isolated): %s",
                    invalidation_err,
                )

        self._update_runtime_mode()
        return {
            "success": True,
            "mode": mode.value,
            "previous_mode": old_mode.value,
            "adapter_swapped": swapped,
            "runtime_mode": self._runtime_mode,
        }

    def invalidate_cross_mode_state(
        self: Any, old_mode: ExecutionMode, new_mode: ExecutionMode
    ) -> None:
        """BUG-232: drop PAPER-derived state when leaving simulation (and
        vice versa) so no stale tick/price/proposal can cross the boundary.

        Isolated by contract: never raises, never blocks the swap result.
        """
        import time as _time

        now_iso = datetime.now(UTC).isoformat()
        old_is_paper = old_mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        new_is_paper = new_mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        if old_is_paper == new_is_paper:
            return  # same boundary class — nothing cross-mode to invalidate

        # 1) Bump the session generation: every stale-tick / stale-proposal
        #    check compares against this. Anything stamped with the previous
        #    generation is rejected downstream.
        old_gen = getattr(self, "_mode_session_generation", 0)
        self._mode_session_generation = old_gen + 1
        logger.warning(
            "[MODE] BUG-232 state invalidation old=%s new=%s generation=%s->%s",
            old_mode.value,
            new_mode.value,
            old_gen,
            self._mode_session_generation,
        )

        # 2) Signal policy caches: last executed price/time and last active
        #    direction are PAPER-geometry state. Clear them so the next
        #    proposal can only be derived from the NEW adapter's tick.
        policy = getattr(self, "signal_policy", None)
        if policy is not None:
            for attr in (
                "last_order_price",
                "last_order_time",
                "_last_active_direction",
                "_last_active_direction_time",
            ):
                with contextlib.suppress(Exception):
                    setattr(policy, attr, None)
            with contextlib.suppress(Exception):
                policy._last_executed_price = 0.0

        # 3) Drop any engine-staged pending proposals/ticks stamped before
        #    the swap (defensive: their tick provenance is the old adapter).
        for attr in ("_pending_proposals", "_latest_tick", "_last_tick"):
            with contextlib.suppress(Exception):
                if hasattr(self, attr):
                    setattr(self, attr, None)

        # 3b) BUG-231 continuation: the M1 bar aggregator still holds bars
        #     minted from the OLD adapter's synthetic ticks (paper random-walk
        #     @2000 for metals). Without a purge, the next completed-bar
        #     window mixes stale paper bars with fresh live bars and the
        #     feature/predictive-limit geometry stays 2000-relative (observed
        #     live 2026-09-03 14:48-16:19 UTC, audit_signals 1069937..1076824).
        #     A empty aggregator re-warms from the NEW adapter's history via
        #     the existing BUG-054 reseed path in _cold_start_warmup /
        #     _resync_from_broker.
        aggregator = getattr(self, "aggregator", None)
        if aggregator is not None:
            try:
                # reseed([]) atomically clears all history (BUG-054 contract);
                # an empty aggregator re-warms from the NEW adapter's history
                # via _cold_start_warmup / _resync_from_broker.
                aggregator.reseed([])
                logger.warning(
                    "[MODE] BUG-231 aggregator history purged (paper bars "
                    "must not cross the execution boundary)"
                )
            except Exception as agg_err:
                logger.warning(
                    "[MODE] aggregator purge failed (non-fatal, next reseed will realign): %s",
                    agg_err,
                )
            with contextlib.suppress(Exception):
                self.warmup_state = "WARMING_UP"
                self._warmup_attempt = 0
                logger.info(
                    "[MODE] warmup state reset to WARMING_UP — HTF/feature "
                    "chain will re-derive from the new adapter's bars via "
                    "the 15s periodic readiness re-evaluation"
                )

        # 4) Reset the tick-stagnation clock so the watchdog does not
        #    immediately "reconnect" while the new adapter warms up.
        self._last_tick_processed_time = _time.time()

        # 5) BUG-232: drop the cached account snapshot. It was captured from
        #    the OLD adapter; serving it under the new mode made the UI show
        #    a paper account (login 9990001 / 10000.0) after a PAPER->LIVE
        #    swap. The next tick loop refreshes it from the new adapter.
        self._account_snapshot = None

        logger.info(
            "[MODE] BUG-232 cross-mode state invalidated at=%s swap=%s->%s",
            now_iso,
            old_mode.value,
            new_mode.value,
        )
