"""PositionTrackingLedger — explicit owner of per-ticket tracking state.

S6-followup seam (Agent-5, CHG-0032/TASK-OM-P0-DECOMP): the tick/duration
trackers, MFE/MAE excursion state, tick-state counters (favorable/adverse/
stagnation), LSF desync state, and reversal-evidence buffers moved out of
OrderLifecycleManager verbatim. The ledger owns the per-ticket dicts and the
five update routines plus the per-tick duration recorder; the manager
delegates and keeps compatibility @property accessors under the historical
attribute names (live dicts, single source of truth, tests that seed them
directly keep working).

Ownership contract:
    READS   : position snapshot values passed in as arguments (profit, type,
              price), current tick, protection.peak_win_usd (S1 ledger truth)
    WRITES  : only its own per-ticket dicts (19 fields, all TICKET_LOCAL)
    AUTHORITY: NONE — no broker I/O, no risk, no policy, no dispatch, no
              audit writes, no persistence. Pure tracking + telemetry state.

Lifecycle (mirrors the manager's ticket lifecycle):
    ensure_bootstrap()        -> CREATED (idempotent per-ticket seeding)
    record_tick_durations() / update_lsf_desync_metrics() / update_mfe_mae()
    / update_tick_state() / capture_reversal_state() -> TRACKING (per-tick)
    drop_ticket(ticket)       -> CLEANED (atomic teardown, part of the
    manager's _cleanup_ticket_state bundle)

Units: durations in SECONDS; MFE/MAE in PRICE units (not USD, not pips);
peaks in USD. Time source: server-local tick timestamps (BUG-070 aware).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.position_tracker")


class PositionTrackingLedger:
    """Explicit per-ticket tracking-state owner (S6-followup boundary)."""

    def __init__(self) -> None:
        # tick/duration telemetry
        self._last_tick_timestamps: dict[int, datetime] = {}
        self._time_in_profit_sec: dict[int, float] = {}
        self._time_in_drawdown_sec: dict[int, float] = {}
        self._peak_profit_usd: dict[int, float] = {}
        self._peak_drawdown_usd: dict[int, float] = {}
        self._last_tick_for_ticket: dict[int, Any] = {}
        # MFE/MAE excursion state
        self._mfe_tracker: dict[int, float] = {}
        self._mae_tracker: dict[int, float] = {}
        self._time_to_mfe_sec: dict[int, float] = {}
        self._time_to_mae_sec: dict[int, float] = {}
        # tick-state counters + LSF desync
        self._lsf_state: dict[int, dict[str, float]] = {}
        self._last_seen_ts: dict[int, datetime] = {}
        self._stagnation_ticks: dict[int, int] = {}
        self._adverse_ticks: dict[int, int] = {}
        self._favorable_ticks: dict[int, int] = {}
        self._last_price_tracker: dict[int, float] = {}
        # reversal-evidence buffers
        self._reversal_events: dict[int, list[dict[str, Any]]] = {}
        self._entry_probs: dict[int, dict[str, float]] = {}
        self._entry_regime_state: dict[int, str] = {}

    def ensure_bootstrap(
        self,
        ticket: int,
        now: datetime,
        price_current: float,
        profit_price_delta: float,
        net_price_delta: float,
    ) -> None:
        """Bootstraps LSF state and Telemetry counters for newly opened or rescued untracked positions."""
        if ticket not in self._lsf_state:
            self._lsf_state[ticket] = {
                "seen_ticks": 0.0,
                "desync_score": 0.0,
                "last_price": price_current,
                "last_profit_delta": profit_price_delta,
                "last_net_delta": net_price_delta,
                "last_sl": 0.0,
                "last_tp": 0.0,
                "last_modify_intent": 0.0,
                "be_applied": 0.0,
                "trail_applied": 0.0,
                "desync_shocks": 0.0,
            }

        self._last_seen_ts[ticket] = now

        # ANOMALY-VERIFY-01: MFE/MAE trackers MUST seed at ZERO, never at the
        # first observed price delta. Seeding at the first delta is signed by
        # direction: an immediately-adverse SELL (price above entry) seeds a
        # NEGATIVE MFE which max() can never lift above 0 -> a trade that
        # never went favorable is stored with negative MFE (IMPOSSIBLE
        # EXCURSION false-flagged). Contract: MFE >= 0, MAE <= 0.
        if ticket not in self._mfe_tracker:
            self._mfe_tracker[ticket] = 0.0
        if ticket not in self._mae_tracker:
            self._mae_tracker[ticket] = 0.0

        if ticket not in self._time_in_profit_sec:
            self._time_in_profit_sec[ticket] = 0.0
            self._time_in_drawdown_sec[ticket] = 0.0
            self._peak_profit_usd[ticket] = 0.0
            self._peak_drawdown_usd[ticket] = 0.0
            self._last_tick_timestamps[ticket] = now

        st = self._lsf_state[ticket]
        st["seen_ticks"] = st.get("seen_ticks", 0.0) + 1.0

    def update_lsf_desync_metrics(
        self,
        ticket: int,
        now: datetime,
        price_current: float,
        profit_price_delta: float,
        net_price_delta: float,
        atr: float,
    ) -> None:
        """Computes O(1) LSF metrics to detect 'missed position management' or broker IPC desync."""
        st = self._lsf_state.get(ticket)
        if not st:
            return

        last_ts = self._last_seen_ts.get(ticket, now)
        dt = (now - last_ts).total_seconds() if isinstance(last_ts, datetime) else 0.0

        last_price = float(st.get("last_price", price_current))
        last_profit = float(st.get("last_profit_delta", profit_price_delta))
        last_net = float(st.get("last_net_delta", net_price_delta))

        price_jump = abs(price_current - last_price)
        profit_jump = abs(profit_price_delta - last_profit)
        net_jump = abs(net_price_delta - last_net)

        atr_n = max(atr, 0.50)
        jump_z = price_jump / atr_n
        profit_z = profit_jump / atr_n
        net_z = net_jump / atr_n

        desync = float(st.get("desync_score", 0.0))
        shocks = float(st.get("desync_shocks", 0.0))

        if dt > 1.0:
            desync += min(10.0, (dt - 1.0) * 2.0)

        if jump_z > 0.80:
            desync += min(12.0, (jump_z - 0.80) * 10.0)
            shocks += 1.0
        if profit_z > 0.80:
            desync += min(10.0, (profit_z - 0.80) * 8.0)
        if net_z > 0.80:
            desync += min(10.0, (net_z - 0.80) * 8.0)

        desync = max(0.0, desync - 0.50)

        st["desync_score"] = desync
        st["desync_shocks"] = shocks
        st["last_price"] = price_current
        st["last_profit_delta"] = profit_price_delta
        st["last_net_delta"] = net_price_delta

        self._last_seen_ts[ticket] = now

    def update_mfe_mae(
        self,
        ticket: int,
        profit_price_delta: float,
        entry_time: datetime | None,
        now: datetime,
    ) -> None:
        """
        Advances the monotonic MFE/MAE excursion trackers.

        Also stamps WHEN each new extreme occurred so the Phase 08 position
        behaviour record can distinguish "ran to target immediately" from
        "spent an hour underwater first".
        """
        prev_mfe = self._mfe_tracker.get(ticket, 0.0)
        prev_mae = self._mae_tracker.get(ticket, 0.0)
        new_mfe = max(prev_mfe, profit_price_delta)
        new_mae = min(prev_mae, profit_price_delta)

        if entry_time is not None:
            elapsed = (now - entry_time).total_seconds()
            if new_mfe > prev_mfe or ticket not in self._time_to_mfe_sec:
                self._time_to_mfe_sec[ticket] = max(0.0, elapsed)
            if new_mae < prev_mae or ticket not in self._time_to_mae_sec:
                self._time_to_mae_sec[ticket] = max(0.0, elapsed)

        self._mfe_tracker[ticket] = new_mfe
        self._mae_tracker[ticket] = new_mae

    def update_tick_state(
        self, ticket: int, pos: Position, price_current: float, profit_price_delta: float
    ) -> None:
        last_p = self._last_price_tracker.get(ticket, price_current)
        self._last_price_tracker[ticket] = price_current

        if price_current == last_p:
            self._stagnation_ticks[ticket] = self._stagnation_ticks.get(ticket, 0) + 1
        else:
            self._stagnation_ticks[ticket] = max(0, self._stagnation_ticks.get(ticket, 0) - 1)

        is_buy = pos.type == OrderType.BUY
        is_adverse = (price_current < last_p) if is_buy else (price_current > last_p)
        is_favorable = (price_current > last_p) if is_buy else (price_current < last_p)

        if is_adverse:
            self._adverse_ticks[ticket] = self._adverse_ticks.get(ticket, 0) + 1
        elif is_favorable:
            self._favorable_ticks[ticket] = self._favorable_ticks.get(ticket, 0) + 1

    # =========================================================================
    # 57 DERIVED SMART POSITION METRICS ENGINE
    # =========================================================================

    def capture_reversal_state(
        self,
        ticket: int,
        pos: Any,
        probs: Any | None,
        regime_state: Any | None,
        now: datetime,
    ) -> None:
        """
        TASK-3: snapshots/classifies model-probability, regime and liquidity
        reversals while a position is still open. Evidence goes into
        `_reversal_events[ticket]` (bounded per ticket) and survives to the
        closing autopsy row. Pure classification + in-memory bookkeeping —
        never executes any order and never blocks the tick path.
        """
        try:
            if ticket not in self._entry_probs and probs is not None:
                try:
                    pl = probs.squeeze().tolist()
                    if not isinstance(pl, list):
                        pl = [pl]
                    p_no_trade = float(pl[0]) if len(pl) > 0 else 0.0
                    p_buy = float(pl[1]) if len(pl) > 1 else 0.0
                    p_sell = float(pl[2]) if len(pl) > 2 else 0.0
                    self._entry_probs[ticket] = {
                        "buy": round(p_buy, 6),
                        "sell": round(p_sell, 6),
                        "no_trade": round(p_no_trade, 6),
                    }
                except Exception:
                    self._entry_probs[ticket] = {}
            if ticket not in self._entry_regime_state and regime_state is not None:
                try:
                    self._entry_regime_state[ticket] = str(
                        getattr(regime_state, "regime_type", "") or ""
                    )
                except Exception:
                    self._entry_regime_state[ticket] = ""

            events = self._reversal_events.setdefault(ticket, [])
            if len(events) > 12:
                return  # bounded per ticket

            direction = str(getattr(pos, "type", "BUY") or "BUY")
            is_buy = "BUY" in direction.upper()
            entry_probs = self._entry_probs.get(ticket, {})

            if probs is not None and entry_probs:
                try:
                    pl = probs.squeeze().tolist()
                    if not isinstance(pl, list):
                        pl = [pl]
                    p_buy = float(pl[1]) if len(pl) > 1 else 0.0
                    p_sell = float(pl[2]) if len(pl) > 2 else 0.0
                    p_no_trade = float(pl[0]) if len(pl) > 0 else 0.0
                    if is_buy:
                        flipped = p_sell > p_buy + 0.10 and p_sell >= 0.5
                    else:
                        flipped = p_buy > p_sell + 0.10 and p_buy >= 0.5
                    if flipped:
                        events.append(
                            {
                                "type": "MODEL_REVERSAL",
                                "at": now.isoformat(),
                                "prob_buy": round(p_buy, 6),
                                "prob_sell": round(p_sell, 6),
                                "prob_no_trade": round(p_no_trade, 6),
                                "entry_buy": entry_probs.get("buy"),
                                "entry_sell": entry_probs.get("sell"),
                            }
                        )
                except Exception:
                    pass

            if regime_state is not None and self._entry_regime_state.get(ticket):
                try:
                    cur_regime = str(getattr(regime_state, "regime_type", "") or "")
                    if cur_regime and cur_regime != self._entry_regime_state[ticket]:
                        events.append(
                            {
                                "type": "REGIME_REVERSAL",
                                "at": now.isoformat(),
                                "from": self._entry_regime_state[ticket],
                                "to": cur_regime,
                            }
                        )
                        self._entry_regime_state[ticket] = cur_regime
                except Exception:
                    pass
        except Exception as exc:
            logger.error(
                "[TRADE_LINEAGE] reversal capture failed (isolated)",
                ticket=ticket,
                error=str(exc),
            )

    def record_tick_durations(
        self,
        ticket: int,
        now: datetime,
        current_tick: Any,
        profit: float,
        peak_win_usd: float,
    ) -> None:
        """Cache the freshest tick for this ticket (used by the breakeven-aware
        VOLATILITY_EXPANSION exit logic to detect an actual breach of the
        locked SL) + telemetry for time in profit vs drawdown + monotonic
        peak mirror. Moved VERBATIM from manage_active_positions'
        per-position loop (S6-followup)."""
        self._last_tick_for_ticket[ticket] = current_tick

        # Loop-head seeding guarantee (parity): the manager's new-ticket block
        # zeros the duration trackers at first sight; mirror that here so the
        # bare += below matches the original block's assumptions.
        self._time_in_profit_sec.setdefault(ticket, 0.0)
        self._time_in_drawdown_sec.setdefault(ticket, 0.0)
        self._peak_drawdown_usd.setdefault(ticket, 0.0)

        last_t = self._last_tick_timestamps.get(ticket, now)
        delta_sec = (now - last_t).total_seconds()
        if delta_sec < 0:
            delta_sec = 0.0
        self._last_tick_timestamps[ticket] = now

        if profit > 0.0:
            self._time_in_profit_sec[ticket] += delta_sec
        elif profit < 0.0:
            self._time_in_drawdown_sec[ticket] += delta_sec
            self._peak_drawdown_usd[ticket] = min(self._peak_drawdown_usd.get(ticket, 0.0), profit)

        # Single source of truth for peak profit: the protection state machine.
        # Mirrored here so the ledger autopsy (which reads _peak_profit_usd)
        # reports the same monotonic high-water mark.
        self._peak_profit_usd[ticket] = peak_win_usd

    def drop_ticket(self, ticket: int) -> None:
        """Release every per-ticket tracking entry (part of the manager's
        atomic _cleanup_ticket_state bundle)."""
        for d in (
            # NOTE: _last_tick_for_ticket intentionally NOT dropped — the
            # original cleanup bundle never released it (preserved leak,
            # behavior parity over tidiness).
            self._last_tick_timestamps,
            self._time_in_profit_sec,
            self._time_in_drawdown_sec,
            self._peak_profit_usd,
            self._peak_drawdown_usd,
            self._lsf_state,
            self._last_seen_ts,
            self._stagnation_ticks,
            self._adverse_ticks,
            self._favorable_ticks,
            self._last_price_tracker,
            self._mfe_tracker,
            self._mae_tracker,
            self._time_to_mfe_sec,
            self._time_to_mae_sec,
            self._reversal_events,
            self._entry_probs,
            self._entry_regime_state,
        ):
            d.pop(ticket, None)
