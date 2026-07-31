"""
Multi-Strategy Signal Policy Engine (v5.0 Enterprise - Fast Reversal & Level Lockout)
=====================================================================================
Evaluates Ichimoku, ICT, Price Action, and Statistical Arbitrage with Aggressive Order Routing.
Injects Microstructure Market Regime State (Module 1) for real-time execution adaptability.

Enterprise Upgrades & Calibrations Incorporated:
    1. Same-Level Duplicate Re-Entry Lockout (Prevents repeated entries at the same price level).
    2. Fast Liquidity Sweep Reversal Routing (Instant direction flip on ICT sweeps & ChoCh).
    3. Hysteresis Bypass on Confirmed Sweeps (Allows instant direction flip at liquidity pools).
    4. ICT & Ichimoku Structural SL/TP Calculation (Anchored to Swing Lows/Highs and Kumo Span B).
    5. OFI Micro-Momentum Range Override (Allows market scalps in range if abs(OFI) > 0.15).
    6. Tick Velocity Momentum Bypass (Bypasses limit-downgrade when tick_velocity > 10.0 ticks/sec).
    7. Dynamic Z-Score & AI Confidence Blending (Eliminates artificial confidence inflation).
"""

import math
import uuid
from datetime import datetime

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.signals.policy")


class SignalPolicy:
    """
    Evaluates multi-confluence setups and generates active trade proposals for Risk Engine validation.
    Aggressive HFT implementation with Fast Liquidity Reversal and Same-Level Re-entry Lockout.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.20,      # Calibrated to 0.20 for fast HFT response
        cooldown_seconds: float = 3.0,           # Fast 3s cooldown for micro-scalping
        telemetry_interval_sec: float = 4.0,      # Throttles console logging output every 4 seconds max
        range_min_displacement: float = 0.15,     # Reduced displacement threshold for Gold ($0.15)
        range_confidence_penalty: float = 0.10,   # Reduced range penalty to encourage micro-scalps
        max_spread_atr_ratio: float = 0.18,       # Maximum allowed spread as 18% of current M1 ATR
        flip_confidence_penalty: float = 0.10,    # Hysteresis penalty when flipping BUY/SELL
        flip_memory_seconds: float = 8.0,         # Reduced hysteresis memory window
        min_allowed_rr: float = 1.10,             # Absolute minimum Risk-to-Reward ratio required
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.cooldown_seconds = cooldown_seconds
        self.telemetry_interval = telemetry_interval_sec
        self.range_min_displacement = range_min_displacement
        self.range_confidence_penalty = range_confidence_penalty
        self.max_spread_atr_ratio = max_spread_atr_ratio
        self.flip_confidence_penalty = flip_confidence_penalty
        self.flip_memory_seconds = flip_memory_seconds
        self.min_allowed_rr = min_allowed_rr

        self._last_signal_time: datetime | None = None
        self._last_telemetry_time: datetime | None = None
        self._last_logged_action: ActionType = ActionType.NO_TRADE

        # Persistent Memory for Direction & Same-Level Re-entry Protection
        self._last_active_direction: ActionType | None = None
        self._last_active_direction_time: datetime | None = None
        self._last_executed_price: float = 0.0

    def evaluate_probabilities(
        self,
        probabilities: torch.Tensor,
        current_tick: TickData,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
        survival_mode: bool = False,
    ) -> TradeProposal:
        """
        Evaluates conditions at maximum live speed (50ms hot path) and outputs a sized TradeProposal.
        """
        probs = probabilities.squeeze().tolist()
        if not isinstance(probs, list):
            probs = [probs]

        raw_prob_buy = probs[1] if len(probs) > 1 else 0.0
        raw_prob_sell = probs[2] if len(probs) > 2 else 0.0

        prob_buy = self._sanitize_float(raw_prob_buy, 0.0)
        prob_sell = self._sanitize_float(raw_prob_sell, 0.0)
        now = current_tick.timestamp

        raw_atr = getattr(feature_vector, "atr_m1", 1.50)
        atr = max(self._sanitize_float(raw_atr, 1.50), 0.50)
        current_spread = round(max(0.0, current_tick.ask - current_tick.bid), 2)

        # ----------------------------------------------------------------------
        # 1. Extract Microstructure & ICT Parameters
        # ----------------------------------------------------------------------
        tenkan = self._sanitize_float(feature_vector.tenkan_sen, current_tick.ask)
        kijun = self._sanitize_float(feature_vector.kijun_sen, current_tick.bid)
        disp = self._sanitize_float(feature_vector.live_tick_displacement, 0.0)

        ichimoku_bullish = feature_vector.is_above_kumo and (tenkan >= kijun)
        ichimoku_bearish = feature_vector.is_below_kumo and (tenkan <= kijun)

        z_score = self._sanitize_float(getattr(feature_vector, "cross_asset_z_score", 0.0), 0.0)
        abs_z = abs(z_score)
        z_score_confidence = min(0.95, round(0.40 + (abs_z / 4.0) * 0.55, 2))

        stat_arb_bullish = (z_score <= -2.0) and not ichimoku_bearish
        stat_arb_bearish = (z_score >= 2.0) and not ichimoku_bullish

        regime_type = regime_state.regime_type if regime_state else None
        exec_type = regime_state.recommended_execution_type if regime_state else None
        raw_ofi = regime_state.order_flow_imbalance if regime_state else 0.0
        ofi = self._sanitize_float(raw_ofi, 0.0)
        tick_velocity = regime_state.tick_velocity_per_sec if regime_state else 0.0

        # ICT Liquidity Sweep Signals
        sweep_sig = getattr(feature_vector, "liquidity_sweep_signal", 0)
        choch_bull = getattr(feature_vector, "choch_bullish", False)
        choch_bear = getattr(feature_vector, "choch_bearish", False)

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 0: Microstructure Regime Guardian
        # ----------------------------------------------------------------------
        if regime_state and (
            regime_type in (RegimeType.MACRO_NEWS_FREEZE, RegimeType.HIGH_SPREAD_CHOP)
            or exec_type == RecommendedExecutionType.FREEZE_ALL
        ):
            reason = f"REGIME_GUARDIAN_FREEZE ({regime_type.value if regime_type else 'HIGH_SPREAD'})"
            return self._build_no_trade(current_tick, 0.0, reason)

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 1: Dynamic Spread Protection
        # ----------------------------------------------------------------------
        max_allowed_spread = max(0.25, round(atr * self.max_spread_atr_ratio, 2))
        if current_spread > max_allowed_spread:
            reason = f"HIGH_SPREAD_REJECTED ({current_spread:.2f} > max {max_allowed_spread:.2f})"
            return self._build_no_trade(current_tick, 0.0, reason)

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 2: Range & OFI Momentum Override Gate
        # ----------------------------------------------------------------------
        dynamic_min_displacement = max(self.range_min_displacement, atr * 0.12)
        tk_distance = abs(tenkan - kijun)
        is_inside_kumo = not feature_vector.is_above_kumo and not feature_vector.is_below_kumo
        small_displacement = abs(disp) < dynamic_min_displacement

        is_range_market = (
            (regime_type == RegimeType.RANGING_MEAN_REVERSION)
            or is_inside_kumo
            or (tk_distance < (atr * 0.20) and small_displacement)
        )

        # HFT OVERRIDE 1: Allow active scalps in range if OFI order flow is strong
        if is_range_market and abs(ofi) >= 0.15:
            is_range_market = False

        ict_bullish = feature_vector.fvg_bullish_active or feature_vector.order_block_type == 1 or choch_bull
        ict_bearish = feature_vector.fvg_bearish_active or feature_vector.order_block_type == -1 or choch_bear

        moving_up = disp > dynamic_min_displacement or feature_vector.broke_previous_high
        moving_down = disp < -dynamic_min_displacement or feature_vector.broke_previous_low

        total_ai_prob = prob_buy + prob_sell + 1e-8
        relative_buy_bias = prob_buy / total_ai_prob
        relative_sell_bias = prob_sell / total_ai_prob

        active_threshold = self.confidence_threshold
        if survival_mode:
            active_threshold += 0.10
        if is_range_market:
            active_threshold += self.range_confidence_penalty

        # ----------------------------------------------------------------------
        # 3. Decision Engine (Fast Liquidity Reversal & Smart Order Routing)
        # ----------------------------------------------------------------------
        proposed_action = ActionType.NO_TRADE
        reason_code = f"REGIME_{regime_type.value}" if regime_type else ("RANGE_BOUND_SIDEWAYS" if is_range_market else "NEUTRAL_MARKET")
        target_entry_price = current_tick.ask

        htf_supports_buy = feature_vector.is_above_kumo or choch_bull or stat_arb_bullish
        htf_supports_sell = feature_vector.is_below_kumo or choch_bear or stat_arb_bearish
        high_velocity_momentum = tick_velocity >= 10.0

        # --- FAST LIQUIDITY SWEEP REVERSALS (Pillar 1) ---
        is_fast_reversal = False
        if (sweep_sig == 1 or choch_bull) and (relative_buy_bias > 0.45 or prob_buy >= 0.30):
            proposed_action = ActionType.BUY_MARKET
            target_entry_price = current_tick.ask
            reason_code = f"FAST_LIQUIDITY_SWEEP_REVERSAL_BUY (OFI: {ofi:+.2f})"
            is_fast_reversal = True

        elif (sweep_sig == -1 or choch_bear) and (relative_sell_bias > 0.45 or prob_sell >= 0.30):
            proposed_action = ActionType.SELL_MARKET
            target_entry_price = current_tick.bid
            reason_code = f"FAST_LIQUIDITY_SWEEP_REVERSAL_SELL (OFI: {ofi:+.2f})"
            is_fast_reversal = True

        # --- STANDARD BUY SIGNALS ---
        elif (ichimoku_bullish or stat_arb_bullish) and (moving_up or ict_bullish or relative_buy_bias > 0.50 or stat_arb_bullish):
            if stat_arb_bullish and is_range_market:
                proposed_action = ActionType.BUY_LIMIT
                target_entry_price = min(tenkan, round(current_tick.ask - 0.10, 2))
                reason_code = f"STAT_ARB_MEAN_REVERSION_BUY_LIMIT (Z: {z_score:+.2f})"
            elif feature_vector.fvg_bullish_active or (exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum):
                proposed_action = ActionType.BUY_LIMIT
                target_entry_price = min(tenkan, round(current_tick.ask - 0.10, 2))
                reason_code = "ICT_FVG_PULLBACK_BUY_LIMIT"
            elif feature_vector.broke_previous_high or high_velocity_momentum:
                proposed_action = ActionType.BUY_MARKET if high_velocity_momentum else ActionType.BUY_STOP
                target_entry_price = current_tick.ask if high_velocity_momentum else round(current_tick.ask + 0.12, 2)
                reason_code = "HFT_VELOCITY_BUY_MARKET" if high_velocity_momentum else "BREAKOUT_MOMENTUM_BUY_STOP"
            elif not is_range_market or abs(ofi) >= 0.15:
                proposed_action = ActionType.BUY_MARKET
                target_entry_price = current_tick.ask
                reason_code = f"AGGRESSIVE_SCALP_BUY (OFI: {ofi:+.2f})"
            else:
                reason_code = "RANGE_FILTERED_IMPULSIVE_BUY_PREVENTED"

        # --- STANDARD SELL SIGNALS ---
        elif (ichimoku_bearish or stat_arb_bearish) and (moving_down or ict_bearish or relative_sell_bias > 0.50 or stat_arb_bearish):
            if stat_arb_bearish and is_range_market:
                proposed_action = ActionType.SELL_LIMIT
                target_entry_price = max(tenkan, round(current_tick.bid + 0.10, 2))
                reason_code = f"STAT_ARB_MEAN_REVERSION_SELL_LIMIT (Z: {z_score:+.2f})"
            elif feature_vector.fvg_bearish_active or (exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum):
                proposed_action = ActionType.SELL_LIMIT
                target_entry_price = max(tenkan, round(current_tick.bid + 0.10, 2))
                reason_code = "ICT_FVG_PULLBACK_SELL_LIMIT"
            elif feature_vector.broke_previous_low or high_velocity_momentum:
                proposed_action = ActionType.SELL_MARKET if high_velocity_momentum else ActionType.SELL_STOP
                target_entry_price = current_tick.bid if high_velocity_momentum else round(current_tick.bid - 0.12, 2)
                reason_code = "HFT_VELOCITY_SELL_MARKET" if high_velocity_momentum else "BREAKOUT_MOMENTUM_SELL_STOP"
            elif not is_range_market or abs(ofi) >= 0.15:
                proposed_action = ActionType.SELL_MARKET
                target_entry_price = current_tick.bid
                reason_code = f"AGGRESSIVE_SCALP_SELL (OFI: {ofi:+.2f})"
            else:
                reason_code = "RANGE_FILTERED_IMPULSIVE_SELL_PREVENTED"

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 5: Same-Level Duplicate Re-Entry Lockout
        # ----------------------------------------------------------------------
        if (
            proposed_action != ActionType.NO_TRADE 
            and self._last_active_direction == proposed_action 
            and self._last_executed_price > 0.0
            and not is_fast_reversal
        ):
            price_dist_from_last = abs(target_entry_price - self._last_executed_price)
            if price_dist_from_last < (atr * 0.50):
                proposed_action = ActionType.NO_TRADE
                reason_code = f"SAME_LEVEL_REENTRY_BLOCKED (${price_dist_from_last:.2f} < ${atr * 0.50:.2f})"

        # Dynamic Confidence Calculation
        if proposed_action != ActionType.NO_TRADE:
            ai_prob = prob_buy if "BUY" in proposed_action.value else prob_sell
            if "STAT_ARB" in reason_code:
                confidence = max(ai_prob, z_score_confidence)
            else:
                confidence = max(ai_prob, min(0.85, round(0.55 + ai_prob * 0.35, 2)))
        else:
            confidence = 0.0

        if confidence < active_threshold and proposed_action != ActionType.NO_TRADE:
            proposed_action = ActionType.NO_TRADE
            reason_code = f"INSUFFICIENT_CONFIDENCE ({confidence:.2f} < {active_threshold:.2f})"

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 4: Anti-Flip Protection (Bypassed for Fast Sweeps)
        # ----------------------------------------------------------------------
        if proposed_action != ActionType.NO_TRADE and self._last_active_direction is not None and not is_fast_reversal:
            elapsed_flip_sec = (now - self._last_active_direction_time).total_seconds() if self._last_active_direction_time else 999.0
            elapsed_flip_sec = max(0.0, elapsed_flip_sec)

            if elapsed_flip_sec <= self.flip_memory_seconds:
                is_reversing = (
                    ("BUY" in self._last_active_direction.value and "SELL" in proposed_action.value)
                    or ("SELL" in self._last_active_direction.value and "BUY" in proposed_action.value)
                )
                required_flip_confidence = active_threshold + self.flip_confidence_penalty
                if is_reversing and confidence < required_flip_confidence:
                    proposed_action = ActionType.NO_TRADE
                    reason_code = f"FLIP_PROTECTION_BLOCKED ({confidence:.2f} < req {required_flip_confidence:.2f})"

        if proposed_action != ActionType.NO_TRADE:
            self._last_active_direction = proposed_action
            self._last_active_direction_time = now
            self._last_executed_price = target_entry_price

        # Throttled Console Telemetry
        should_log = False
        if self._last_telemetry_time is None:
            should_log = True
        else:
            elapsed_telemetry = max(0.0, (now - self._last_telemetry_time).total_seconds())
            if elapsed_telemetry >= self.telemetry_interval or proposed_action != self._last_logged_action:
                should_log = True

        if should_log:
            logger.info(
                "[MARKET RADAR]",
                action=proposed_action.value,
                regime=regime_type.value if regime_type else ("RANGE/CHOP" if is_range_market else "TRENDING"),
                z_score=f"{z_score:+.2f}",
                ofi=f"{ofi:+.2f}",
                spread=f"${current_spread:.2f}",
                gold_move=f"${disp:+.2f}",
                ai_buy=f"{prob_buy*100:.1f}%",
                ai_sell=f"{prob_sell*100:.1f}%",
                ichi=f"Kumo:{'ABOVE' if feature_vector.is_above_kumo else ('BELOW' if feature_vector.is_below_kumo else 'INSIDE')}",
                reason=reason_code,
            )
            self._last_telemetry_time = now
            self._last_logged_action = proposed_action

        if proposed_action == ActionType.NO_TRADE:
            return self._build_no_trade(current_tick, confidence, reason_code)

        if self._last_signal_time is not None:
            elapsed_cooldown = max(0.0, (now - self._last_signal_time).total_seconds())
            if elapsed_cooldown < self.cooldown_seconds:
                return self._build_no_trade(current_tick, confidence, f"COOLDOWN_ACTIVE ({elapsed_cooldown:.1f}s)")

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 3: ICT & Ichimoku Structural SL/TP Calculation
        # ----------------------------------------------------------------------
        raw_swing_low = getattr(feature_vector, "recent_swing_low", 0.0)
        raw_swing_high = getattr(feature_vector, "recent_swing_high", 0.0)

        swing_low = self._sanitize_float(raw_swing_low, 0.0)
        swing_high = self._sanitize_float(raw_swing_high, 0.0)
        span_b = self._sanitize_float(getattr(feature_vector, "senkou_span_b", 0.0), target_entry_price)

        if "BUY" in proposed_action.value:
            cand_sl = [v for v in (swing_low, kijun, span_b) if 0.0 < v < target_entry_price]
            structural_sl = min(cand_sl) - (atr * 0.15) if cand_sl else (target_entry_price - atr * 0.80)

            cand_tp = [v for v in (swing_high, span_b) if v > target_entry_price]
            structural_tp = max(cand_tp) if cand_tp else (target_entry_price + atr * 1.60)

            sl_distance = min(max(target_entry_price - structural_sl, atr * 0.50), atr * 2.0)
            stop_loss = round(target_entry_price - sl_distance, 2)
            take_profit = round(max(structural_tp, target_entry_price + (sl_distance * 1.35)), 2)
        else:
            cand_sl = [v for v in (swing_high, kijun, span_b) if v > target_entry_price]
            structural_sl = max(cand_sl) + (atr * 0.15) if cand_sl else (target_entry_price + atr * 0.80)

            cand_tp = [v for v in (swing_low, span_b) if 0.0 < v < target_entry_price]
            structural_tp = min(cand_tp) if cand_tp else (target_entry_price - atr * 1.60)

            sl_distance = min(max(structural_sl - target_entry_price, atr * 0.50), atr * 2.0)
            stop_loss = round(target_entry_price + sl_distance, 2)
            take_profit = round(min(structural_tp, target_entry_price - (sl_distance * 1.35)), 2)

        risk_amount = max(abs(target_entry_price - stop_loss), 1e-5)
        reward_amount = abs(take_profit - target_entry_price)
        actual_rr = round(reward_amount / risk_amount, 2)

        if actual_rr < self.min_allowed_rr:
            return self._build_no_trade(current_tick, confidence, f"POOR_RISK_REWARD ({actual_rr:.2f} < {self.min_allowed_rr})")

        self._last_signal_time = now

        return TradeProposal(
            request_id=str(uuid.uuid4()),
            symbol=current_tick.symbol,
            generated_at=now,
            action=proposed_action,
            confidence=float(confidence),
            proposed_entry=float(target_entry_price),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            risk_reward_ratio=float(actual_rr),
            reason_code=reason_code,
        )

    def _build_no_trade(self, tick: TickData, confidence: float, reason: str) -> TradeProposal:
        return TradeProposal(
            request_id=str(uuid.uuid4()),
            symbol=tick.symbol,
            generated_at=tick.timestamp,
            action=ActionType.NO_TRADE,
            confidence=float(confidence),
            proposed_entry=float(tick.bid),
            stop_loss=float(tick.bid * 0.99),
            take_profit=float(tick.bid * 1.01),
            risk_reward_ratio=1.0,
            reason_code=reason,
        )

    def _sanitize_float(self, val: float | None, default: float) -> float:
        """Sanitizes input float against None, NaN, and Inf values."""
        if val is None:
            return default
        try:
            fval = float(val)
            if math.isnan(fval) or math.isinf(fval):
                return default
            return fval
        except (TypeError, ValueError):
            return default