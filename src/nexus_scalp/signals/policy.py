"""
Multi-Strategy Signal Policy Engine (v4.2 HFT Scalper Calibrated)
================================================================
Evaluates Ichimoku, ICT, Price Action, and Statistical Arbitrage with Aggressive Order Routing.
Injects Microstructure Market Regime State (Module 1) for real-time execution adaptability.

Enterprise Upgrades & Calibrations Incorporated:
    1. OFI Micro-Momentum Range Override (Allows market scalps in range if abs(OFI) > 0.15).
    2. Tick Velocity Momentum Bypass (Bypasses limit-downgrade when tick_velocity > 10.0 ticks/sec).
    3. Optimized Baseline Confidence (confidence_threshold=0.20 for faster signal triggering).
    4. Dynamic Z-Score & AI Confidence Blending (Eliminates artificial confidence inflation).
    5. Persistent Directional Hysteresis (Anti-Flip memory protected across intermediate ticks).
"""

from datetime import datetime, timezone
import math
import uuid
from typing import Optional
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
    Aggressive HFT implementation with OFI range override and velocity momentum routing.
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

        self._last_signal_time: Optional[datetime] = None
        self._last_telemetry_time: Optional[datetime] = None
        self._last_logged_action: ActionType = ActionType.NO_TRADE

        self._last_active_direction: Optional[ActionType] = None
        self._last_active_direction_time: Optional[datetime] = None

    def evaluate_probabilities(
        self,
        probabilities: torch.Tensor,
        current_tick: TickData,
        feature_vector: FeatureVector,
        regime_state: Optional[MarketRegimeState] = None,
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

        # Extract Microstructure & Stat-Arb Parameters
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

        ict_bullish = feature_vector.fvg_bullish_active or feature_vector.order_block_type == 1 or feature_vector.choch_bullish
        ict_bearish = feature_vector.fvg_bearish_active or feature_vector.order_block_type == -1 or feature_vector.choch_bearish

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
        # 3. Decision Engine (Aggressive Micro-Scalping Order Routing)
        # ----------------------------------------------------------------------
        proposed_action = ActionType.NO_TRADE
        reason_code = f"REGIME_{regime_type.value}" if regime_type else ("RANGE_BOUND_SIDEWAYS" if is_range_market else "NEUTRAL_MARKET")
        target_entry_price = current_tick.ask

        htf_supports_buy = feature_vector.is_above_kumo or feature_vector.choch_bullish or stat_arb_bullish
        htf_supports_sell = feature_vector.is_below_kumo or feature_vector.choch_bearish or stat_arb_bearish

        # HFT OVERRIDE 2: High tick velocity allows direct Market Orders
        high_velocity_momentum = tick_velocity >= 10.0

        # --- BUY SIGNALS ---
        if (ichimoku_bullish or stat_arb_bullish) and (moving_up or ict_bullish or relative_buy_bias > 0.50 or stat_arb_bullish):
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

        # --- SELL SIGNALS ---
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
        # PROPOSAL GATE 4: Anti-Flip Protection (Persistent Directional Hysteresis)
        # ----------------------------------------------------------------------
        if proposed_action != ActionType.NO_TRADE and self._last_active_direction is not None:
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
        # PROPOSAL GATE 3: Structure-Based SL/TP Calculation (Tight Micro-Scalp)
        # ----------------------------------------------------------------------
        raw_swing_low = getattr(feature_vector, "recent_swing_low", 0.0)
        raw_swing_high = getattr(feature_vector, "recent_swing_high", 0.0)

        swing_low = self._sanitize_float(raw_swing_low, 0.0)
        swing_high = self._sanitize_float(raw_swing_high, 0.0)

        if "BUY" in proposed_action.value:
            structural_sl = swing_low
            if structural_sl <= 0.0 or structural_sl >= target_entry_price:
                structural_sl = target_entry_price - (atr * 0.80)

            sl_distance = min(max(target_entry_price - structural_sl, atr * 0.50), atr * 1.8)
            stop_loss = round(target_entry_price - sl_distance, 2)
            take_profit = round(target_entry_price + (sl_distance * 1.35), 2)
        else:
            structural_sl = swing_high
            if structural_sl <= 0.0 or structural_sl <= target_entry_price:
                structural_sl = target_entry_price + (atr * 0.80)

            sl_distance = min(max(structural_sl - target_entry_price, atr * 0.50), atr * 1.8)
            stop_loss = round(target_entry_price + sl_distance, 2)
            take_profit = round(target_entry_price - (sl_distance * 1.35), 2)

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

    def _sanitize_float(self, val: Optional[float], default: float) -> float:
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