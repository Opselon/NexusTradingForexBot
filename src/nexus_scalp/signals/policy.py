"""
Multi-Strategy Signal Policy Engine (v5.0 Enterprise - Fast Reversal & Level Lockout)
=====================================================================================
Evaluates Ichimoku, ICT, Price Action, and Statistical Arbitrage with Aggressive Order Routing.
Injects Microstructure Market Regime State (Module 1) for real-time execution adaptability.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import torch

from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine

logger = get_logger("nexus_scalp.signals.policy")

# =============================================================================
# MODULE B INVARIANTS (mirrored in execution/order_manager.py)
# =============================================================================

#: Max simultaneous exposure: 1 active position OR 1 pending order, engine-wide.
MAX_TOTAL_EXPOSURE: int = 1

#: Pending limit orders are immune to cancel/recreate churn for this long.
PENDING_ORDER_LOCK_SECONDS: float = 30.0

#: Reason code emitted with CLOSE_POSITION when the AI flips direction on us.
AI_REVERSAL_REASON: str = "AI_REVERSAL_SIGNAL"


class SignalPolicy:
    """
    Evaluates multi-confluence setups and generates active trade proposals for Risk Engine validation.
    Aggressive HFT implementation with Fast Liquidity Reversal and Same-Level Re-entry Lockout.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.20,  # Calibrated to 0.20 for fast HFT response
        cooldown_seconds: float = 3.0,  # Fast 3s cooldown for micro-scalping
        telemetry_interval_sec: float = 4.0,  # Throttles console logging output every 4 seconds max
        range_min_displacement: float = 0.15,  # Reduced displacement threshold for Gold ($0.15)
        range_confidence_penalty: float = 0.10,  # Reduced range penalty to encourage micro-scalps
        max_spread_atr_ratio: float = 0.18,  # Maximum allowed spread as 18% of current M1 ATR
        flip_confidence_penalty: float = 0.10,  # Hysteresis penalty when flipping BUY/SELL
        flip_memory_seconds: float = 8.0,  # Reduced hysteresis memory window
        min_allowed_rr: float = 1.10,  # Absolute minimum Risk-to-Reward ratio required
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
        self.last_order_price: float | None = None
        self.last_order_time: datetime | None = None

        # Part 3: Tick level sweep trackers
        self._last_tick_bid = 0.0
        self._last_tick_ask = 0.0
        self._last_tick_time: datetime | None = None
        # De-duplication guard: ignore re-evaluated ticks that carry the same timestamp
        # or the same bid/ask quote (the hot path can be called faster than the feed
        # ticks, producing duplicate evaluations within <100ms and log corruption).
        self._dedup_last_time: datetime | None = None
        self._dedup_last_bid: float = 0.0
        self._dedup_last_ask: float = 0.0
        # BUG-169: last NON-duplicate evaluation's proposal, surfaced on a
        # duplicate tick instead of a synthetic NO_TRADE conf=0.0 (which the
        # UI displayed as the Active Intelligence Output).
        self._last_real_proposal: TradeProposal | None = None

    def evaluate_probabilities(
        self,
        probabilities: torch.Tensor,
        current_tick: TickData,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
        survival_mode: bool = False,
        force_log: bool = False,
        order_manager: Any = None,
        completed_bars: list[Any] | None = None,
    ) -> TradeProposal:
        """
        Evaluates conditions at maximum live speed (50ms hot path) and outputs a sized TradeProposal.
        """
        # Forensic execution trace id (PHASE 13 audit, 2026-08-20): ONE id per
        # evaluation, stamped BEFORE any gate, carried into every proposal the
        # policy emits (NO_TRADE included) so logs + audit rows + dispatch are
        # joinable by a single EXEC-... key. Observability only (INV-018) —
        # never influences a decision.
        now_exec = current_tick.timestamp
        execution_id = f"EXEC-{now_exec:%Y%m%d}-{now_exec:%H%M%S}-{uuid.uuid4().hex[:6]}"
        guardian_proposal = self._evaluate_guardian_gate(regime_state, current_tick, execution_id)
        if guardian_proposal is not None:
            return guardian_proposal

        probs = probabilities.squeeze().tolist()
        if not isinstance(probs, list):
            probs = [probs]

        dedup_proposal = self._evaluate_duplicate_tick(
            probs, current_tick, execution_id, regime_state
        )
        if dedup_proposal is not None:
            return dedup_proposal

        raw_prob_buy = probs[1] if len(probs) > 1 else 0.0
        raw_prob_sell = probs[2] if len(probs) > 2 else 0.0

        # CONFIDENCE-SEMANTICS REPAIR (2026-09-02, Hermes-Main): the
        # serving head is 4 logits (NO_TRADE/BUY/SELL/WAIT) but the label
        # contract is 3-class - WAIT is a policy-bridge state that has
        # NEVER been a training label (label census zero; online fine-tune
        # class_counts [.., 0]). Comparing the raw 4-class directional
        # probability against thresholds calibrated for the trained classes
        # made the confidence gate mathematically impassable (0/464
        # candidates; all-time max raw probability 0.357 < the 0.40 base
        # threshold). Normalize the leading directional probability over
        # the TRAINED classes (BUY+SELL+NO_TRADE) so the gate measures the
        # model's actual trained semantics. A degenerate vector falls back
        # to the raw value instead of manufacturing confidence. Candidate
        # channels / flip-reversal logic still consume the RAW
        # probabilities below - unchanged.
        confidence, confidence_source = self._directional_confidence(probs)

        # --- PRE-COMPUTE CHANNELS AND PARAMETERS UPFRONT FOR DIAGNOSTICS ---
        prob_buy = self._sanitize_float(raw_prob_buy, 0.0)
        prob_sell = self._sanitize_float(raw_prob_sell, 0.0)
        # CONFIDENCE-SEMANTICS REPAIR: sanitize like prob_buy/prob_sell -
        # a NaN/inf slice from the model must not poison the candidate
        # measure (NaN mass -> NaN confidence -> proposal build failure).
        prob_no_trade = self._sanitize_float(probs[0] if len(probs) > 0 else 0.0, 0.0)

        now = current_tick.timestamp
        target_entry_price = current_tick.ask
        proposed_action = ActionType.NO_TRADE

        raw_atr = getattr(feature_vector, "atr_m1", 1.50)
        atr = max(self._sanitize_float(raw_atr, 1.50), 0.50)
        current_spread = round(max(0.0, current_tick.ask - current_tick.bid), 2)

        regime_type = regime_state.regime_type if regime_state else None
        regime_str = regime_type.value if regime_type else "UNKNOWN"
        regime_conf = float(regime_state.regime_probability) if regime_state else 0.0

        (
            active_positions_count,
            active_pending_count,
            pending_price,
            pending_ticket,
            live_tickets,
            held_position_dirs,
        ) = self._get_active_tickets_info(order_manager)

        # Guard against construction paths that bypass __init__: these
        # attributes must exist before the pending-order lock below reads them.
        if not hasattr(self, "_locked_pending_ticket"):
            self._locked_pending_ticket = None
        if not hasattr(self, "_locked_pending_price"):
            self._locked_pending_price = None
        if not hasattr(self, "_locked_pending_time"):
            self._locked_pending_time = None
        if not hasattr(self, "_last_signal_time"):
            self._last_signal_time = None

        # Same-level re-entry lockout: pin the pending order (ticket, price,
        # time) while it exists so no second entry at the same level can be
        # generated until the pending order resolves.
        if pending_ticket is not None:
            if self._locked_pending_ticket != pending_ticket:
                self._locked_pending_ticket = pending_ticket
                self._locked_pending_price = pending_price
                self._locked_pending_time = now
        else:
            self._locked_pending_ticket = None
            self._locked_pending_price = None
            self._locked_pending_time = None

        # ======================================================================
        # MODULE B: AI POSITION REVERSAL PROTOCOL (evaluated FIRST)
        # ======================================================================
        # If we hold an active position and the model now argues strongly for the
        # opposite direction, we must NOT stack an opposing order. We emit
        # CLOSE_POSITION with reason AI_REVERSAL_SIGNAL; order_manager closes the
        # ticket, stamps exit_mechanism=AI_REVERSAL_EXIT in the ledger, and only then
        # dispatches the new directional order.
        #
        # This gate runs before the frequency throttle, the same-level re-entry lockout
        # and the exposure gate: closing a position that the model has turned against
        # is risk-reducing and must never be suppressed by an entry-side filter.
        if active_positions_count >= 1 and held_position_dirs:
            reversal_proposal = self._evaluate_ai_reversal(
                current_tick=current_tick,
                feature_vector=feature_vector,
                held_position_dirs=held_position_dirs,
                prob_buy=prob_buy,
                prob_sell=prob_sell,
                no_trade_prob=prob_no_trade,
                atr=atr,
                regime_str=regime_str,
                regime_conf=regime_conf,
            )
            if reversal_proposal is not None:
                self._last_signal_time = now
                self._last_active_direction = reversal_proposal.reversal_action
                self._last_active_direction_time = now
                return reversal_proposal

        throttle_proposal = self._evaluate_frequency_throttle(
            now, current_tick, regime_str, regime_conf
        )
        if throttle_proposal is not None:
            return throttle_proposal

        total_exposure = active_positions_count + active_pending_count
        exposure_proposal = self._evaluate_exposure_limits(
            total_exposure,
            active_positions_count,
            active_pending_count,
            order_manager,
            live_tickets,
            target_entry_price,
            current_tick,
            regime_str,
            regime_conf,
            atr,
            completed_bars,
            now,
        )
        if exposure_proposal is not None:
            return exposure_proposal

        tenkan = self._sanitize_float(feature_vector.tenkan_sen, current_tick.ask)
        kijun = self._sanitize_float(feature_vector.kijun_sen, current_tick.bid)
        disp = self._sanitize_float(feature_vector.live_tick_displacement, 0.0)

        ichimoku_bullish = feature_vector.is_above_kumo and (tenkan >= kijun)
        ichimoku_bearish = feature_vector.is_below_kumo and (tenkan <= kijun)

        z_score = self._sanitize_float(getattr(feature_vector, "cross_asset_z_score", 0.0), 0.0)
        abs_z = abs(z_score)
        z_score_confidence = min(0.95, round(0.40 + (abs_z / 4.0) * 0.55, 2))

        trend_strength = self._sanitize_float(getattr(feature_vector, "trend_strength", 0.0), 0.0)

        stat_arb_bullish = (z_score <= -2.0) and not ichimoku_bearish and (trend_strength >= -0.20)
        stat_arb_bearish = (z_score >= 2.0) and not ichimoku_bullish and (trend_strength <= 0.20)

        regime_type = regime_state.regime_type if regime_state else None
        exec_type = regime_state.recommended_execution_type if regime_state else None
        raw_ofi = regime_state.order_flow_imbalance if regime_state else 0.0
        ofi = self._sanitize_float(raw_ofi, 0.0)
        tick_velocity = regime_state.tick_velocity_per_sec if regime_state else 0.0

        sweep_sig = getattr(feature_vector, "liquidity_sweep_signal", 0)
        choch_bull = getattr(feature_vector, "choch_bullish", False)
        choch_bear = getattr(feature_vector, "choch_bearish", False)

        dynamic_min_displacement = max(self.range_min_displacement, atr * 0.12)
        tk_distance = abs(tenkan - kijun)
        is_inside_kumo = not feature_vector.is_above_kumo and not feature_vector.is_below_kumo
        small_displacement = abs(disp) < dynamic_min_displacement

        # BUG-230: the classifier's WARMUP state (first min_ticks_for_stats
        # ticks) emits RANGING_MEAN_REVERSION with prob 0.50 as a synthetic
        # placeholder - NOT a confirmed range market. Treating it as one let
        # the stat-arb LIMIT channel and the range confidence penalty fire on
        # synthetic state before the regime engine had any real data. The
        # engine-level HTF warmup gate already fail-closes entries during
        # model warmup; this closes the regime-side seam (positions managed
        # by other paths keep their protective logic - this only affects the
        # range classification of NEW candidates).
        _warmup_regime = bool(regime_state and regime_state.reason == RegimeReason.WARMUP)
        is_range_market = (
            (regime_type == RegimeType.RANGING_MEAN_REVERSION and not _warmup_regime)
            or is_inside_kumo
            or (tk_distance < (atr * 0.20) and small_displacement)
        )

        if is_range_market and abs(ofi) >= 0.15:
            is_range_market = False

        ict_bullish = (
            feature_vector.fvg_bullish_active or feature_vector.order_block_type == 1 or choch_bull
        )
        ict_bearish = (
            feature_vector.fvg_bearish_active or feature_vector.order_block_type == -1 or choch_bear
        )

        moving_up = disp > dynamic_min_displacement or feature_vector.broke_previous_high
        moving_down = disp < -dynamic_min_displacement or feature_vector.broke_previous_low

        total_ai_prob = prob_buy + prob_sell + 1e-8
        relative_buy_bias = prob_buy / total_ai_prob
        relative_sell_bias = prob_sell / total_ai_prob
        high_velocity_momentum = tick_velocity >= 10.0

        regime_str = regime_type.value if regime_type else "UNKNOWN"
        regime_conf = regime_state.regime_probability if regime_state else 0.0

        is_guardian_active = bool(
            regime_state
            and (
                regime_type in (RegimeType.MACRO_NEWS_FREEZE, RegimeType.HIGH_SPREAD_CHOP)
                or exec_type == RecommendedExecutionType.FREEZE_ALL
            )
        )
        guardian_status = "ACTIVE" if is_guardian_active else "IDLE"

        execution_mode = "STANDARD"
        override_reason = None
        blocked_by = None
        decision_stage = "STANDARD_EVAL"

        # Pre-compute original unfiltered candidate model action
        cand_action = "NO_TRADE"
        if (sweep_sig == 1 or choch_bull) and (relative_buy_bias > 0.45 or prob_buy >= 0.30):
            cand_action = "BUY_MARKET"
        elif (sweep_sig == -1 or choch_bear) and (relative_sell_bias > 0.45 or prob_sell >= 0.30):
            cand_action = "SELL_MARKET"
        elif (ichimoku_bullish or stat_arb_bullish) and (
            moving_up or ict_bullish or relative_buy_bias > 0.50 or stat_arb_bullish
        ):
            if stat_arb_bullish and is_range_market:
                cand_action = "BUY_LIMIT"
            elif feature_vector.fvg_bullish_active or (
                exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum
            ):
                cand_action = "BUY_LIMIT"
            elif feature_vector.broke_previous_high or high_velocity_momentum:
                cand_action = "BUY_MARKET" if high_velocity_momentum else "BUY_STOP"
            elif not is_range_market or abs(ofi) >= 0.15:
                cand_action = "BUY_MARKET"
        elif (ichimoku_bearish or stat_arb_bearish) and (
            moving_down or ict_bearish or relative_sell_bias > 0.50 or stat_arb_bearish
        ):
            if stat_arb_bearish and is_range_market:
                cand_action = "SELL_LIMIT"
            elif feature_vector.fvg_bearish_active or (
                exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum
            ):
                cand_action = "SELL_LIMIT"
            elif feature_vector.broke_previous_low or high_velocity_momentum:
                cand_action = "SELL_MARKET" if high_velocity_momentum else "SELL_STOP"
            elif not is_range_market or abs(ofi) >= 0.15:
                cand_action = "SELL_MARKET"

        # CONFIDENCE-SEMANTICS REPAIR: a candidate is measured with the
        # directional (trained-class) confidence computed at the head of
        # this method; the 0.0 default only applies when no candidate
        # exists (pure NO_TRADE rows keep conf 0.0 as before).
        confidence = confidence if cand_action != "NO_TRADE" else 0.0

        # Calculate candidate confidence
        cand_confidence = 0.0
        if cand_action != "NO_TRADE":
            # BUG-245 (2026-09-05, Agent-4): the CHG-0042 directional
            # semantics already guard every degenerate denominator
            # (trained_mass <= 0 / non-finite / negative -> RAW_FALLBACK)
            # in _directional_confidence. This site re-implemented the
            # same division without any guard, so a zero/negative trained
            # mass with any structural candidate channel fires a live
            # ZeroDivisionError. Align the per-candidate measure with
            # the centralized degenerate handler: degenerate -> raw
            # OWN-side probability (never manufactures confidence, never
            # crashes, never emits a negative value into the frozen
            # TradeProposal model).
            cand_ai_prob = max(0.0, prob_buy) if "BUY" in cand_action else max(0.0, prob_sell)
            cand_trained_mass = prob_buy + prob_sell + prob_no_trade
            if cand_trained_mass > 0.0 and math.isfinite(cand_trained_mass):
                _cand_ratio = cand_ai_prob / cand_trained_mass
                if math.isfinite(_cand_ratio) and _cand_ratio >= 0.0:
                    cand_ai_prob = _cand_ratio
            is_stat_arb = "STAT_ARB" in cand_action or (
                stat_arb_bullish if "BUY" in cand_action else stat_arb_bearish
            )
            if is_stat_arb:
                cand_confidence = max(cand_ai_prob, z_score_confidence)
            else:
                # TRADE QUALITY FIX (2026-08-18, perf forensics): the old
                # `max(prob, 0.55 + prob*0.35)` floor inflated EVERY candidate
                # to >= 0.61, so the confidence gate never rejected weak
                # signals - the ledger shows 192/233 trades entered at
                # conf 0.0-0.4 and the bulk of the >$4.7k loss. Confidence is
                # now the REAL directional model probability (the honest
                # signal), so a 0.30 signal is rejected by the 0.35 gate and
                # only genuinely strong probabilities reach execution.
                cand_confidence = cand_ai_prob

        confidence = cand_confidence
        confidence_before_filters = confidence

        is_buy_cand = "BUY" in cand_action
        is_sell_cand = "SELL" in cand_action
        cand_stop_loss = None
        cand_take_profit = None
        cand_actual_rr = None

        if is_buy_cand or is_sell_cand:
            # Validate input types to prevent silent errors and expose invalid mock issues
            def is_numeric(val: Any) -> bool:
                if val is None:
                    return False
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return math.isfinite(float(val))
                return False

            if not is_numeric(target_entry_price):
                raise ValueError(f"Invalid entry price: {target_entry_price}")
            if not is_numeric(atr):
                raise ValueError(f"Invalid ATR value: {atr}")

            dist_sl_20 = getattr(feature_vector, "dist_to_swing_low_20", 1.0)
            dist_sh_20 = getattr(feature_vector, "dist_to_swing_high_20", 1.0)
            if not is_numeric(dist_sl_20):
                raise ValueError(f"Invalid dist_to_swing_low_20: {dist_sl_20}")
            if not is_numeric(dist_sh_20):
                raise ValueError(f"Invalid dist_to_swing_high_20: {dist_sh_20}")

            swing_low_reconstructed = target_entry_price - (dist_sl_20 * atr)
            swing_high_reconstructed = target_entry_price + (dist_sh_20 * atr)
            span_b = self._sanitize_float(
                getattr(feature_vector, "senkou_span_b", 0.0), target_entry_price
            )

            if is_buy_cand:
                cand_sl_levels = [
                    v
                    for v in (swing_low_reconstructed, kijun, span_b)
                    if is_numeric(v) and 0.0 < v < target_entry_price
                ]
                structural_level_sl = (
                    max(cand_sl_levels) if cand_sl_levels else (target_entry_price - atr)
                )
                cand_stop_loss = round(
                    structural_level_sl - (atr * self.algo_config.atr_sl_buffer_multiplier), 2
                )

                cand_tp_levels = [
                    v
                    for v in (swing_high_reconstructed, span_b)
                    if is_numeric(v) and v > target_entry_price
                ]
                cand_take_profit = round(
                    max(cand_tp_levels) if cand_tp_levels else (target_entry_price + atr * 2.0), 2
                )
            else:
                cand_sl_levels = [
                    v
                    for v in (swing_high_reconstructed, kijun, span_b)
                    if is_numeric(v) and v > target_entry_price
                ]
                structural_level_sl = (
                    min(cand_sl_levels) if cand_sl_levels else (target_entry_price + atr)
                )
                cand_stop_loss = round(
                    structural_level_sl + (atr * self.algo_config.atr_sl_buffer_multiplier), 2
                )

                cand_tp_levels = [
                    v
                    for v in (swing_low_reconstructed, span_b)
                    if is_numeric(v) and 0.0 < v < target_entry_price
                ]
                cand_take_profit = round(
                    min(cand_tp_levels) if cand_tp_levels else (target_entry_price - atr * 2.0), 2
                )

            # Ensure take_profit satisfies at least the minimum allowed RR to guarantee reward > risk
            risk_amount = max(abs(target_entry_price - cand_stop_loss), 1e-5)
            active_min_rr = getattr(self.algo_config, "min_risk_reward_ratio", 1.8)
            if cand_confidence >= getattr(self.algo_config, "high_confidence_threshold", 0.95):
                min_rr_hc = getattr(self.algo_config, "min_rr_high_confidence", 1.2)
                active_min_rr = min(active_min_rr, min_rr_hc)

            # Use smaller of min_allowed_rr and active_min_rr to avoid over-adjusting targets
            active_tp_rr = min(self.min_allowed_rr, active_min_rr)
            min_required_tp_dist = risk_amount * active_tp_rr
            if is_buy_cand:
                if cand_take_profit < target_entry_price + min_required_tp_dist:
                    cand_take_profit = round(target_entry_price + min_required_tp_dist, 2)
            elif cand_take_profit > target_entry_price - min_required_tp_dist:
                cand_take_profit = round(target_entry_price - min_required_tp_dist, 2)

            reward_amount = abs(cand_take_profit - target_entry_price)
            cand_actual_rr = round(reward_amount / risk_amount, 2)

        # Ensure we sanitize/convert mock objects to safe types to prevent TypeError in God Mode
        feat_ob_valid_bos = getattr(feature_vector, "feat_ob_valid_bos", 0.0)
        if (
            hasattr(feat_ob_valid_bos, "__class__")
            and "Mock" in feat_ob_valid_bos.__class__.__name__
        ):
            feat_ob_valid_bos = 0.0
        else:
            feat_ob_valid_bos = self._sanitize_float(feat_ob_valid_bos, 0.0)

        order_block_type = getattr(feature_vector, "order_block_type", 0)
        if hasattr(order_block_type, "__class__") and "Mock" in order_block_type.__class__.__name__:
            order_block_type = 0
        else:
            order_block_type = int(self._sanitize_float(order_block_type, 0.0))

        choch_bullish = getattr(feature_vector, "choch_bullish", False)
        if hasattr(choch_bullish, "__class__") and "Mock" in choch_bullish.__class__.__name__:
            choch_bullish = False
        else:
            choch_bullish = bool(choch_bullish)

        choch_bearish = getattr(feature_vector, "choch_bearish", False)
        if hasattr(choch_bearish, "__class__") and "Mock" in choch_bearish.__class__.__name__:
            choch_bearish = False
        else:
            choch_bearish = bool(choch_bearish)

        fvg_bullish_active = getattr(feature_vector, "fvg_bullish_active", False)
        if (
            hasattr(fvg_bullish_active, "__class__")
            and "Mock" in fvg_bullish_active.__class__.__name__
        ):
            fvg_bullish_active = False
        else:
            fvg_bullish_active = bool(fvg_bullish_active)

        fvg_bearish_active = getattr(feature_vector, "fvg_bearish_active", False)
        if (
            hasattr(fvg_bearish_active, "__class__")
            and "Mock" in fvg_bearish_active.__class__.__name__
        ):
            fvg_bearish_active = False
        else:
            fvg_bearish_active = bool(fvg_bearish_active)

        liquidity_sweep_signal = getattr(feature_vector, "liquidity_sweep_signal", 0)
        if (
            hasattr(liquidity_sweep_signal, "__class__")
            and "Mock" in liquidity_sweep_signal.__class__.__name__
        ):
            liquidity_sweep_signal = 0
        else:
            liquidity_sweep_signal = int(self._sanitize_float(liquidity_sweep_signal, 0.0))

        # ======================================================================
        # PART 1: SMC GOD MODE EXECUTION
        # ======================================================================
        has_bos = feat_ob_valid_bos > 0.0
        has_choch = choch_bull or choch_bear or choch_bullish or choch_bearish
        valid_ob = order_block_type != 0
        zone_quality_thresh = self.algo_config.ai_zone_confidence_threshold
        has_sweep = (sweep_sig != 0) or (liquidity_sweep_signal != 0)
        has_fvg = fvg_bullish_active or fvg_bearish_active

        smc_god_mode_active = False
        if (
            (has_bos or has_choch)
            and valid_ob
            and (confidence >= zone_quality_thresh)
            and has_sweep
            and has_fvg
        ):
            smc_god_mode_active = True
            execution_mode = "SMC_GOD_MODE"
            override_reason = "HTF_BYPASSED"
            logger.info("SMC GOD MODE ACTIVATED: HTF trend filters bypassed!")

        active_threshold = self.confidence_threshold
        if survival_mode:
            active_threshold += 0.10
        if is_range_market:
            active_threshold += self.range_confidence_penalty

        # BUG-249 (Agent-5 decision forensics, 2026-09-05): `max_spread_atr_ratio`
        # was plumbed into the constructor but NEVER enforced - a dead guard.
        # Wire it fail-closed: when spread/ATR exceeds the ratio the broker
        # take already dominates the M1 scalp window, so candidates must not
        # enter (risk_info is still stamped for counterfactual stratification).
        spread_atr_ratio = (current_spread / atr) if atr > 0 else 0.0
        spread_atr_exceeded = current_spread > 0.0 and spread_atr_ratio > self.max_spread_atr_ratio

        # Multi-timeframe trend & S/R variables
        h4_trend = self._sanitize_float(getattr(feature_vector, "htf_h4_trend", 0.0), 0.0)
        h1_mom = self._sanitize_float(getattr(feature_vector, "htf_h1_momentum", 0.0), 0.0)
        m30_str = self._sanitize_float(getattr(feature_vector, "htf_m30_structure", 0.0), 0.0)
        m15_conf = self._sanitize_float(getattr(feature_vector, "htf_m15_confirmation", 0.0), 0.0)
        trend_strength = self._sanitize_float(getattr(feature_vector, "trend_strength", 0.0), 0.0)

        support_zone_dist = self._sanitize_float(
            getattr(feature_vector, "support_zone_dist", 3.0), 3.0
        )
        resistance_zone_dist = self._sanitize_float(
            getattr(feature_vector, "resistance_zone_dist", 3.0), 3.0
        )

        # Strict Multi-Timeframe and S/R Selective Alignments
        htf_buy_aligned = (h4_trend >= 0 or m30_str >= 0 or m15_conf >= 0 or h1_mom >= -0.1) and (
            trend_strength >= -0.2
        )
        htf_sell_aligned = (h4_trend <= 0 or m30_str <= 0 or m15_conf <= 0 or h1_mom <= 0.1) and (
            trend_strength <= 0.2
        )

        # Determine if Higher Timeframe Bearish Momentum is strong
        is_strong_bearish_momentum = h1_mom <= -2.0 and h4_trend == -1.0

        required_support_margin = 0.25
        if is_strong_bearish_momentum:
            required_support_margin = 0.10  # Reduced by 60% (0.25 * 0.40)

        sr_buy_allowed = resistance_zone_dist >= 0.25
        # If HTF bearish momentum is strong, reduce required margin or allow breakout / pullback sells
        sr_sell_allowed = (
            support_zone_dist >= required_support_margin
        ) or is_strong_bearish_momentum

        tick_sweep_proposal = self._evaluate_tick_sweep(
            sweep_sig,
            current_tick,
            ofi,
            tick_velocity,
            raw_prob_buy,
            raw_prob_sell,
            is_range_market,
            execution_id,
            now,
            atr,
            regime_str,
            regime_conf,
            trend_strength,
        )

        # Update last tick trackers
        self._last_tick_bid = current_tick.bid
        self._last_tick_ask = current_tick.ask
        self._last_tick_time = now

        if tick_sweep_proposal is not None:
            self._last_signal_time = now
            return tick_sweep_proposal

        predictive_proposal = self._evaluate_predictive_limit(
            valid_ob,
            smc_god_mode_active,
            total_exposure,
            order_block_type,
            current_tick,
            atr,
            completed_bars,
            execution_id,
            now,
            confidence,
            confidence_before_filters,
            regime_str,
            regime_conf,
            trend_strength,
        )
        if predictive_proposal is not None:
            self._last_signal_time = now
            return predictive_proposal

        # ----------------------------------------------------------------------
        # STANDARD DECISION FLOW (with SMC_GOD_MODE check)
        # ----------------------------------------------------------------------
        def build_nt(reason_msg, blocked_by_filter=None):
            nonlocal confidence
            active_conf = (
                confidence
                if (confidence > 0 or proposed_action != ActionType.NO_TRADE)
                else cand_confidence
            )

            act_rr = getattr(self.algo_config, "min_risk_reward_ratio", 1.8)
            if active_conf >= getattr(self.algo_config, "high_confidence_threshold", 0.70):
                act_rr = getattr(self.algo_config, "min_rr_high_confidence", 1.2)

            base_thr = self.confidence_threshold
            surv_adj = 0.10 if survival_mode else 0.0
            range_pen = self.range_confidence_penalty if is_range_market else 0.0
            eff_thr = active_threshold

            risk_checks_dict = {
                "zone_quality": float(active_conf),
                "min_zone_quality": float(self.algo_config.ai_zone_confidence_threshold),
                "rr": float(cand_actual_rr if cand_actual_rr is not None else 1.0),
                "min_rr": float(act_rr),
                "model_confidence": float(confidence),
                "confidence_source": confidence_source,
                "base_threshold": float(base_thr),
                "range_penalty": float(range_pen),
                "survival_mode_adjustment": float(surv_adj),
                "effective_threshold": float(eff_thr),
                # CHG-0043 decision-evidence: the quoting state this decision
                # saw (ask-bid, USD). Observability only — never a decision
                # input (INV-018); the audit row persists it for the
                # counterfactual engine's spread stratification.
                "spread_usd": float(current_spread),
                # BUG-249 gate evidence (INV-018): ATR-normalized spread.
                "spread_atr_ratio": float(round(spread_atr_ratio, 4)),
                "max_spread_atr_ratio": float(self.max_spread_atr_ratio),
            }
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.NO_TRADE,
                confidence=float(active_conf),
                proposed_entry=float(
                    target_entry_price if (is_buy_cand or is_sell_cand) else current_tick.bid
                ),
                stop_loss=float(
                    cand_stop_loss if (is_buy_cand or is_sell_cand) else current_tick.bid * 0.99
                ),
                take_profit=float(
                    cand_take_profit if (is_buy_cand or is_sell_cand) else current_tick.bid * 1.01
                ),
                risk_reward_ratio=float(cand_actual_rr if (is_buy_cand or is_sell_cand) else 1.0),
                reason_code=reason_msg,
                model_action=cand_action,
                buy_probability=prob_buy,
                sell_probability=prob_sell,
                no_trade_probability=prob_no_trade,
                regime=regime_str,
                regime_confidence=regime_conf,
                risk_allowed=False,
                guardian_status=guardian_status,
                rejection_reason=reason_msg,
                final_action="NO_TRADE",
                risk_checks=risk_checks_dict,
                execution_mode=execution_mode,
                override_reason=override_reason,
                decision_stage=decision_stage,
                blocked_by=blocked_by_filter,
                htf_score=float(trend_strength),
                smc_score=float(active_conf),
                confidence_before_filters=float(confidence_before_filters),
                confidence_after_filters=float(active_conf),
            )

        # --- RULE MATRIX INTEGRATION ---
        if self.rule_matrix:
            self.rule_matrix.refresh_cache()
            # 1. Check Filters first
            blocked_reason = self.rule_matrix.evaluate_pre_trade_filters(
                tick=current_tick, fv=feature_vector, regime_state=regime_state
            )
            if blocked_reason:
                return build_nt(blocked_reason, blocked_by_filter=blocked_reason)

            # 2. Check Custom Rules Triggered Entries
            rule_proposal = self.rule_matrix.evaluate_pre_trade_entry(
                tick=current_tick,
                fv=feature_vector,
                regime_state=regime_state,
                probs=[probs[0], raw_prob_buy, raw_prob_sell]
                if len(probs) > 2
                else [1.0, 0.0, 0.0],
            )
            if rule_proposal:
                logger.info(
                    "Signal triggered by Rule Matrix Entry Engine",
                    rule=rule_proposal.reason_code,
                    action=rule_proposal.action.value,
                )
                return rule_proposal.model_copy(
                    update={
                        "model_action": rule_proposal.action.value,
                        "buy_probability": prob_buy,
                        "sell_probability": prob_sell,
                        "no_trade_probability": prob_no_trade,
                        "regime": regime_str,
                        "regime_confidence": regime_conf,
                        "risk_allowed": True,
                        "guardian_status": guardian_status,
                        "rejection_reason": None,
                        "final_action": rule_proposal.action.value,
                    }
                )

        # ----------------------------------------------------------------------
        # Decision Engine (Fast Liquidity Reversal & Smart Order Routing)
        # ----------------------------------------------------------------------
        proposed_action = ActionType.NO_TRADE
        # BUG-229 (TASK-TDF Q3): the DEFAULT label below is the "no rule
        # fired / no candidate" outcome, NOT a regime event. The old
        # REGIME_*/RANGE_BOUND_SIDEWAYS/NEUTRAL_MARKET strings made
        # reason-code analytics blame the market regime for every idle
        # tick. The regime value stays embedded as a suffix (recoverable
        # context); explicit rule-path codes are byte-identical and
        # overwrite this default below. The code deliberately avoids the
        # "REGIME_" substring so the audit_repository regime-fallback
        # partition never matches it.
        reason_code = (
            f"NO_CANDIDATE_{regime_type.value}"
            if regime_type
            else ("NO_CANDIDATE_RANGE_MARKET" if is_range_market else "NO_CANDIDATE_NEUTRAL_MARKET")
        )
        target_entry_price = current_tick.ask

        high_velocity_momentum = tick_velocity >= 10.0

        # --- FAST LIQUIDITY SWEEP REVERSALS ---
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
        elif (ichimoku_bullish or stat_arb_bullish) and (
            moving_up or ict_bullish or relative_buy_bias > 0.50 or stat_arb_bullish
        ):
            if not htf_buy_aligned and not smc_god_mode_active:
                decision_stage = "HTF_TREND_FILTER"
                return build_nt(
                    "BUY_REJECTED_HTF_TREND_CONFL_FAIL", blocked_by_filter="HTF_TREND_CONFL_FAIL"
                )
            elif not sr_buy_allowed and not smc_god_mode_active:
                decision_stage = "SR_MARGIN_FILTER"
                return build_nt(
                    "BUY_REJECTED_SR_RESISTANCE_MARGIN_FAIL",
                    blocked_by_filter="SR_RESISTANCE_MARGIN_FAIL",
                )
            elif stat_arb_bullish and is_range_market:
                proposed_action = ActionType.BUY_LIMIT
                target_entry_price = min(tenkan, round(current_tick.ask - 0.10, 2))
                reason_code = f"STAT_ARB_MEAN_REVERSION_BUY_LIMIT (Z: {z_score:+.2f})"
            elif feature_vector.fvg_bullish_active or (
                exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum
            ):
                proposed_action = ActionType.BUY_LIMIT
                target_entry_price = min(tenkan, round(current_tick.ask - 0.10, 2))
                reason_code = "ICT_FVG_PULLBACK_BUY_LIMIT"
            elif feature_vector.broke_previous_high or high_velocity_momentum:
                proposed_action = (
                    ActionType.BUY_MARKET if high_velocity_momentum else ActionType.BUY_STOP
                )
                target_entry_price = (
                    current_tick.ask
                    if high_velocity_momentum
                    else round(current_tick.ask + 0.12, 2)
                )
                reason_code = (
                    "HFT_VELOCITY_BUY_MARKET"
                    if high_velocity_momentum
                    else "BREAKOUT_MOMENTUM_BUY_STOP"
                )
            elif not is_range_market or abs(ofi) >= 0.15:
                proposed_action = ActionType.BUY_MARKET
                target_entry_price = current_tick.ask
                reason_code = f"AGGRESSIVE_SCALP_BUY (OFI: {ofi:+.2f})"
            else:
                return build_nt(
                    "RANGE_FILTERED_IMPULSIVE_BUY_PREVENTED", blocked_by_filter="RANGE_FILTER"
                )

        # --- STANDARD SELL SIGNALS ---
        elif (ichimoku_bearish or stat_arb_bearish) and (
            moving_down or ict_bearish or relative_sell_bias > 0.50 or stat_arb_bearish
        ):
            if not htf_sell_aligned and not smc_god_mode_active:
                decision_stage = "HTF_TREND_FILTER"
                return build_nt(
                    "SELL_REJECTED_HTF_TREND_CONFL_FAIL", blocked_by_filter="HTF_TREND_CONFL_FAIL"
                )
            elif not sr_sell_allowed and not smc_god_mode_active:
                decision_stage = "SR_MARGIN_FILTER"
                return build_nt(
                    "SELL_REJECTED_SR_SUPPORT_MARGIN_FAIL",
                    blocked_by_filter="SR_SUPPORT_MARGIN_FAIL",
                )
            elif stat_arb_bearish and is_range_market:
                proposed_action = ActionType.SELL_LIMIT
                target_entry_price = max(tenkan, round(current_tick.bid + 0.10, 2))
                reason_code = f"STAT_ARB_MEAN_REVERSION_SELL_LIMIT (Z: {z_score:+.2f})"
            elif feature_vector.fvg_bearish_active or (
                exec_type == RecommendedExecutionType.PASSIVE_LIMIT and not high_velocity_momentum
            ):
                proposed_action = ActionType.SELL_LIMIT
                target_entry_price = max(tenkan, round(current_tick.bid + 0.10, 2))
                reason_code = "ICT_FVG_PULLBACK_SELL_LIMIT"
            elif feature_vector.broke_previous_low or high_velocity_momentum:
                proposed_action = (
                    ActionType.SELL_MARKET if high_velocity_momentum else ActionType.SELL_STOP
                )
                target_entry_price = (
                    current_tick.bid
                    if high_velocity_momentum
                    else round(current_tick.bid - 0.12, 2)
                )
                reason_code = (
                    "HFT_VELOCITY_SELL_MARKET"
                    if high_velocity_momentum
                    else "BREAKOUT_MOMENTUM_SELL_STOP"
                )
            elif not is_range_market or abs(ofi) >= 0.15:
                proposed_action = ActionType.SELL_MARKET
                target_entry_price = current_tick.bid
                reason_code = f"AGGRESSIVE_SCALP_SELL (OFI: {ofi:+.2f})"
            else:
                return build_nt(
                    "RANGE_FILTERED_IMPULSIVE_SELL_PREVENTED", blocked_by_filter="RANGE_FILTER"
                )

        # Apply SMC God Mode Penalty
        if smc_god_mode_active:
            confidence *= 0.85
            logger.info(
                f"SMC GOD MODE: Applying 15% confidence penalty. Adjusted confidence={confidence:.2f}"
            )

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 6: 50% Impulse Equilibrium hard gating for Short trades
        # ----------------------------------------------------------------------
        if proposed_action in (ActionType.SELL_MARKET, ActionType.SELL_LIMIT, ActionType.SELL_STOP):
            if order_block_type == -1 and feature_vector.feat_ob_equilibrium_ratio < 0.50:
                decision_stage = "OB_EQUILIBRIUM_FILTER"
                return build_nt(
                    "OB_BELOW_50_PERCENT_EQUILIBRIUM",
                    blocked_by_filter="OB_BELOW_50_PERCENT_EQUILIBRIUM",
                )

        # Query live active positions and pending orders
        has_any_live_order = False
        live_tickets = []
        if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
            live_tickets = order_manager.get_active_live_tickets()
            for ticket_info in live_tickets:
                t_symbol = ticket_info.get("symbol")
                t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                if t_symbol == "XAUUSD" and t_magic == 888101:
                    has_any_live_order = True
                    break

        # If no live order exists on MT5 terminal chart, instantly release the price lock
        if not has_any_live_order:
            self.last_order_price = None
            self.last_order_time = None
            self._last_active_direction = None
            self._last_active_direction_time = None
            self._last_executed_price = 0.0

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 5: Same-Level Duplicate Re-Entry Lockout
        # ----------------------------------------------------------------------
        if proposed_action != ActionType.NO_TRADE and not is_fast_reversal:
            reentry_blocked = False
            reentry_blocked_reason = ""

            if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
                for ticket_info in live_tickets:
                    t_symbol = ticket_info.get("symbol")
                    t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                    t_price = ticket_info.get("price")

                    if t_symbol == "XAUUSD" and t_magic == 888101 and t_price is not None:
                        price_dist = abs(target_entry_price - t_price)
                        threshold = 0.50  # minimum distance threshold is $0.50
                        if price_dist < threshold:
                            reentry_blocked = True
                            reentry_blocked_reason = (
                                f"SAME_LEVEL_REENTRY_BLOCKED (${price_dist:.2f} < ${threshold:.2f})"
                            )
                            break
            # Standalone unit test fallback when order_manager is not provided
            elif self._last_active_direction == proposed_action and self._last_executed_price > 0.0:
                price_dist_from_last = abs(target_entry_price - self._last_executed_price)
                if price_dist_from_last < (atr * 0.50):
                    reentry_blocked = True
                    reentry_blocked_reason = f"SAME_LEVEL_REENTRY_BLOCKED (${price_dist_from_last:.2f} < ${atr * 0.50:.2f})"

            if reentry_blocked:
                decision_stage = "REENTRY_GATE"
                return build_nt(reentry_blocked_reason, blocked_by_filter="SAME_LEVEL_REENTRY")

        if confidence < active_threshold and proposed_action != ActionType.NO_TRADE:
            decision_stage = "CONFIDENCE_GATE"
            base_thr = self.confidence_threshold
            surv_adj = 0.10 if survival_mode else 0.0
            range_pen = self.range_confidence_penalty if is_range_market else 0.0
            eff_thr = active_threshold
            reason_msg = (
                f"INSUFFICIENT_CONFIDENCE: Model Confidence ({confidence:.2f}) < "
                f"Effective Threshold ({eff_thr:.2f}) [Base: {base_thr:.2f}, "
                f"Range Penalty: +{range_pen:.2f}, Survival Mode: +{surv_adj:.2f}]"
            )
            return build_nt(
                reason_msg,
                blocked_by_filter="CONFIDENCE_FAIL",
            )

        # Neural Zone Validation
        is_zone_active = (
            fvg_bullish_active or fvg_bearish_active or order_block_type != 0 or sweep_sig != 0
        )
        if is_zone_active and proposed_action != ActionType.NO_TRADE:
            zone_quality_score = confidence
            if zone_quality_score < self.algo_config.ai_zone_confidence_threshold:
                decision_stage = "ZONE_QUALITY_GATE"
                return build_nt(
                    f"ZONE_QUALITY_BELOW_THRESHOLD ({zone_quality_score:.2f} < {self.algo_config.ai_zone_confidence_threshold:.2f})",
                    blocked_by_filter="ZONE_QUALITY_FAIL",
                )

        # ----------------------------------------------------------------------
        # PROPOSAL GATE 4: Anti-Flip Protection (Bypassed for Fast Sweeps)
        # ----------------------------------------------------------------------
        if (
            proposed_action != ActionType.NO_TRADE
            and self._last_active_direction is not None
            and not is_fast_reversal
        ):
            elapsed_flip_sec = (
                (now - self._last_active_direction_time).total_seconds()
                if self._last_active_direction_time
                else 999.0
            )
            elapsed_flip_sec = max(0.0, elapsed_flip_sec)

            if elapsed_flip_sec <= self.flip_memory_seconds:
                is_reversing = (
                    "BUY" in self._last_active_direction.value and "SELL" in proposed_action.value
                ) or (
                    "SELL" in self._last_active_direction.value and "BUY" in proposed_action.value
                )
                required_flip_confidence = active_threshold + self.flip_confidence_penalty
                if is_reversing and confidence < required_flip_confidence:
                    decision_stage = "FLIP_PROTECTION"
                    return build_nt(
                        f"FLIP_PROTECTION_BLOCKED ({confidence:.2f} < req {required_flip_confidence:.2f})",
                        blocked_by_filter="FLIP_PROTECTION",
                    )

        if proposed_action == ActionType.NO_TRADE:
            final_proposal = build_nt(reason_code)
        elif (
            self._last_signal_time is not None
            and max(0.0, (now - self._last_signal_time).total_seconds()) < self.cooldown_seconds
        ):
            elapsed_cooldown = max(0.0, (now - self._last_signal_time).total_seconds())
            final_proposal = build_nt(
                f"COOLDOWN_ACTIVE ({elapsed_cooldown:.1f}s)", blocked_by_filter="COOLDOWN"
            )
        else:
            # For passing signals, use the pre-computed candidate levels
            stop_loss = cand_stop_loss
            take_profit = cand_take_profit

            # Ensure stop_loss satisfies directional price invariants relative to updated target_entry_price
            if "BUY" in proposed_action.value and (
                stop_loss is None or stop_loss >= target_entry_price
            ):
                stop_loss = round(
                    target_entry_price - (atr * self.algo_config.atr_sl_buffer_multiplier), 2
                )
            elif "SELL" in proposed_action.value and (
                stop_loss is None or stop_loss <= target_entry_price
            ):
                stop_loss = round(
                    target_entry_price + (atr * self.algo_config.atr_sl_buffer_multiplier), 2
                )

            actual_rr = cand_actual_rr

            # Determine active min required RR based on confidence (normal vs high confidence)
            active_min_rr = getattr(self.algo_config, "min_risk_reward_ratio", 1.8)
            if confidence >= getattr(self.algo_config, "high_confidence_threshold", 0.95):
                min_rr_hc = getattr(self.algo_config, "min_rr_high_confidence", 1.2)
                active_min_rr = min(active_min_rr, min_rr_hc)

            # Ensure take_profit satisfies at least the minimum allowed RR to guarantee reward > risk
            risk_amount = max(abs(target_entry_price - stop_loss), 1e-5)
            active_tp_rr = min(self.min_allowed_rr, active_min_rr)
            min_required_tp_dist = risk_amount * active_tp_rr
            if "BUY" in proposed_action.value:
                if take_profit < target_entry_price + min_required_tp_dist:
                    take_profit = round(target_entry_price + min_required_tp_dist, 2)
            elif take_profit > target_entry_price - min_required_tp_dist:
                take_profit = round(target_entry_price - min_required_tp_dist, 2)

            reward_amount = abs(take_profit - target_entry_price)
            actual_rr = round(reward_amount / risk_amount, 2)

            # Asymmetric Risk Gatekeeper
            if actual_rr < active_min_rr:
                final_proposal = build_nt(
                    "ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD",
                    blocked_by_filter="ASYMMETRIC_RR_LIMIT",
                )
            elif spread_atr_exceeded and proposed_action != ActionType.NO_TRADE:
                # BUG-249: ATR-normalized spread gate (fail-closed).
                decision_stage = "SPREAD_ATR_GATE"
                final_proposal = build_nt(
                    f"SPREAD_ATR_RATIO_EXCEEDED ({spread_atr_ratio:.2f} > {self.max_spread_atr_ratio:.2f})",
                    blocked_by_filter="SPREAD_ATR_RATIO",
                )
            else:
                reason_code = (
                    f"{reason_code} | HTF:[H4={h4_trend:+.1f}, H1_Mom={h1_mom:+.1f}, "
                    f"M30_Str={m30_str:+.1f}, M15_Conf={m15_conf:+.1f}] | "
                    f"S_Dist={support_zone_dist:.2f}, R_Dist={resistance_zone_dist:.2f}"
                )
                self._last_active_direction = proposed_action
                self._last_active_direction_time = now
                self._last_executed_price = target_entry_price
                self.last_order_price = target_entry_price
                self.last_order_time = now
                self._last_signal_time = now

                risk_checks_dict = {
                    "zone_quality": float(confidence),
                    "min_zone_quality": float(self.algo_config.ai_zone_confidence_threshold),
                    "rr": float(actual_rr),
                    "min_rr": float(active_min_rr),
                    "confidence_source": confidence_source,
                    # CHG-0043 decision-evidence spread stamp (see build_nt).
                    "spread_usd": float(current_spread),
                    # BUG-249 gate evidence (INV-018): ATR-normalized spread.
                    "spread_atr_ratio": float(round(spread_atr_ratio, 4)),
                    "max_spread_atr_ratio": float(self.max_spread_atr_ratio),
                }

                final_proposal = TradeProposal(
                    request_id=str(uuid.uuid4()),
                    execution_id=execution_id,
                    symbol=current_tick.symbol,
                    generated_at=now,
                    action=proposed_action,
                    confidence=float(confidence),
                    proposed_entry=float(target_entry_price),
                    stop_loss=float(stop_loss),
                    take_profit=float(take_profit),
                    risk_reward_ratio=float(actual_rr),
                    reason_code=reason_code,
                    model_action=cand_action,
                    buy_probability=prob_buy,
                    sell_probability=prob_sell,
                    no_trade_probability=prob_no_trade,
                    regime=regime_str,
                    regime_confidence=regime_conf,
                    risk_allowed=True,
                    guardian_status=guardian_status,
                    rejection_reason=None,
                    final_action=proposed_action.value,
                    risk_checks=risk_checks_dict,
                    execution_mode=execution_mode,
                    override_reason=override_reason,
                    decision_stage="FINAL_DECISION",
                    blocked_by=blocked_by,
                    htf_score=float(trend_strength),
                    smc_score=float(confidence),
                    confidence_before_filters=float(confidence_before_filters),
                    confidence_after_filters=float(confidence),
                )

        # PHASE 13 forensic trace: one log line per evaluation carrying the
        # EXEC id + full decision chain (action, stage, blocked_by, confidences,
        # regime) so a single id explains WHY this evaluation did/didn't trade.
        # Guarded by the same throttle as radar telemetry (never a hot-path
        # flood). Observability only.
        if execution_id and final_proposal is not None:
            logger.info(
                "[EXEC_TRACE]",
                execution_id=execution_id,
                request_id=final_proposal.request_id,
                action=(
                    final_proposal.action.value
                    if hasattr(final_proposal.action, "value")
                    else str(final_proposal.action)
                ),
                stage=final_proposal.decision_stage,
                blocked_by=final_proposal.blocked_by,
                reason=final_proposal.reason_code,
                conf_before=float(final_proposal.confidence_before_filters or 0.0),
                conf_after=float(final_proposal.confidence_after_filters or 0.0),
                regime=str(final_proposal.regime or ""),
            )

        # Throttled Console Telemetry logging actual finalized decision action
        should_log = False
        if force_log:
            should_log = True
        elif self._last_telemetry_time is None:
            should_log = True
        else:
            elapsed_telemetry = max(0.0, (now - self._last_telemetry_time).total_seconds())
            if (
                elapsed_telemetry >= self.telemetry_interval
                or final_proposal.action != self._last_logged_action
            ):
                should_log = True

        if should_log:
            logger.info(
                "[MARKET RADAR]",
                action=final_proposal.action.value,
                regime=regime_type.value
                if regime_type
                else ("RANGE/CHOP" if is_range_market else "TRENDING"),
                z_score=f"{z_score:+.2f}",
                ofi=f"{ofi:+.2f}",
                spread=f"${current_spread:.2f}",
                gold_move=f"${disp:+.2f}",
                ai_buy=f"{prob_buy * 100:.1f}%",
                ai_sell=f"{prob_sell * 100:.1f}%",
                ichi=f"Kumo:{'ABOVE' if feature_vector.is_above_kumo else ('BELOW' if feature_vector.is_below_kumo else 'INSIDE')}",
                reason=final_proposal.reason_code,
            )
            self._last_telemetry_time = now
            self._last_logged_action = final_proposal.action

        # BUG-169: remember the latest REAL (non-duplicate) evaluation so a
        # duplicate tick can re-surface it instead of a fabricated NO_TRADE.
        if final_proposal is not None and getattr(final_proposal, "decision_stage", "") != (
            "DEDUP_GATE"
        ):
            self._last_real_proposal = final_proposal

        return final_proposal

    def _evaluate_tick_sweep(
        self,
        sweep_sig: int,
        current_tick: TickData,
        ofi: float,
        tick_velocity: float,
        raw_prob_buy: float,
        raw_prob_sell: float,
        is_range_market: bool,
        execution_id: str,
        now: datetime,
        atr: float,
        regime_str: str,
        regime_conf: float,
        trend_strength: float,
    ) -> TradeProposal | None:
        if self._last_tick_bid <= 0.0:
            return None

        price_pierced_liq = False
        reversal_detected = False
        direction = None

        if sweep_sig == 1 and current_tick.bid < self._last_tick_bid:
            price_pierced_liq = True
            reversal_detected = True
            direction = "BUY"
        elif sweep_sig == -1 and current_tick.ask > self._last_tick_ask:
            price_pierced_liq = True
            reversal_detected = True
            direction = "SELL"

        ofi_flip = ofi > 0 if direction == "BUY" else ofi < 0
        velocity_reverses = tick_velocity > 5.0

        sweep_direction_prob = (
            raw_prob_buy if direction == "BUY" else raw_prob_sell if direction == "SELL" else 0.0
        )

        sweep_conf_thresh = self.confidence_threshold + (
            self.range_confidence_penalty if is_range_market else 0.0
        )

        sweep_has_confidence = sweep_direction_prob >= sweep_conf_thresh

        if (
            price_pierced_liq
            and reversal_detected
            and ofi_flip
            and velocity_reverses
            and sweep_has_confidence
        ):
            execution_mode = "TICK_SWEEP"
            proposed_action = (
                ActionType.BUY_MARKET if direction == "BUY" else ActionType.SELL_MARKET
            )
            target_entry_price = current_tick.ask if direction == "BUY" else current_tick.bid
            reason_code = f"TICK_LEVEL_LIQUIDITY_SWEEP_{direction}"
            sweep_conf = float(sweep_direction_prob)

            logger.info(
                f"TICK LEVEL SWEEP DETECTED: Emitting instant {proposed_action.value}! (prob={sweep_conf:.2f})"
            )

            stop_loss = (
                target_entry_price - atr * 1.5
                if direction == "BUY"
                else target_entry_price + atr * 1.5
            )
            take_profit = (
                target_entry_price + atr * 3.0
                if direction == "BUY"
                else target_entry_price - atr * 3.0
            )

            actual_rr = round(
                abs(take_profit - target_entry_price)
                / max(abs(target_entry_price - stop_loss), 1e-5),
                2,
            )

            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=now,
                action=proposed_action,
                confidence=sweep_conf,
                proposed_entry=float(target_entry_price),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_reward_ratio=float(actual_rr),
                reason_code=reason_code,
                regime=regime_str,
                regime_confidence=regime_conf,
                execution_mode=execution_mode,
                override_reason="TICK_VELOCITY_TRIGGERED",
                decision_stage="TICK_SWEEP_EXECUTION",
                # EXPLAINABILITY CONTRACT (2026-09-03): the tick-sweep path
                # applies its OWN confidence floor (sweep_conf_thresh, stricter
                # in range) instead of the standard-flow gate. Persist the
                # evidence - observability only (INV-018).
                risk_checks={
                    "decision_path": "TICK_SWEEP",
                    "confidence_gate_applied": True,
                    "sweep_conf_threshold": float(sweep_conf_thresh),
                    "sweep_direction_prob": float(sweep_conf),
                    "model_confidence_verdict": "SWEEP_PATH_THRESHOLD",
                    "structural_gate": {
                        "price_pierced_liquidity": True,
                        "reversal_detected": True,
                        "ofi_flip": True,
                        "velocity_reverses": True,
                        "direction": direction,
                    },
                },
                htf_score=float(trend_strength),
                smc_score=sweep_conf,
                confidence_before_filters=sweep_conf,
                confidence_after_filters=sweep_conf,
            )
        return None

    def _evaluate_predictive_limit(
        self,
        valid_ob: bool,
        smc_god_mode_active: bool,
        total_exposure: int,
        order_block_type: int,
        current_tick: TickData,
        atr: float,
        completed_bars: list[Any] | None,
        execution_id: str,
        now: datetime,
        confidence: float,
        confidence_before_filters: float,
        regime_str: str,
        regime_conf: float,
        trend_strength: float,
    ) -> TradeProposal | None:
        if valid_ob and not smc_god_mode_active and total_exposure < MAX_TOTAL_EXPOSURE:
            execution_mode = "PREDICTIVE_LIMIT"
            proposed_action = (
                ActionType.BUY_LIMIT if order_block_type == 1 else ActionType.SELL_LIMIT
            )

            swing_low_20 = current_tick.bid - atr
            swing_high_20 = current_tick.ask + atr
            if completed_bars is not None and len(completed_bars) >= 20:
                swing_low_20 = np.min([b.low for b in completed_bars[-20:]])
                swing_high_20 = np.max([b.high for b in completed_bars[-20:]])
            target_entry_price = round(swing_low_20 + 0.50 * (swing_high_20 - swing_low_20), 2)

            if proposed_action == ActionType.BUY_LIMIT and target_entry_price >= current_tick.ask:
                target_entry_price = round(current_tick.ask - 0.12, 2)
            elif (
                proposed_action == ActionType.SELL_LIMIT and target_entry_price <= current_tick.bid
            ):
                target_entry_price = round(current_tick.bid + 0.12, 2)

            deepest_wick = (
                swing_low_20 if proposed_action == ActionType.BUY_LIMIT else swing_high_20
            )
            stop_loss = (
                round(deepest_wick - atr * self.algo_config.atr_sl_buffer_multiplier, 2)
                if proposed_action == ActionType.BUY_LIMIT
                else round(deepest_wick + atr * self.algo_config.atr_sl_buffer_multiplier, 2)
            )

            take_profit = (
                round(swing_high_20, 2)
                if proposed_action == ActionType.BUY_LIMIT
                else round(swing_low_20, 2)
            )

            risk_amount = max(abs(target_entry_price - stop_loss), 1e-5)
            active_min_rr = getattr(self.algo_config, "min_risk_reward_ratio", 1.8)
            reward_amount = abs(take_profit - target_entry_price)
            actual_rr = round(reward_amount / risk_amount, 2)

            if actual_rr < active_min_rr:
                min_tp_dist = risk_amount * active_min_rr
                take_profit = (
                    round(target_entry_price + min_tp_dist, 2)
                    if proposed_action == ActionType.BUY_LIMIT
                    else round(target_entry_price - min_tp_dist, 2)
                )
                reward_amount = abs(take_profit - target_entry_price)
                actual_rr = round(reward_amount / risk_amount, 2)

            reason_code = f"PREDICTIVE_OB_{proposed_action.name}_EQUILIBRIUM"
            logger.info(
                f"PREDICTIVE LIMIT EXECUTED: Placing {proposed_action.value} at 50% Equilibrium {target_entry_price}!"
            )

            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=now,
                action=proposed_action,
                confidence=float(confidence),
                proposed_entry=float(target_entry_price),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_reward_ratio=float(actual_rr),
                reason_code=reason_code,
                regime=regime_str,
                regime_confidence=regime_conf,
                execution_mode=execution_mode,
                override_reason="PREDICTIVE_OB_PLACEMENT",
                decision_stage="PREDICTIVE_LIMIT_GENERATION",
                htf_score=float(trend_strength),
                smc_score=float(confidence),
                confidence_before_filters=float(confidence_before_filters),
                confidence_after_filters=float(confidence),
                # EXPLAINABILITY CONTRACT (2026-09-03, counterfactual
                # forensic): the predictive-limit path replaces the model
                # confidence gate with a STRUCTURAL gate. The audit row must
                # prove which gate ran and what it verified - observability
                # only, never a decision input (INV-018).
                risk_checks={
                    "decision_path": "PREDICTIVE_LIMIT",
                    "confidence_gate_applied": False,
                    "model_confidence": float(confidence),
                    "model_confidence_verdict": "NOT_REQUIRED_STRUCTURAL_PATH",
                    "structural_gate": {
                        "valid_ob": True,
                        "order_block_type": int(order_block_type),
                        "smc_god_mode": bool(smc_god_mode_active),
                        "total_exposure": int(total_exposure),
                        "min_rr_enforced": float(actual_rr),
                        "equilibrium_entry": float(target_entry_price),
                    },
                },
            )
        return None

    def _evaluate_exposure_limits(
        self,
        total_exposure: int,
        active_positions_count: int,
        active_pending_count: int,
        order_manager: Any,
        live_tickets: list[Any],
        target_entry_price: float,
        current_tick: TickData,
        regime_str: str,
        regime_conf: float,
        atr: float,
        completed_bars: list[Any] | None,
        now: datetime,
    ) -> TradeProposal | None:
        # 2. Strict Single-Position Exposure Gate (MAX_TOTAL_EXPOSURE = 1)
        # Total of Active Open Positions + Active Pending Orders MUST NOT exceed 1.
        if total_exposure >= MAX_TOTAL_EXPOSURE:
            # Check price proximity to find if we should return SAME_LEVEL_REENTRY_BLOCKED (threshold is $0.50)
            is_same_level = False
            if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
                for ticket_info in live_tickets:
                    t_symbol = ticket_info.get("symbol")
                    t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                    t_price = ticket_info.get("price")
                    if t_symbol == "XAUUSD" and t_magic == 888101 and t_price is not None:
                        if abs(target_entry_price - t_price) < 0.50:
                            is_same_level = True
                            break

            if is_same_level:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="SAME_LEVEL_REENTRY_BLOCKED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                )

            # If we hold 1 active open position, block entries.
            # blocked_by=EXECUTION_STATE_BLOCK: an execution-state block, NOT
            # a model rejection — the learning engine must not learn "the
            # model chose not to trade" from an unavailable execution slot.
            if active_positions_count >= 1:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="MAX_EXPOSURE_REACHED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                    blocked_by="EXECUTION_STATE_BLOCK",
                    decision_stage="EXPOSURE_GATE",
                )

            # If we hold 1 active pending order, check lock & price drift hysteresis
            if active_pending_count >= 1:
                # Calculate 50% Equilibrium price
                swing_low_20 = current_tick.bid - atr
                swing_high_20 = current_tick.ask + atr
                if completed_bars is not None and len(completed_bars) >= 20:
                    swing_low_20 = np.min([b.low for b in completed_bars[-20:]])
                    swing_high_20 = np.max([b.high for b in completed_bars[-20:]])
                new_eq_price = round(swing_low_20 + 0.50 * (swing_high_20 - swing_low_20), 2)

                time_delta = (
                    (now - self._locked_pending_time).total_seconds()
                    if self._locked_pending_time is not None
                    else 0.0
                )
                price_drift = (
                    abs(new_eq_price - self._locked_pending_price)
                    if self._locked_pending_price is not None
                    else 0.0
                )

                # 30-SECOND PENDING LOCK: never cancel/recreate a live limit order unless
                # it has been resting for more than 30s AND price has drifted >= 1.0 x ATR.
                if time_delta <= PENDING_ORDER_LOCK_SECONDS or price_drift < (1.0 * atr):
                    # Maintain the existing live limit order and return ActionType.NO_TRADE
                    # (execution-state block, not model rejection).
                    return self._build_no_trade(
                        tick=current_tick,
                        confidence=0.0,
                        reason="PENDING_ORDER_LOCKED",
                        regime_str=regime_str,
                        regime_conf=regime_conf,
                        blocked_by="EXECUTION_STATE_BLOCK",
                        decision_stage="EXPOSURE_GATE",
                    )
        return None

    def _evaluate_frequency_throttle(
        self,
        now: datetime,
        current_tick: TickData,
        regime_str: str,
        regime_conf: float,
    ) -> TradeProposal | None:
        # 1. Enforce ORDER_FREQUENCY_THROTTLED check (MIN_ORDER_INTERVAL_SECONDS = 60)
        if self._last_signal_time is not None:
            elapsed = (now - self._last_signal_time).total_seconds()
            if elapsed < 60.0:
                return self._build_no_trade(
                    tick=current_tick,
                    confidence=0.0,
                    reason="ORDER_FREQUENCY_THROTTLED",
                    regime_str=regime_str,
                    regime_conf=regime_conf,
                )
        return None

    def _get_active_tickets_info(
        self, order_manager: Any
    ) -> tuple[int, int, float | None, int | None, list[Any], dict[int, str]]:
        active_positions_count = 0
        active_pending_count = 0
        pending_price = None
        pending_ticket = None
        live_tickets: list[Any] = []
        held_position_dirs: dict[int, str] = {}

        if order_manager is not None and hasattr(order_manager, "get_active_live_tickets"):
            live_tickets = order_manager.get_active_live_tickets()
            for ticket_info in live_tickets:
                t_symbol = ticket_info.get("symbol")
                t_magic = ticket_info.get("magic") or ticket_info.get("magic_number")
                t_type = ticket_info.get("type")
                if t_symbol == "XAUUSD" and t_magic == 888101:
                    if t_type == "POSITION":
                        active_positions_count += 1
                        t_ticket = ticket_info.get("ticket")
                        t_dir = str(ticket_info.get("direction") or "").upper()
                        if t_ticket is not None and t_dir:
                            held_position_dirs[int(t_ticket)] = t_dir
                    elif t_type == "PENDING":
                        active_pending_count += 1
                        pending_price = ticket_info.get("price")
                        pending_ticket = ticket_info.get("ticket")
        return (
            active_positions_count,
            active_pending_count,
            pending_price,
            pending_ticket,
            live_tickets,
            held_position_dirs,
        )

    def _evaluate_duplicate_tick(
        self,
        probs: list[float],
        current_tick: TickData,
        execution_id: str,
        regime_state: MarketRegimeState | None,
    ) -> TradeProposal | None:
        # ---------------------------------------------------------------------
        # TASK 5 FIX: micro-throttler / tick de-duplication.
        # The hot path (50ms) is frequently invoked faster than the market feed
        # produces quotes, so the same tick (identical timestamp, or identical
        # bid AND ask) gets re-evaluated and re-logged, corrupting telemetry.
        # We detect a duplicate and return a lightweight NO_TRADE proposal WITHOUT
        # touching the persistent state (cooldown, last direction, price locks).
        # ---------------------------------------------------------------------
        tick_ts = current_tick.timestamp
        tick_bid = float(getattr(current_tick, "bid", 0.0) or 0.0)
        tick_ask = float(getattr(current_tick, "ask", 0.0) or 0.0)
        is_duplicate = (
            tick_ts is not None
            and self._dedup_last_time is not None
            and tick_ts == self._dedup_last_time
        ) or (
            tick_bid == self._dedup_last_bid and tick_ask == self._dedup_last_ask and tick_bid > 0.0
        )
        if is_duplicate:
            # BUG-169: a duplicate tick carries ZERO new information, so the
            # engine now skips the whole pipeline for it (LiveEngine early
            # return). If the policy is STILL reached with a duplicate (e.g.
            # another caller, or the engine guard is bypassed), surface the
            # LAST REAL decision instead of a synthetic NO_TRADE conf=0.0 —
            # the fabricated proposal was what the UI displayed as the
            # "Active Intelligence Output", hiding the actual fresh decision
            # and freezing the displayed confidence at 0.00%. The duplicate
            # still never touches cooldown/direction/price-lock state.
            last = getattr(self, "_last_real_proposal", None)
            if last is not None:
                return last.model_copy(
                    update={
                        "request_id": str(uuid.uuid4()),
                        "execution_id": execution_id,
                        "generated_at": current_tick.timestamp,
                    }
                )
            _pb = probs[1] if len(probs) > 1 else 0.0
            _ps = probs[2] if len(probs) > 2 else 0.0
            _pnt = probs[0] if len(probs) > 0 else 0.0
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.NO_TRADE,
                confidence=0.0,
                proposed_entry=current_tick.bid,
                stop_loss=current_tick.bid * 0.99,
                take_profit=current_tick.bid * 1.01,
                risk_reward_ratio=1.0,
                reason_code="TICK_DUPLICATE_SUPPRESSED",
                model_action="NO_TRADE",
                buy_probability=float(_pb),
                sell_probability=float(_ps),
                no_trade_probability=float(_pnt),
                regime=str(regime_state.regime_type.value if regime_state else "UNKNOWN"),
                regime_confidence=float(regime_state.regime_probability if regime_state else 0.0),
                risk_allowed=False,
                guardian_status="IDLE",
                rejection_reason="TICK_DUPLICATE_SUPPRESSED",
                final_action="NO_TRADE",
                decision_stage="DEDUP_GATE",
                blocked_by="TICK_DEDUP",
                htf_score=0.0,
                smc_score=0.0,
                confidence_before_filters=0.0,
                confidence_after_filters=0.0,
            )

        # Record the freshest tick signature for the next call.
        self._dedup_last_time = tick_ts
        self._dedup_last_bid = tick_bid
        self._dedup_last_ask = tick_ask
        return None

    def _evaluate_guardian_gate(
        self,
        regime_state: MarketRegimeState | None,
        current_tick: TickData,
        execution_id: str,
    ) -> TradeProposal | None:
        is_guardian_active = False
        if regime_state is not None:
            regime_type = regime_state.regime_type
            exec_type = regime_state.recommended_execution_type

            # Unsafe regimes to block
            UNSAFE_REGIMES = {
                "HIGH_SPREAD_CHOP",
                "UNKNOWN",
                "MARKET_HALTED",
                "LOW_LIQUIDITY",
                "NEWS_LOCK",
                "MACRO_NEWS_FREEZE",
            }
            reg_val = getattr(regime_type, "value", str(regime_type))
            if reg_val in UNSAFE_REGIMES or exec_type == RecommendedExecutionType.FREEZE_ALL:
                is_guardian_active = True

        if is_guardian_active:
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                execution_id=execution_id,
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.NO_TRADE,
                confidence=0.0,
                proposed_entry=current_tick.bid,
                stop_loss=current_tick.bid * 0.99,
                take_profit=current_tick.bid * 1.01,
                risk_reward_ratio=1.0,
                reason_code="BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
                # CHG-0043 decision-evidence: the guardian fires BEFORE
                # inference (pre-model freeze), so no direction or model
                # probabilities exist yet. Recorded honestly as
                # NOT_RECORDED/absent — never fabricated from later state.
                # sentinel SL/TP are documented placeholders, NOT real
                # geometry (the counterfactual engine must treat them as
                # geometry_unavailable_before_gate).
                model_action="NO_TRADE",
                buy_probability=0.0,
                sell_probability=0.0,
                no_trade_probability=1.0,
                regime=str(regime_state.regime_type.value if regime_state else "UNKNOWN"),
                regime_confidence=float(regime_state.regime_probability if regime_state else 0.0),
                risk_allowed=False,
                guardian_status="ACTIVE",
                rejection_reason="BLOCKED_BY_GUARDIAN_UNSAFE_REGIME",
                final_action="NO_TRADE",
                decision_stage="GUARDIAN_GATE",
                blocked_by="REGIME_GUARDIAN",
                htf_score=0.0,
                smc_score=0.0,
                confidence_before_filters=0.0,
                confidence_after_filters=0.0,
                risk_checks={
                    # pre-model block: quoting state IS known even though the
                    # model never ran (spread observable without inference).
                    "spread_usd": float(round(max(0.0, current_tick.ask - current_tick.bid), 2)),
                    "geometry_unavailable_before_gate": True,
                    "confidence_source": "PRE_MODEL_GUARDIAN",
                },
            )
        return None

    def _evaluate_ai_reversal(
        self,
        current_tick: TickData,
        feature_vector: FeatureVector,
        held_position_dirs: dict[int, str],
        prob_buy: float,
        prob_sell: float,
        no_trade_prob: float = 0.0,
        atr: float = 1.5,
        regime_str: str = "UNKNOWN",
        regime_conf: float = 0.0,
    ) -> TradeProposal | None:
        """
        AI Position Reversal veto.

        Detects the case where the engine holds an active BUY while a strong SELL signal
        emerges (or vice versa) and returns a CLOSE_POSITION proposal carrying
        reason_code AI_REVERSAL_SIGNAL plus the intended `reversal_action`.

        Reversal requires BOTH:
          - relative directional bias of the opposing side >= ai_flip_relative_bias_threshold
            (or an absolute probability lead of at least ai_flip_min_delta), AND
          - structural agreement from SMC/Ichimoku (ChoCh, liquidity sweep, or Kumo side),
            so a single noisy inference cannot flip a healthy position.

        Returns None when no reversal is warranted, leaving the standard flow untouched.
        """
        # BUG-251 (Agent-5 decision forensics, 2026-09-05): normalize the
        # reversal bias to the SAME trained-class semantics the entry gate
        # uses (CHG-0042 / _directional_confidence). The old raw-only bias
        # `buy/(buy+sell)` diverged from the entry confidence
        # `max(buy,sell)/(buy+sell+no_trade)`: with large NO_TRADE mass a
        # conviction that PASSES the entry gate could FAIL this bias (or
        # vice versa). Degenerate mass falls back to the raw directional
        # share (never manufactures conviction).
        trained_mass = (
            max(0.0, prob_buy) + max(0.0, prob_sell) + max(0.0, float(no_trade_prob))
        )
        if trained_mass > 0.0 and math.isfinite(trained_mass):
            rel_buy_bias = max(0.0, prob_buy) / trained_mass
            rel_sell_bias = max(0.0, prob_sell) / trained_mass
        else:
            total_active = max(0.0, prob_buy) + max(0.0, prob_sell) + 1e-8
            rel_buy_bias = max(0.0, prob_buy) / total_active
            rel_sell_bias = max(0.0, prob_sell) / total_active

        rel_threshold = getattr(self.algo_config, "ai_flip_relative_bias_threshold", 0.60)
        min_delta = getattr(self.algo_config, "ai_flip_min_delta", 0.10)

        strong_sell = (rel_sell_bias >= rel_threshold) or (prob_sell > prob_buy + min_delta)
        strong_buy = (rel_buy_bias >= rel_threshold) or (prob_buy > prob_sell + min_delta)

        # Structural confirmation guards against whipsaw on model noise alone.
        choch_bull = bool(getattr(feature_vector, "choch_bullish", False))
        choch_bear = bool(getattr(feature_vector, "choch_bearish", False))
        sweep_sig = int(
            self._sanitize_float(getattr(feature_vector, "liquidity_sweep_signal", 0), 0.0)
        )
        below_kumo = bool(getattr(feature_vector, "is_below_kumo", False))
        above_kumo = bool(getattr(feature_vector, "is_above_kumo", False))

        sell_structure = choch_bear or sweep_sig == -1 or below_kumo
        buy_structure = choch_bull or sweep_sig == 1 or above_kumo

        for ticket, direction in held_position_dirs.items():
            reversal_action: ActionType | None = None

            if direction == "BUY" and strong_sell and sell_structure:
                reversal_action = ActionType.SELL_MARKET
            elif direction == "SELL" and strong_buy and buy_structure:
                reversal_action = ActionType.BUY_MARKET

            if reversal_action is None:
                continue

            is_buy_reversal = reversal_action == ActionType.BUY_MARKET
            entry = current_tick.ask if is_buy_reversal else current_tick.bid
            stop_loss = (
                round(entry - atr * 1.5, 2) if is_buy_reversal else round(entry + atr * 1.5, 2)
            )
            take_profit = (
                round(entry + atr * 3.0, 2) if is_buy_reversal else round(entry - atr * 3.0, 2)
            )
            confidence = float(prob_buy if is_buy_reversal else prob_sell)
            # BUG-251: report reversal confidence in the SAME trained-class
            # scale as the entry gate (clamp >= 0, no manufacturing when the
            # mass is degenerate) so the audit row compares like with like.
            _mass = (
                max(0.0, prob_buy) + max(0.0, prob_sell) + max(0.0, float(no_trade_prob))
            )
            if _mass > 0.0 and math.isfinite(_mass):
                _norm = confidence / _mass
                if math.isfinite(_norm) and _norm >= 0.0:
                    confidence = float(_norm)

            logger.info(
                ">>> AI REVERSAL SIGNAL: opposing conviction detected, requesting CLOSE_POSITION before flip <<<",
                ticket=ticket,
                held=direction,
                new_action=reversal_action.value,
                prob_buy=round(prob_buy, 4),
                prob_sell=round(prob_sell, 4),
            )

            # NOTE: the proposal's action is CLOSE_POSITION so the invariant validator on
            # TradeProposal does not apply directional SL/TP checks. The intended new
            # direction travels in `reversal_action`, which order_manager dispatches only
            # after the conflicting ticket is confirmed closed.
            return TradeProposal(
                request_id=str(uuid.uuid4()),
                symbol=current_tick.symbol,
                generated_at=current_tick.timestamp,
                action=ActionType.CLOSE_POSITION,
                confidence=confidence,
                proposed_entry=float(entry),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_reward_ratio=2.0,
                reason_code=AI_REVERSAL_REASON,
                ticket=int(ticket),
                reversal_action=reversal_action,
                is_ai_reversal=True,
                model_action=reversal_action.value,
                buy_probability=prob_buy,
                sell_probability=prob_sell,
                regime=regime_str,
                regime_confidence=regime_conf,
                risk_allowed=True,
                guardian_status="IDLE",
                rejection_reason=None,
                final_action=ActionType.CLOSE_POSITION.value,
                execution_mode="AI_REVERSAL",
                override_reason="OPPOSING_SIGNAL_NO_STACKING",
                decision_stage="AI_REVERSAL_GATE",
                confidence_before_filters=confidence,
                confidence_after_filters=confidence,
            )

        return None

    def _build_no_trade(
        self,
        tick: TickData,
        confidence: float,
        reason: str,
        model_action: str = "NO_TRADE",
        buy_prob: float = 0.0,
        sell_prob: float = 0.0,
        no_trade_prob: float = 0.0,
        regime_str: str = "UNKNOWN",
        regime_conf: float = 0.0,
        risk_allowed: bool = False,
        guardian_status: str = "IDLE",
        proposed_entry: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        risk_reward_ratio: float | None = None,
        risk_checks: dict[str, Any] | None = None,
        blocked_by: str | None = None,
        decision_stage: str | None = None,
    ) -> TradeProposal:
        return TradeProposal(
            request_id=str(uuid.uuid4()),
            symbol=tick.symbol,
            generated_at=tick.timestamp,
            action=ActionType.NO_TRADE,
            confidence=float(confidence),
            proposed_entry=float(proposed_entry if proposed_entry is not None else tick.bid),
            stop_loss=float(stop_loss if stop_loss is not None else tick.bid * 0.99),
            take_profit=float(take_profit if take_profit is not None else tick.bid * 1.01),
            risk_reward_ratio=float(risk_reward_ratio if risk_reward_ratio is not None else 1.0),
            reason_code=reason,
            # Diagnostics
            model_action=model_action,
            buy_probability=buy_prob,
            sell_probability=sell_prob,
            no_trade_probability=no_trade_prob,
            regime=regime_str,
            regime_confidence=regime_conf,
            risk_allowed=risk_allowed,
            guardian_status=guardian_status,
            rejection_reason=reason,
            final_action="NO_TRADE",
            risk_checks=risk_checks,
            execution_mode="STANDARD",
            override_reason=None,
            decision_stage=decision_stage or "NO_TRADE_BUILDER",
            blocked_by=blocked_by,
            htf_score=0.0,
            smc_score=float(confidence),
            confidence_before_filters=float(confidence),
            confidence_after_filters=float(confidence),
        )

    def extract_live_chart_overlays(
        self, completed_bars: list[Any], atr_val: float
    ) -> dict[str, Any]:
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

        [b.close for b in completed_bars]
        highs = [b.high for b in completed_bars]
        lows = [b.low for b in completed_bars]
        [b.open for b in completed_bars]

        # 1. Swing Highs & Swing Lows
        swing_highs = []
        swing_lows = []
        for i in range(5, len(completed_bars) - 5):
            window_highs = [b.high for b in completed_bars[i - 5 : i + 6]]
            window_lows = [b.low for b in completed_bars[i - 5 : i + 6]]
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
                bos_lines.append(
                    {
                        "id": f"bos_sh_{i}",
                        "price": float(prev_shs[-1]),
                        "type": "BULLISH_BOS",
                        "time": completed_bars[i].timestamp.isoformat(),
                    }
                )
            if prev_sls and completed_bars[i].close < prev_sls[-1]:
                bos_lines.append(
                    {
                        "id": f"bos_sl_{i}",
                        "price": float(prev_sls[-1]),
                        "type": "BEARISH_BOS",
                        "time": completed_bars[i].timestamp.isoformat(),
                    }
                )

        bos_lines = bos_lines[-10:]

        # 3. Midline calculation
        last_sh_idx, last_sh_val = swing_highs[-1]
        last_sl_idx, last_sl_val = swing_lows[-1]
        equilibrium_50 = last_sl_val + 0.50 * (last_sh_val - last_sl_val)
        midlines.append(
            {
                "id": "equilibrium_50",
                "price": float(equilibrium_50),
                "label": "50%",
                "time_start": completed_bars[min(last_sh_idx, last_sl_idx)].timestamp.isoformat(),
            }
        )

        # 4. OB Boxes & Liquidity Sweep (LIQ) Markers
        for i in range(2, len(completed_bars)):
            b_current = completed_bars[i]
            b_prev1 = completed_bars[i - 1]
            completed_bars[i - 2]

            # Bullish Order Block
            if b_current.close > b_prev1.high and b_prev1.close < b_prev1.open:
                price_low = b_prev1.low
                price_high = b_prev1.high
                rectangles.append(
                    {
                        "id": f"ob_bull_{i}",
                        "type": "BULLISH_ORDER_BLOCK",
                        "price_low": float(price_low),
                        "price_high": float(price_high),
                        "ai_confidence": 0.85,
                        "time": b_prev1.timestamp.isoformat(),
                    }
                )

            # Bearish Order Block
            if b_current.close < b_prev1.low and b_prev1.close > b_prev1.open:
                price_low = b_prev1.low
                price_high = b_prev1.high
                rectangles.append(
                    {
                        "id": f"ob_bear_{i}",
                        "type": "BEARISH_ORDER_BLOCK",
                        "price_low": float(price_low),
                        "price_high": float(price_high),
                        "ai_confidence": 0.85,
                        "time": b_prev1.timestamp.isoformat(),
                    }
                )

            # Liquidity sweeps
            recent_high_10 = (
                max([b.high for b in completed_bars[max(0, i - 11) : i]])
                if i > 0
                else b_current.high
            )
            recent_low_10 = (
                min([b.low for b in completed_bars[max(0, i - 11) : i]]) if i > 0 else b_current.low
            )

            if b_current.low < recent_low_10 and b_current.close > recent_low_10:
                liq_markers.append(
                    {
                        "id": f"liq_low_{i}",
                        "type": "SELL_SIDE_LIQUIDITY_SWEEP",
                        "price": float(b_current.low),
                        "time": b_current.timestamp.isoformat(),
                    }
                )
            elif b_current.high > recent_high_10 and b_current.close < recent_high_10:
                liq_markers.append(
                    {
                        "id": f"liq_high_{i}",
                        "type": "BUY_SIDE_LIQUIDITY_SWEEP",
                        "price": float(b_current.high),
                        "time": b_current.timestamp.isoformat(),
                    }
                )

        rectangles = rectangles[-15:]
        liq_markers = liq_markers[-15:]

        return {
            "rectangles": rectangles,
            "bos_lines": bos_lines,
            "midlines": midlines,
            "liq_markers": liq_markers,
        }

    # ------------------------------------------------------------------
    # CONFIDENCE-SEMANTICS REPAIR (2026-09-02): 4-logit head vs 3-class
    # trained label contract. WAIT (index 3) is a legacy policy bridge
    # with zero training examples - it must never dilute or carry
    # directional confidence. The gate now measures the leading trained-
    # class probability normalized over BUY+SELL+NO_TRADE. O(1), no I/O.
    # ------------------------------------------------------------------
    def _directional_confidence(self, probs: list[float]) -> tuple[float, str]:
        """Return (confidence, source) under trained-class semantics."""
        prob_buy = self._sanitize_float(probs[1] if len(probs) > 1 else 0.0, 0.0)
        prob_sell = self._sanitize_float(probs[2] if len(probs) > 2 else 0.0, 0.0)
        prob_no_trade = self._sanitize_float(probs[0] if len(probs) > 0 else 0.0, 0.0)
        raw_directional = max(prob_buy, prob_sell)
        if len(probs) < 3:
            # Malformed/short vector: fall back to raw semantics.
            return raw_directional, "RAW_FALLBACK"
        trained_mass = prob_buy + prob_sell + prob_no_trade
        if trained_mass <= 0.0 or not math.isfinite(trained_mass):
            # Degenerate mass (e.g. all-zero or NaN components): never
            # manufacture confidence - pre-fix raw behavior.
            return raw_directional, "RAW_FALLBACK"
        conf = raw_directional / trained_mass
        if not math.isfinite(conf) or conf < 0.0:
            return raw_directional, "RAW_FALLBACK"
        return conf, "DIRECTIONAL_NORMALIZED"

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
