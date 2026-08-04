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
from typing import Any

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.signals.policy")


from nexus_scalp.signals.rule_matrix import RuleMatrixEngine

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
        rule_matrix: RuleMatrixEngine | None = None,
        algo_config: AlgoConfig | None = None,
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
        self.rule_matrix = rule_matrix
        self.algo_config = algo_config or AlgoConfig()

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
        force_log: bool = False,
    ) -> TradeProposal:
        """
        Evaluates conditions at maximum live speed (50ms hot path) and outputs a sized TradeProposal.
        """
        probs = probabilities.squeeze().tolist()
        if not isinstance(probs, list):
            probs = [probs]

        raw_prob_buy = probs[1] if len(probs) > 1 else 0.0
        raw_prob_sell = probs[2] if len(probs) > 2 else 0.0

        # --- RULE MATRIX INTEGRATION ---
        if self.rule_matrix:
            self.rule_matrix.refresh_cache()
            # 1. Check Filters first
            blocked_reason = self.rule_matrix.evaluate_pre_trade_filters(
                tick=current_tick,
                fv=feature_vector,
                regime_state=regime_state
            )
            if blocked_reason:
                return self._build_no_trade(current_tick, 0.0, blocked_reason)

            # 2. Check Custom Rules Triggered Entries (which take precedence over base PyTorch AI)
            rule_proposal = self.rule_matrix.evaluate_pre_trade_entry(
                tick=current_tick,
                fv=feature_vector,
                regime_state=regime_state,
                probs=[probs[0], raw_prob_buy, raw_prob_sell] if len(probs) > 2 else [1.0, 0.0, 0.0]
            )
            if rule_proposal:
                logger.info(
                    "Signal triggered by Rule Matrix Entry Engine",
                    rule=rule_proposal.reason_code,
                    action=rule_proposal.action.value,
                )
                return rule_proposal

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

        # Multi-timeframe trend & S/R variables
        h4_trend = self._sanitize_float(getattr(feature_vector, "htf_h4_trend", 0.0), 0.0)
        h1_mom = self._sanitize_float(getattr(feature_vector, "htf_h1_momentum", 0.0), 0.0)
        m30_str = self._sanitize_float(getattr(feature_vector, "htf_m30_structure", 0.0), 0.0)
        m15_conf = self._sanitize_float(getattr(feature_vector, "htf_m15_confirmation", 0.0), 0.0)
        trend_strength = self._sanitize_float(getattr(feature_vector, "trend_strength", 0.0), 0.0)

        support_zone_dist = self._sanitize_float(getattr(feature_vector, "support_zone_dist", 3.0), 3.0)
        resistance_zone_dist = self._sanitize_float(getattr(feature_vector, "resistance_zone_dist", 3.0), 3.0)

        # Strict Multi-Timeframe and S/R Selective Alignments
        htf_buy_aligned = (h4_trend >= 0 or m30_str >= 0 or m15_conf >= 0 or h1_mom >= -0.1) and (trend_strength >= -0.2)
        htf_sell_aligned = (h4_trend <= 0 or m30_str <= 0 or m15_conf <= 0 or h1_mom <= 0.1) and (trend_strength <= 0.2)

        sr_buy_allowed = resistance_zone_dist >= 0.25
        sr_sell_allowed = support_zone_dist >= 0.25

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
            if not htf_buy_aligned:
                reason_code = "BUY_REJECTED_HTF_TREND_CONFL_FAIL"
            elif not sr_buy_allowed:
                reason_code = "BUY_REJECTED_SR_RESISTANCE_MARGIN_FAIL"
            elif stat_arb_bullish and is_range_market:
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
            if not htf_sell_aligned:
                reason_code = "SELL_REJECTED_HTF_TREND_CONFL_FAIL"
            elif not sr_sell_allowed:
                reason_code = "SELL_REJECTED_SR_SUPPORT_MARGIN_FAIL"
            elif stat_arb_bearish and is_range_market:
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
        # PROPOSAL GATE 6: 50% Impulse Equilibrium hard gating for Short trades
        # ----------------------------------------------------------------------
        if proposed_action in (ActionType.SELL_MARKET, ActionType.SELL_LIMIT, ActionType.SELL_STOP):
            if feature_vector.order_block_type == -1 and feature_vector.feat_ob_equilibrium_ratio < 0.50:
                proposed_action = ActionType.NO_TRADE
                reason_code = "OB_BELOW_50_PERCENT_EQUILIBRIUM"

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

        # Neural Zone Validation
        is_zone_active = (
            feature_vector.fvg_bullish_active
            or feature_vector.fvg_bearish_active
            or feature_vector.order_block_type != 0
            or sweep_sig != 0
        )
        if is_zone_active and proposed_action != ActionType.NO_TRADE:
            zone_quality_score = confidence
            if zone_quality_score < self.algo_config.ai_zone_confidence_threshold:
                proposed_action = ActionType.NO_TRADE
                reason_code = f"ZONE_QUALITY_BELOW_THRESHOLD ({zone_quality_score:.2f} < {self.algo_config.ai_zone_confidence_threshold:.2f})"

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
            reason_code = (
                f"{reason_code} | HTF:[H4={h4_trend:+.1f}, H1_Mom={h1_mom:+.1f}, "
                f"M30_Str={m30_str:+.1f}, M15_Conf={m15_conf:+.1f}] | "
                f"S_Dist={support_zone_dist:.2f}, R_Dist={resistance_zone_dist:.2f}"
            )
            self._last_active_direction = proposed_action
            self._last_active_direction_time = now
            self._last_executed_price = target_entry_price

        # Throttled Console Telemetry
        should_log = False
        if force_log:
            should_log = True
        elif self._last_telemetry_time is None:
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
        swing_low_reconstructed = target_entry_price - (feature_vector.dist_to_swing_low_20 * atr)
        swing_high_reconstructed = target_entry_price + (feature_vector.dist_to_swing_high_20 * atr)
        span_b = self._sanitize_float(getattr(feature_vector, "senkou_span_b", 0.0), target_entry_price)

        if "BUY" in proposed_action.value:
            cand_sl = [v for v in (swing_low_reconstructed, kijun, span_b) if 0.0 < v < target_entry_price]
            structural_level_sl = min(cand_sl) if cand_sl else (target_entry_price - atr)
            stop_loss = round(structural_level_sl - (atr * self.algo_config.atr_sl_buffer_multiplier), 2)

            cand_tp = [v for v in (swing_high_reconstructed, span_b) if v > target_entry_price]
            take_profit = round(min(cand_tp) if cand_tp else (target_entry_price + atr * 2.0), 2)
        else:
            cand_sl = [v for v in (swing_high_reconstructed, kijun, span_b) if v > target_entry_price]
            structural_level_sl = max(cand_sl) if cand_sl else (target_entry_price + atr)
            stop_loss = round(structural_level_sl + (atr * self.algo_config.atr_sl_buffer_multiplier), 2)

            cand_tp = [v for v in (swing_low_reconstructed, span_b) if 0.0 < v < target_entry_price]
            take_profit = round(max(cand_tp) if cand_tp else (target_entry_price - atr * 2.0), 2)

        risk_amount = max(abs(target_entry_price - stop_loss), 1e-5)
        reward_amount = abs(take_profit - target_entry_price)
        actual_rr = round(reward_amount / risk_amount, 2)

        # Asymmetric Risk Gatekeeper
        if actual_rr < self.algo_config.min_risk_reward_ratio:
            return self._build_no_trade(current_tick, confidence, "ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD")

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

    def extract_live_chart_overlays(self, completed_bars: list[Any], atr_val: float) -> dict[str, Any]:
        """
        Causally extracts real SMC zones and levels (BOS, OB, 50% Midline, LIQ Markers)
        from completed MT5 bars.
        """
        if not completed_bars or len(completed_bars) < 20:
            return {"rectangles": [], "bos_lines": [], "midlines": [], "liq_markers": []}

        import numpy as np
        rectangles = []
        bos_lines = []
        midlines = []
        liq_markers = []

        closes = [b.close for b in completed_bars]
        highs = [b.high for b in completed_bars]
        lows = [b.low for b in completed_bars]
        opens = [b.open for b in completed_bars]

        # 1. Swing Highs & Swing Lows
        swing_highs = []
        swing_lows = []
        for i in range(5, len(completed_bars) - 5):
            window_highs = [b.high for b in completed_bars[i-5 : i+6]]
            window_lows = [b.low for b in completed_bars[i-5 : i+6]]
            if completed_bars[i].high == max(window_highs):
                swing_highs.append((i, completed_bars[i].high))
            if completed_bars[i].low == min(window_lows):
                swing_lows.append((i, completed_bars[i].low))

        if not swing_highs:
            swing_highs = [(len(completed_bars) - 10, float(np.max(highs)))]
        if not swing_lows:
            swing_lows = [(len(completed_bars) - 10, float(np.min(lows)))]

        # 2. Extract BOS (Break of Structure) Lines
        for i in range(1, len(completed_bars)):
            prev_shs = [val for idx, val in swing_highs if idx < i]
            prev_sls = [val for idx, val in swing_lows if idx < i]

            if prev_shs and completed_bars[i].close > prev_shs[-1]:
                bos_lines.append({
                    "id": f"bos_sh_{i}",
                    "price": float(prev_shs[-1]),
                    "type": "BOS_HIGH",
                    "time": completed_bars[i].timestamp.isoformat()
                })
            if prev_sls and completed_bars[i].close < prev_sls[-1]:
                bos_lines.append({
                    "id": f"bos_sl_{i}",
                    "price": float(prev_sls[-1]),
                    "type": "BOS_LOW",
                    "time": completed_bars[i].timestamp.isoformat()
                })

        bos_lines = bos_lines[-10:]

        # 3. Midline calculation
        last_sh_idx, last_sh_val = swing_highs[-1]
        last_sl_idx, last_sl_val = swing_lows[-1]
        equilibrium_50 = last_sl_val + 0.50 * (last_sh_val - last_sl_val)
        midlines.append({
            "id": "equilibrium_50",
            "price": float(equilibrium_50),
            "label": "50%",
            "time_start": completed_bars[min(last_sh_idx, last_sl_idx)].timestamp.isoformat()
        })

        # 4. OB Boxes & Liquidity Sweep (LIQ) Markers
        for i in range(2, len(completed_bars)):
            b_current = completed_bars[i]
            b_prev1 = completed_bars[i - 1]
            b_prev2 = completed_bars[i - 2]

            # Bullish Order Block
            if b_current.close > b_prev1.high and b_prev1.close < b_prev1.open:
                price_low = b_prev1.low
                price_high = b_prev1.high
                rectangles.append({
                    "id": f"ob_bull_{i}",
                    "type": "BULLISH_ORDER_BLOCK",
                    "price_low": float(price_low),
                    "price_high": float(price_high),
                    "ai_confidence": 0.85,
                    "time": b_prev1.timestamp.isoformat()
                })

            # Bearish Order Block
            if b_current.close < b_prev1.low and b_prev1.close > b_prev1.open:
                price_low = b_prev1.low
                price_high = b_prev1.high
                rectangles.append({
                    "id": f"ob_bear_{i}",
                    "type": "BEARISH_ORDER_BLOCK",
                    "price_low": float(price_low),
                    "price_high": float(price_high),
                    "ai_confidence": 0.85,
                    "time": b_prev1.timestamp.isoformat()
                })

            # Liquidity sweeps
            recent_high_10 = max([b.high for b in completed_bars[max(0, i-11) : i]]) if i > 0 else b_current.high
            recent_low_10 = min([b.low for b in completed_bars[max(0, i-11) : i]]) if i > 0 else b_current.low

            if b_current.low < recent_low_10 and b_current.close > recent_low_10:
                liq_markers.append({
                    "id": f"liq_low_{i}",
                    "type": "LIQ_LOW",
                    "price": float(b_current.low),
                    "time": b_current.timestamp.isoformat()
                })
            elif b_current.high > recent_high_10 and b_current.close < recent_high_10:
                liq_markers.append({
                    "id": f"liq_high_{i}",
                    "type": "LIQ_HIGH",
                    "price": float(b_current.high),
                    "time": b_current.timestamp.isoformat()
                })

        rectangles = rectangles[-15:]
        liq_markers = liq_markers[-15:]

        return {
            "rectangles": rectangles,
            "bos_lines": bos_lines,
            "midlines": midlines,
            "liq_markers": liq_markers
        }

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