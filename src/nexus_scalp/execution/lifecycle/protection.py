"""ProtectionEngine — deterministic breakeven / trailing / giveback protection.

P0 seam S10 (god-file decomposition): the deterministic position protection
layer — breakeven lock, ATR trailing stop, profit-giveback evaluation and
enforcement, and their pip/digits/contract resolvers — moves OUT of
``OrderLifecycleManager`` verbatim (behavior-preserving extraction). The
manager composes this engine and keeps thin delegates; per-ticket protection
state remains in the canonical protection ledger the manager already owns —
the engine reads/writes THROUGH the ``om`` composition root.

Moved methods (verbatim bodies, ``self`` -> ``om``):
    apply_breakeven_lock / apply_atr_trailing_stop / _maybe_tighten_protective_sl
    evaluate_profit_giveback / enforce_profit_giveback_protection / _tiered_giveback_floor
    calculate_breakeven_sl / is_sl_improvement / _should_modify_sl
    _protective_sl_floor / _is_sl_at_or_beyond / refresh_protection_state
    _atr_profit_threshold_usd / _log_protection_audit / _log_throttled_be_failure
    _resolve_pip_size / _resolve_price_digits / _resolve_contract_size

Ownership contract:
    READS   : protection ledger (per-ticket state), initial risks, adapter
              (modify/close), notifier, audit, last-tick view
    WRITES  : protection state, _last_modify_sl / _sl_modified_flags,
              _forced_exit_mechanisms, audit protection rows
    AUTHORITY: protective SL modification + giveback close ONLY — no new
              position dispatch, no hedge entry.

The BREAKEVEN_* / PROFIT_GIVEBACK_* / TIERED_* invariants remain defined on
``execution/order_manager.py`` (tests import them from there) and are bound
lazily below (import cycle breaker, same discipline as scoring.py's
``_om_symbols``).
"""

from __future__ import annotations

import math
import time
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData
from nexus_scalp.execution.protection_ledger import PositionProtectionState
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.lifecycle.protection")


def _om_protection_symbols() -> tuple[
    float,  # 0  BREAKEVEN_PROFIT_USD
    float,  # 1  BREAKEVEN_TRIGGER_R
    float,  # 2  BREAKEVEN_ATR_MULTIPLIER
    float,  # 3  BREAKEVEN_LOCK_PIPS
    float,  # 4  BREAKEVEN_ATTEMPT_COOLDOWN_SEC
    float,  # 5  DEFAULT_PIP_SIZE
    float,  # 6  PROFIT_GIVEBACK_PEAK_USD
    float,  # 7  PROFIT_GIVEBACK_MIN_RETENTION
    tuple[tuple[float, float], ...],  # 8  TIERED_GIVEBACK_RETENTION_FLOOR
    float,  # 9  TIERED_GIVEBACK_ARM_R
    int,  # 10 PROFIT_GIVEBACK_HOLD_SCORE_PENALTY
    int,  # 11 NEGATIVE_AFTER_PROFIT_HOLD_SCORE
    float,  # 12 ATR_TRAILING_MULTIPLIER
    float,  # 13 TELEMETRY_CONSOLE_INTERVAL_SEC
]:
    """Late-bound god-module protection constants (import cycle breaker)."""
    from nexus_scalp.execution.order_manager import (
        ATR_TRAILING_MULTIPLIER,
        BREAKEVEN_ATR_MULTIPLIER,
        BREAKEVEN_ATTEMPT_COOLDOWN_SEC,
        BREAKEVEN_LOCK_PIPS,
        BREAKEVEN_PROFIT_USD,
        BREAKEVEN_TRIGGER_R,
        DEFAULT_PIP_SIZE,
        NEGATIVE_AFTER_PROFIT_HOLD_SCORE,
        PROFIT_GIVEBACK_HOLD_SCORE_PENALTY,
        PROFIT_GIVEBACK_MIN_RETENTION,
        PROFIT_GIVEBACK_PEAK_USD,
        TELEMETRY_CONSOLE_INTERVAL_SEC,
        TIERED_GIVEBACK_ARM_R,
        TIERED_GIVEBACK_RETENTION_FLOOR,
    )

    return (
        BREAKEVEN_PROFIT_USD,
        BREAKEVEN_TRIGGER_R,
        BREAKEVEN_ATR_MULTIPLIER,
        BREAKEVEN_LOCK_PIPS,
        BREAKEVEN_ATTEMPT_COOLDOWN_SEC,
        DEFAULT_PIP_SIZE,
        PROFIT_GIVEBACK_PEAK_USD,
        PROFIT_GIVEBACK_MIN_RETENTION,
        TIERED_GIVEBACK_RETENTION_FLOOR,
        TIERED_GIVEBACK_ARM_R,
        PROFIT_GIVEBACK_HOLD_SCORE_PENALTY,
        NEGATIVE_AFTER_PROFIT_HOLD_SCORE,
        ATR_TRAILING_MULTIPLIER,
        TELEMETRY_CONSOLE_INTERVAL_SEC,
    )


def _om_exit_mechanism() -> Any:
    """Late-bound ExitMechanism taxonomy class (import cycle breaker)."""
    from nexus_scalp.execution.order_manager import ExitMechanism

    return ExitMechanism


class ProtectionEngine:
    """Deterministic protection owner: breakeven / trailing / giveback (S10)."""

    def __init__(self, om: Any) -> None:
        # Composition root (OrderLifecycleManager). The engine deliberately
        # accesses the manager's canonical state surface (protection ledger,
        # SL trackers, forced-exit bookkeeping) instead of copying any of it
        # (single source of truth, S5 discipline).
        self.om = om

    def _should_modify_sl(self, ticket: int, new_sl: float) -> bool:
        """Determines if the proposed new stop loss step is significantly different from last sent modification."""
        last_sl = self.om._last_modify_sl.get(ticket, 0.0)
        if abs(new_sl - last_sl) >= self.om.min_step:
            return True
        return False

    def _resolve_pip_size(self, symbol_info: SymbolInfo | None) -> float:
        """
        Canonical pip size resolver.

        A pip is 10 broker points, derived from `SymbolInfo.point` whenever the broker
        specification is available. Falls back to the project-wide gold pip constant
        (`DEFAULT_PIP_SIZE`, also used by `rule_matrix.py`) when it is not, so no
        XAUUSD point conversion is hard-coded at the call sites.
        """
        DEFAULT_PIP_SIZE = _om_protection_symbols()[5]
        if symbol_info is not None:
            try:
                point = float(symbol_info.point)
                if point > 0.0 and not math.isnan(point) and not math.isinf(point):
                    return point * 10.0
            except (TypeError, ValueError):
                pass
        return DEFAULT_PIP_SIZE

    def _resolve_price_digits(self, symbol_info: SymbolInfo | None) -> int:
        """Broker price precision, defaulting to 2 decimals (XAUUSD convention)."""
        if symbol_info is not None:
            try:
                digits = int(symbol_info.digits)
                if 0 <= digits <= 10:
                    return digits
            except (TypeError, ValueError):
                pass
        return 2

    def _atr_profit_threshold_usd(
        self,
        volume: float,
        symbol_info: SymbolInfo | None,
        atr: float,
    ) -> float:
        """
        Converts `BREAKEVEN_ATR_MULTIPLIER` x ATR (price units) into this position's
        USD PnL using the same contract-size arithmetic the risk engine uses.

        Raw ATR price units are never compared against USD PnL directly.
        """
        BREAKEVEN_ATR_MULTIPLIER = _om_protection_symbols()[2]
        try:
            atr_price_delta = max(float(atr), 0.0) * BREAKEVEN_ATR_MULTIPLIER
        except (TypeError, ValueError):
            return math.inf
        usd = float(self.om._price_delta_to_usd(atr_price_delta, volume, symbol_info))
        if usd <= 0.0 or math.isnan(usd) or math.isinf(usd):
            # A non-positive/invalid conversion must never create a free trigger.
            return math.inf
        return usd

    def calculate_breakeven_sl(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> float:
        """
        Breakeven stop price locking `BREAKEVEN_LOCK_PIPS` of profit beyond entry.

        BUY : entry + 0.20 pips
        SELL: entry - 0.20 pips
        """
        BREAKEVEN_LOCK_PIPS = _om_protection_symbols()[3]
        pip = self.om._resolve_pip_size(symbol_info)
        offset = BREAKEVEN_LOCK_PIPS * pip
        raw = pos.price_open + offset if pos.type == OrderType.BUY else pos.price_open - offset
        return float(round(raw, self.om._resolve_price_digits(symbol_info)))

    @staticmethod
    def _is_sl_at_or_beyond(pos: Position, sl_value: float, reference_sl: float) -> bool:
        """
        True when `sl_value` is at or beyond `reference_sl` in the position's favourable
        direction. Used both for the restart-safe breakeven check and to guarantee an
        existing protective stop is never moved backwards.
        """
        if sl_value <= 0.0:
            return False
        if pos.type == OrderType.BUY:
            return sl_value >= (reference_sl - 1e-9)
        return sl_value <= (reference_sl + 1e-9)

    def refresh_protection_state(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> PositionProtectionState:
        """
        Reconciles per-ticket protection state with the position as the broker reports it.

        Performed on EVERY refresh so that:
          - `peak_win_usd` advances monotonically with floating PnL,
          - the breakeven level is always current, and
          - a position whose real SL already sits at/beyond breakeven is treated as
            protected even if this process just restarted and has no memory of it
            (prevents duplicate SL modifications after state reconstruction).
        """
        state: PositionProtectionState = self.om.get_protection_state(pos.ticket)
        state.update_peak(pos.profit)

        breakeven_sl = self.om.calculate_breakeven_sl(pos, symbol_info)
        state.breakeven_sl_price = breakeven_sl

        # Real MT5 state wins over the in-memory flag: the flag is never the only
        # source of truth. Note this can only ever mark the position as MORE
        # protected, never less.
        if self.om._is_sl_at_or_beyond(pos, pos.sl, breakeven_sl):
            if not state.was_sl_modified:
                logger.debug(
                    "BREAKEVEN ALREADY PRESENT ON BROKER: reconstructing protected state",
                    ticket=pos.ticket,
                    actual_sl=pos.sl,
                    breakeven_sl=breakeven_sl,
                )
            state.was_sl_modified = True
            self.om._sl_modified_flags[pos.ticket] = True

        return state

    def _protective_sl_floor(self, ticket: int) -> float:
        """
        Lowest (BUY) / highest (SELL) stop price any later mechanism is allowed to set,
        i.e. the confirmed breakeven lock. Returns 0.0 when no lock is active.
        """
        state: PositionProtectionState | None = self.om._protection_ledger.get(ticket)
        if state is None or not state.was_sl_modified:
            return 0.0
        return state.breakeven_sl_price

    def is_sl_improvement(self, pos: Position, new_sl: float) -> bool:
        """
        Guard shared by breakeven, ATR trailing and rule-driven SL moves.

        Returns True only when `new_sl` tightens protection: it must advance past the
        current broker SL in the profitable direction AND must never regress behind an
        already-confirmed breakeven lock.
        """
        if new_sl <= 0.0:
            return False

        is_buy = pos.type == OrderType.BUY

        # 1. Never loosen the stop the broker already holds.
        if pos.sl > 0.0:
            if is_buy and new_sl <= pos.sl:
                return False
            if not is_buy and new_sl >= pos.sl:
                return False

        # 2. Never move behind a confirmed breakeven lock.
        floor_sl = self.om._protective_sl_floor(pos.ticket)
        if floor_sl > 0.0:
            if is_buy and new_sl < (floor_sl - 1e-9):
                return False
            if not is_buy and new_sl > (floor_sl + 1e-9):
                return False

        return True

    def _log_protection_audit(
        self,
        pos: Position,
        action: str,
        reason: str,
        stop_loss: float = 0.0,
    ) -> None:
        """
        Writes a protection event to the SQLite audit ledger.

        Deliberately isolated and fully exception-guarded: an audit/telemetry failure
        must never prevent (or disable) a breakeven or close action.
        """
        try:
            self.om.audit.log_order(
                ticket=pos.ticket,
                order_id=f"protect_{pos.ticket}_{action.lower()}",
                symbol=pos.symbol,
                action=action,
                price=pos.price_open,
                stop_loss=stop_loss,
                take_profit=pos.tp,
                volume=pos.volume,
                reason=reason,
                latency=0.0,
                execution_mode="PROTECTION",
            )
        except Exception as err:
            logger.error(
                "Protection audit write failed (protection continues)",
                ticket=pos.ticket,
                error=str(err),
            )

    def apply_breakeven_lock(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
        atr: float = 0.0,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """
        Priority-4 protection: locks a breakeven(+0.20 pip) stop once the position has
        earned meaningful profit.

        Activation (either trigger is sufficient):
            current_pnl_usd >= BREAKEVEN_PROFIT_USD            ($15.00)
            current_pnl_usd >= 1.5 ATR expressed in USD PnL

        Guarded by `was_sl_modified` so the modification is issued at most once per
        ticket, and by the broker-state reconciliation in `refresh_protection_state`
        so a restart cannot duplicate it.

        Returns True only when the adapter CONFIRMED the modification.
        """
        _S = _om_protection_symbols()
        BREAKEVEN_PROFIT_USD, BREAKEVEN_TRIGGER_R = _S[0], _S[1]
        BREAKEVEN_ATTEMPT_COOLDOWN_SEC = _S[4]
        state = self.om.get_protection_state(pos.ticket)

        if state.was_sl_modified or state.close_requested:
            return False

        # Retry cooldown (BUG-085/086): a broker-rejected or deferred breakeven
        # modification must not be re-attempted every management tick. The failure
        # storm on the live path produced 6,674 BREAKEVEN_FAILED audit rows from a
        # handful of tickets; the cooldown bounds retries to one per
        # BREAKEVEN_ATTEMPT_COOLDOWN_SEC while keeping the retry possible.
        now_mono = time.monotonic()
        if (now_mono - state.last_be_attempt_time) < BREAKEVEN_ATTEMPT_COOLDOWN_SEC:
            return False
        state.last_be_attempt_time = now_mono

        current_pnl_usd = float(pos.profit)
        atr_threshold_usd = self.om._atr_profit_threshold_usd(pos.volume, symbol_info, atr)
        # AGENT4-SPRINT: R-anchored trigger floor — the flat $15 threshold alone
        # fires at ~0.09R and locks an entry-level stop before the move develops.
        initial_risk_usd = self.om._initial_risks.get(pos.ticket, 0.0)
        r_trigger_usd = BREAKEVEN_TRIGGER_R * initial_risk_usd if initial_risk_usd > 0.0 else 0.0
        be_trigger_usd = max(BREAKEVEN_PROFIT_USD, r_trigger_usd)
        if current_pnl_usd < be_trigger_usd and current_pnl_usd < atr_threshold_usd:
            return False

        breakeven_sl = state.breakeven_sl_price or self.om.calculate_breakeven_sl(pos, symbol_info)
        state.breakeven_sl_price = breakeven_sl

        # Already at/beyond breakeven on the broker side: nothing to send.
        if self.om._is_sl_at_or_beyond(pos, pos.sl, breakeven_sl):
            state.was_sl_modified = True
            self.om._sl_modified_flags[pos.ticket] = True
            return False

        # Respect the broker's minimum stop distance PLUS the live spread so a
        # breakeven modification can never cross into the opposing book. The broker
        # STOP_LEVEL alone is insufficient: on a 2-digit XAUUSD symbol the stops
        # level can be ~0.10-0.35, smaller than the 0.20-0.25 live spread, so a
        # breakeven SL placed exactly at STOP_LEVEL distance would still be rejected
        # (or worse, crossed by the fill). Retry on a later pass instead of burning a
        # guaranteed-reject modification request.
        live_spread = (
            float(current_tick.ask - current_tick.bid) if current_tick is not None else 0.0
        )
        effective_freeze_gap = max(min_stop_gap, 0.35) + max(live_spread, 0.0)
        if current_tick is not None:
            is_buy = pos.type == OrderType.BUY
            current_market_price = current_tick.bid if is_buy else current_tick.ask

            # Verify SL sits on valid side of current market price to prevent MT5 10016 Retcode
            if is_buy and breakeven_sl >= (current_market_price - effective_freeze_gap):
                # Market pulled back before modification dispatched; defer or cap SL safely below market bid
                breakeven_sl = round(
                    current_market_price - effective_freeze_gap,
                    self.om._resolve_price_digits(symbol_info),
                )
                if breakeven_sl <= pos.price_open:
                    self.om._log_throttled_be_failure(
                        state,
                        pos,
                        f"BREAKEVEN DEFERRED: market pulled back (Bid: ${current_market_price:.2f}), SL would cross market price",
                        breakeven_sl,
                    )
                    return False

            elif not is_buy and breakeven_sl <= (current_market_price + effective_freeze_gap):
                # Market pulled back before modification dispatched; defer or cap SL safely above market ask
                breakeven_sl = round(
                    current_market_price + effective_freeze_gap,
                    self.om._resolve_price_digits(symbol_info),
                )
                if breakeven_sl >= pos.price_open:
                    self.om._log_throttled_be_failure(
                        state,
                        pos,
                        f"BREAKEVEN DEFERRED: market pulled back (Ask: ${current_market_price:.2f}), SL would cross market price",
                        breakeven_sl,
                    )
                    return False

        take_profit = pos.tp  # Existing take-profit is preserved verbatim.

        try:
            success = bool(
                self.om.mt5_adapter.modify_position(
                    ticket=pos.ticket,
                    stop_loss=breakeven_sl,
                    take_profit=take_profit,
                )
            )
        except Exception as err:
            success = False
            logger.error(
                "BREAKEVEN LOCK ERROR: modify_position raised",
                ticket=pos.ticket,
                error=str(err),
            )

        if not success:
            # Explicitly do NOT set was_sl_modified: the retry stays possible on the
            # next tracking cycle. Failure logging is throttled, the retry is not.
            self.om._log_throttled_be_failure(
                state,
                pos,
                "BREAKEVEN LOCK FAILED: broker rejected modification, retry pending",
                breakeven_sl,
            )
            return False

        # Only a CONFIRMED modification advances the tracked final SL. A failed
        # attempt must never pollute `_last_modify_sl` (BUG-085): doing so made the
        # autopsy record final_sl != initial_sl with was_sl_modified=False and could
        # suppress the retry via `_should_modify_sl` step comparison.
        self.om._last_modify_sl[pos.ticket] = breakeven_sl
        state.was_sl_modified = True
        self.om._sl_modified_flags[pos.ticket] = True

        logger.info(
            "BREAKEVEN LOCK ACTIVATED",
            ticket=f"#{pos.ticket}",
            pnl=f"${current_pnl_usd:.2f}",
            peak=f"${state.peak_win_usd:.2f}",
            entry=pos.price_open,
            sl=breakeven_sl,
        )
        self.om._log_protection_audit(
            pos,
            action="BREAKEVEN_LOCK",
            reason=f"BREAKEVEN_LOCK_ACTIVATED pnl=${current_pnl_usd:.2f} peak=${state.peak_win_usd:.2f}",
            stop_loss=breakeven_sl,
        )

        if self.om.notifier:
            try:
                contract_size = self.om._resolve_contract_size(symbol_info)
                self.om.notifier.notify_break_even_applied_extended(
                    ticket=pos.ticket,
                    new_sl=breakeven_sl,
                    original_risk_usd=self.om._initial_risks.get(pos.ticket, 0.0),
                    protected_amount_usd=abs(breakeven_sl - pos.price_open)
                    * pos.volume
                    * contract_size,
                    reply_to_message_id=self.om._order_message_ids.get(pos.ticket),
                )
            except Exception as err:
                logger.error("Breakeven notification failed", ticket=pos.ticket, error=str(err))

        return True

    def _maybe_tighten_protective_sl(
        self,
        pos: Position,
        state: PositionProtectionState,
        symbol_info: SymbolInfo | None = None,
    ) -> bool:
        """
        TASK 3 helper: dynamically tightens an already-locked protective stop towards the
        current profit floor (never loosening it). Used in VOLATILITY_EXPANSION when a
        market close is suppressed so the position is still actively defended without
        crossing the spread. Returns True if a modification was issued and confirmed.
        """
        if not state.was_sl_modified:
            return False
        peak = state.peak_win_usd
        # Only meaningful once a meaningful peak profit exists.
        if peak <= 0.0 or pos.profit <= 0.0:
            return False

        contract_sz = self.om._resolve_contract_size(symbol_info)
        # Target = lock in a portion of current profit, but never below the breakeven level.
        target_profit_lock = pos.profit * 0.85
        if pos.type == OrderType.BUY:
            candidate_sl = pos.price_open + (
                target_profit_lock / max(pos.volume * contract_sz, 1.0)
            )
        else:
            candidate_sl = pos.price_open - (
                target_profit_lock / max(pos.volume * contract_sz, 1.0)
            )
        candidate_sl = round(candidate_sl, self.om._resolve_price_digits(symbol_info))

        if not self.om.is_sl_improvement(pos, candidate_sl):
            return False
        if not self.om._should_modify_sl(pos.ticket, candidate_sl):
            return False

        try:
            success = bool(
                self.om.mt5_adapter.modify_position(
                    ticket=pos.ticket,
                    stop_loss=candidate_sl,
                    take_profit=pos.tp,
                )
            )
        except Exception:
            return False
        if success:
            self.om._sl_modified_flags[pos.ticket] = True
            self.om._last_modify_sl[pos.ticket] = candidate_sl
            logger.info(
                "PROFIT GIVEBACK: dynamic SL tighten in VOLATILITY_EXPANSION",
                ticket=f"#{pos.ticket}",
                new_sl=candidate_sl,
                old_sl=pos.sl,
            )
        return success

    def _resolve_contract_size(self, symbol_info: SymbolInfo | None) -> float:
        """Contract size with the project-wide 100.0 (gold) fallback."""
        if symbol_info is not None and symbol_info.trade_contract_size > 0:
            return float(symbol_info.trade_contract_size)
        return 100.0

    def _log_throttled_be_failure(
        self,
        state: PositionProtectionState,
        pos: Position,
        message: str,
        breakeven_sl: float,
    ) -> None:
        """
        Emits a breakeven-failure warning at most once every
        `TELEMETRY_CONSOLE_INTERVAL_SEC` per ticket so a persistent broker rejection
        cannot flood the console. The audit record is written every time.
        """
        TELEMETRY_CONSOLE_INTERVAL_SEC = _om_protection_symbols()[13]
        now = time.monotonic()
        if (now - state.last_be_failure_log_time) >= TELEMETRY_CONSOLE_INTERVAL_SEC:
            logger.warning(
                message,
                ticket=pos.ticket,
                breakeven_sl=breakeven_sl,
                actual_sl=pos.sl,
                pnl=f"${pos.profit:+.2f}",
            )
            state.last_be_failure_log_time = now

        self.om._log_protection_audit(
            pos,
            action="BREAKEVEN_FAILED",
            reason=message,
            stop_loss=breakeven_sl,
        )

    def _tiered_giveback_floor(self, ticket: int, peak: float) -> tuple[float, bool]:
        """
        Returns (retention_floor, armed) for a peak profit.

        The floor is derived from the PEAK expressed in R (peak USD / initial risk
        USD). Tiers let small scalps tolerate normal noise while locking in a
        meaningful share of larger runners. `armed=False` means the giveback
        protection stays DISARMED (micro-profit noise zone).
        """
        _S = _om_protection_symbols()
        PROFIT_GIVEBACK_MIN_RETENTION = _S[7]
        TIERED_GIVEBACK_RETENTION_FLOOR = _S[8]
        TIERED_GIVEBACK_ARM_R = _S[9]
        risk_usd = self.om._initial_risks.get(ticket, 0.0)
        if risk_usd <= 0.0 or peak <= 0.0:
            # Without a known planned risk we fall back to the absolute floor so
            # protection is never silently disabled.
            return PROFIT_GIVEBACK_MIN_RETENTION, True
        peak_r = peak / risk_usd
        if peak_r < TIERED_GIVEBACK_ARM_R:
            return PROFIT_GIVEBACK_MIN_RETENTION, False
        floor = PROFIT_GIVEBACK_MIN_RETENTION
        for tier_r, tier_floor in TIERED_GIVEBACK_RETENTION_FLOOR:
            if peak_r >= tier_r:
                floor = tier_floor
            else:
                break
        return floor, True

    def evaluate_profit_giveback(
        self,
        ticket: int,
        current_pnl_usd: float,
        base_hold_score: int,
    ) -> tuple[int, bool, str]:
        """
        Deterministic profit-erosion evaluation and hold-score safety override.

        Runs AFTER the base score has been computed but BEFORE the score is used for
        any execution decision, so normal scoring can never overwrite a safety verdict.

        Returns (final_hold_score, protection_required, reason).
        """
        _S = _om_protection_symbols()
        PROFIT_GIVEBACK_PEAK_USD = _S[6]
        PROFIT_GIVEBACK_HOLD_SCORE_PENALTY = _S[10]
        NEGATIVE_AFTER_PROFIT_HOLD_SCORE = _S[11]
        state = self.om.get_protection_state(ticket)
        score = int(base_hold_score)
        peak = state.peak_win_usd

        if peak < PROFIT_GIVEBACK_PEAK_USD:
            return max(0, min(100, score)), False, ""

        retention_floor, armed = self.om._tiered_giveback_floor(ticket, peak)
        if not armed:
            return max(0, min(100, score)), False, ""

        retention = state.retention_ratio(current_pnl_usd)

        # --- Priority 3: negative PnL after a meaningful profit -----------------
        # Evaluated before anything can raise the score again: a trade that banked
        # >= $20 and is now red must never look attractive to hold.
        if current_pnl_usd < 0.0:
            return (
                NEGATIVE_AFTER_PROFIT_HOLD_SCORE,
                True,
                f"NEGATIVE_PNL_AFTER_PEAK peak=${peak:.2f} current=${current_pnl_usd:.2f}",
            )

        # --- Priority 2: tiered profit retention floor breached -----------------
        if retention < retention_floor:
            score -= PROFIT_GIVEBACK_HOLD_SCORE_PENALTY
            score = max(0, min(100, score))
            return (
                score,
                True,
                f"PROFIT_RETENTION_BREACH peak=${peak:.2f} current=${current_pnl_usd:.2f} "
                f"retention={retention:.2%} floor={retention_floor:.2%}",
            )

        return max(0, min(100, score)), False, ""

    def enforce_profit_giveback_protection(
        self,
        pos: Position,
        hold_score: int,
        symbol_info: SymbolInfo | None = None,
        regime: str | None = None,
    ) -> tuple[int, bool]:
        """
        Priority-2/3 protection: arms PROFIT_GIVEBACK_PROTECTION and submits exactly one
        market close for a winner that has eroded past the retention floor or turned
        negative after banking >= PROFIT_GIVEBACK_PEAK_USD.

        Returns (effective_hold_score, protection_active). When protection_active is
        True the caller MUST NOT let any lower-priority mechanism act on the ticket.

        TASK 3 HARDENING: when the close is being triggered inside a high-spread
        VOLATILITY_EXPANSION regime AND a breakeven (or better) stop is ALREADY locked
        on the broker terminal, we must NOT fire a live market close that crosses the
        spread (which would destroy the protected profit). Instead we trust the locked
        SL to do the job and, if possible, tighten it dynamically via modify_position.
        A market close is only permitted if price has crossed below the breakeven SL or
        the SL modification itself fails.
        """
        ExitMechanism = _om_exit_mechanism()
        state = self.om.get_protection_state(pos.ticket)
        current_pnl_usd = float(pos.profit)

        final_score, protection_required, reason = self.om.evaluate_profit_giveback(
            ticket=pos.ticket,
            current_pnl_usd=current_pnl_usd,
            base_hold_score=hold_score,
        )

        if not protection_required:
            return final_score, False

        retention = state.retention_ratio(current_pnl_usd)
        state.profit_giveback_triggered = True

        # --- TASK 3: Breakeven-aware exit suppression during VOLATILITY_EXPANSION ---
        is_vol_expansion = regime == "VOLATILITY_EXPANSION"
        breakeven_locked = state.was_sl_modified and self.om._is_sl_at_or_beyond(
            pos, pos.sl, state.breakeven_sl_price
        )
        if is_vol_expansion and breakeven_locked and not state.close_requested:
            # Reference for the "price crossed below breakeven" check.
            ref = getattr(self, "_last_tick_for_ticket", {}).get(pos.ticket)
            # Determine whether price has already breached the locked protective stop.
            price_below_be = False
            if pos.type == OrderType.BUY:
                price_below_be = ref is not None and getattr(ref, "bid", 1e18) <= pos.sl
            else:
                price_below_be = ref is not None and getattr(ref, "ask", 0.0) >= pos.sl

            if not price_below_be:
                # Do NOT cross the spread with a market close. Keep the locked SL and
                # attempt a dynamic tighten (trailing) via native MT5 modification.
                logger.info(
                    "PROFIT GIVEBACK: breakeven SL already locked in VOLATILITY_EXPANSION; "
                    "suppressing market close, relying on protective SL",
                    ticket=f"#{pos.ticket}",
                    peak=f"${state.peak_win_usd:.2f}",
                    current=f"${current_pnl_usd:.2f}",
                    retention=f"{retention:.2%}",
                    locked_sl=pos.sl,
                )
                logger.info(
                    "[POSITION_EXIT_BLOCKED]",
                    ticket=pos.ticket,
                    intended_action="CLOSE",
                    blocker="VOLATILITY_EXPANSION_BREAKEVEN_SUPPRESSION",
                    reason=(
                        "breakeven SL locked on broker; market close would cross the "
                        "spread and destroy protected profit"
                    ),
                    pnl=round(float(current_pnl_usd), 2),
                    locked_sl=pos.sl,
                )
                # Try to tighten the SL to the current retention floor (still >= breakeven).
                tightened = self.om._maybe_tighten_protective_sl(pos, state, symbol_info)
                if not tightened:
                    logger.debug(
                        "PROFIT GIVEBACK: SL tighten skipped (already optimal or broker rejected)",
                        ticket=pos.ticket,
                    )
                # Protection is considered active (lower-priority mechanisms must not act),
                # but no market close is dispatched.
                return max(0, min(100, final_score)), True
        # when the previous request was reported as failed (close_requested stays
        # False in that case).
        if state.close_requested:
            logger.debug(
                "PROFIT GIVEBACK PROTECTION: close already requested, suppressing duplicate",
                ticket=pos.ticket,
            )
            return final_score, True

        logger.warning(
            "[EXIT TRACE] PROFIT GIVEBACK PROTECTION TRIGGERED",
            ticket=f"#{pos.ticket}",
            peak=f"${state.peak_win_usd:.2f}",
            current=f"${current_pnl_usd:.2f}",
            retention=f"{retention:.2%}",
            hold_score=final_score,
            reason=reason,
            exit_mechanism=ExitMechanism.PROFIT_GIVEBACK_PROTECTION,
        )
        self.om._log_protection_audit(
            pos,
            action="PROFIT_GIVEBACK_PROTECTION",
            reason=f"{reason} hold_score={final_score} exit_mechanism={ExitMechanism.PROFIT_GIVEBACK_PROTECTION}",
            stop_loss=pos.sl,
        )

        # Propagate the exit metadata through the EXISTING forced-exit mechanism so the
        # ledger autopsy attributes the close correctly. No parallel interface is added.
        self.om._forced_exit_mechanisms[pos.ticket] = ExitMechanism.PROFIT_GIVEBACK_PROTECTION

        try:
            closed = bool(self.om.adapter.close_position(ticket=pos.ticket))
        except Exception as err:
            closed = False
            logger.error(
                "PROFIT GIVEBACK PROTECTION: close_position raised",
                ticket=pos.ticket,
                error=str(err),
            )

        if closed:
            state.close_requested = True
            self.om._hold_score_tracker[pos.ticket] = final_score
            with self.om._live_tickets_lock:
                self.om._live_tickets_cache.pop(pos.ticket, None)
            if self.om.notifier:
                try:
                    self.om.notifier.notify_early_emergency_cut(
                        ticket=pos.ticket,
                        score=final_score,
                        reasons=f"{ExitMechanism.PROFIT_GIVEBACK_PROTECTION}: {reason}",
                        saved_usd=current_pnl_usd,
                        reply_to_message_id=self.om._order_message_ids.get(pos.ticket),
                    )
                except Exception as err:
                    logger.error(
                        "Profit giveback notification failed", ticket=pos.ticket, error=str(err)
                    )
        else:
            # Close failed: clear the forced tag (so an organic exit is not mislabelled)
            # and leave close_requested False so the next cycle retries.
            self.om._forced_exit_mechanisms.pop(pos.ticket, None)
            self.om._log_protection_audit(
                pos,
                action="PROFIT_GIVEBACK_CLOSE_FAILED",
                reason=f"{reason} close_position returned falsy, retry pending",
                stop_loss=pos.sl,
            )

        return final_score, True

    def apply_atr_trailing_stop(
        self,
        pos: Position,
        price_current: float,
        atr: float,
        symbol_info: SymbolInfo | None = None,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """
        Priority-5 protection: ATR trailing stop built on the ATR already produced by
        the feature pipeline (`FeatureVector.atr_m1`); no second ATR implementation is
        introduced.

        BUY : trailing_sl = price - ATR * ATR_TRAILING_MULTIPLIER
        SELL: trailing_sl = price + ATR * ATR_TRAILING_MULTIPLIER

        The stop is only ever tightened: `is_sl_improvement` rejects any candidate that
        would loosen the broker SL or regress behind a confirmed breakeven lock.
        """
        ATR_TRAILING_MULTIPLIER = _om_protection_symbols()[12]
        state = self.om.get_protection_state(pos.ticket)
        if state.close_requested or state.profit_giveback_triggered:
            # A higher-priority protection decision is in force; trailing must not
            # replace or cancel it.
            return False

        try:
            distance = max(float(min_stop_gap), round(float(atr) * ATR_TRAILING_MULTIPLIER, 2))
        except (TypeError, ValueError):
            logger.error("ATR trailing skipped: invalid ATR input", ticket=pos.ticket, atr=atr)
            return False

        if distance <= 0.0:
            return False

        target_sl = (
            price_current - distance if pos.type == OrderType.BUY else price_current + distance
        )
        target_sl = round(target_sl, self.om._resolve_price_digits(symbol_info))

        if not self.om.is_sl_improvement(pos, target_sl):
            return False

        if current_tick is not None and min_stop_gap > 0.0:
            reference = current_tick.bid if pos.type == OrderType.BUY else current_tick.ask
            gap = (reference - target_sl) if pos.type == OrderType.BUY else (target_sl - reference)
            if gap < min_stop_gap:
                return False

        if not self.om._should_modify_sl(pos.ticket, target_sl):
            return False

        old_sl = pos.sl
        try:
            success = bool(
                self.om.adapter.modify_position(
                    ticket=pos.ticket, stop_loss=target_sl, take_profit=pos.tp
                )
            )
        except Exception as err:
            success = False
            logger.error("ATR TRAILING: modify_position raised", ticket=pos.ticket, error=str(err))

        if not success:
            return False

        # Only a CONFIRMED modification advances the tracked final SL (BUG-085).
        self.om._last_modify_sl[pos.ticket] = target_sl
        self.om._sl_modified_flags[pos.ticket] = True
        self.om._log_protection_audit(
            pos,
            action="ATR_TRAILING_STOP",
            reason=f"ATR_TRAILING atr={atr:.5f} multiplier={ATR_TRAILING_MULTIPLIER}",
            stop_loss=target_sl,
        )

        if self.om.notifier:
            try:
                self.om.notifier.notify_trailing_stop_advanced_extended(
                    ticket=pos.ticket,
                    old_sl=old_sl,
                    new_sl=target_sl,
                    current_price=price_current,
                    reply_to_message_id=self.om._order_message_ids.get(pos.ticket),
                )
            except Exception as err:
                logger.error("Trailing notification failed", ticket=pos.ticket, error=str(err))

        return True
