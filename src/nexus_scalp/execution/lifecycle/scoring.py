"""PositionScoringEngine — hold-value, protection and trajectory scoring.

P0 seam S9 (god-file decomposition, extraction 3 of the order-manager wave):
the PURE/low-state scoring cluster leaves ``OrderLifecycleManager`` verbatim
(behavior-preserving extraction). Owns the per-ticket trajectory history
(bounded deque of PositionEvaluationStep) — the one remaining piece of
per-ticket state this engine OWNS; everything else is read through the
manager's canonical surface (``om``): S5 ticket-state views, protection
ledger, rule-matrix config.

Moved methods (verbatim bodies, ``self`` -> ``om``):
    _calculate_protection_score            : giveback/protection scoring
    _calculate_continuous_giveback_severity
    _calculate_adaptive_evidence_scores    : trajectory-feature evidence
    _calculate_trajectory_features
    _calculate_hold_value_score            : canonical hold-value score
    _evaluate_minimum_loss_optimization
    _add_trajectory_step                   : trajectory writer (state owner)

Ownership contract:
    READS   : S5 views, protection ledger, algo config (via om)
    WRITES  : its own trajectory history
    AUTHORITY: NONE — no broker I/O, no dispatch, no protection actions.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.lifecycle.scoring")


def _om_symbols() -> tuple[float, float, type]:
    """Late-bound god-module symbols (import cycle breaker).

    PositionEvaluationStep / giveback constants remain defined on the
    facade module for compatibility; the scoring engine imports them
    lazily so the facade can compose this engine at module scope.
    """
    from nexus_scalp.execution.order_manager import (
        PROFIT_GIVEBACK_MIN_RETENTION,
        PROFIT_GIVEBACK_PEAK_USD,
        PositionEvaluationStep,
    )

    return PROFIT_GIVEBACK_MIN_RETENTION, PROFIT_GIVEBACK_PEAK_USD, PositionEvaluationStep


class PositionScoringEngine:
    """Hold-value / protection / trajectory scoring owner (S9)."""

    def __init__(self, om: Any) -> None:
        # Composition root (OrderLifecycleManager).
        self.om = om
        #: ticket -> bounded trajectory observation window (100 steps).
        self._trajectory_history: dict[int, deque[Any]] = {}

    def _calculate_protection_score(
        self,
        ticket: int,
        pos: Position,
        base_hold_score: int,
        pnl_features: dict[str, float],
        evidence: dict[str, float],
        confidence_factor: float,
        atr: float,
    ) -> float:
        """
        Calculates a continuous protection score (0.0 to 100.0) combining baseline state weights
        with continuous risk severity, including protection escalation as risk deteriorates.
        """
        # Retrieve centralized weights from AlgoConfig
        w_prof = getattr(self.om.algo_config, "w_profit_retention", 0.30)
        w_pnl = getattr(self.om.algo_config, "w_pnl_trajectory", 0.15)
        w_dd_vel = getattr(self.om.algo_config, "w_drawdown_velocity", 0.15)
        w_rev = getattr(self.om.algo_config, "w_market_reversal", 0.20)
        w_rec = getattr(self.om.algo_config, "w_recovery_probability", 0.10)
        w_hscore = getattr(self.om.algo_config, "w_hold_score", 0.10)

        # Scale weights continuously based on position state (context-dependent weights)
        is_profitable = pos.profit >= 0.0
        if is_profitable:
            # Shift weight toward profit retention and continuation
            w_prof *= 1.5
            w_rec *= 0.2
        else:
            # Shift weight toward drawdown velocity, recovery, and market reversal
            w_dd_vel *= 1.5
            w_rev *= 1.3
            w_rec *= 1.2
            w_prof *= 0.1

        # Normalize weights
        total_w = w_prof + w_pnl + w_dd_vel + w_rev + w_rec + w_hscore
        if total_w > 0.0:
            w_prof /= total_w
            w_pnl /= total_w
            w_dd_vel /= total_w
            w_rev /= total_w
            w_rec /= total_w
            w_hscore /= total_w

        # Scaled continuous input variables [0.0, 1.0]
        profit_giveback_severity = self.om._calculate_continuous_giveback_severity(
            ticket, pos.profit
        )

        # PnL deterioration: 1.0 when PnL slope is highly negative
        pnl_slope = pnl_features.get("pnl_slope", 0.0)
        pnl_deterioration = max(0.0, min(1.0, -pnl_slope * 2.0))

        # Drawdown velocity (scaled)
        dd_vel = pnl_features.get("drawdown_velocity", 0.0)
        drawdown_velocity = max(0.0, min(1.0, dd_vel * 3.0))

        # Reversal probability (scaled by AI confidence factor continuously)
        effective_ai_weight = confidence_factor
        adverse_prob = evidence.get("adverse_score", 0.0)
        reversal_probability = adverse_prob * effective_ai_weight

        # Recovery probability
        rec_prob = evidence.get("recovery_score", 0.0)
        # We weigh (1 - recovery_probability) as protection pressure
        recovery_probability_pressure = (1.0 - rec_prob) * effective_ai_weight

        # Hold score deterioration
        hold_score_deterioration = max(0.0, min(1.0, (100.0 - base_hold_score) / 100.0))

        # Time risk: increases as time underwater grows
        time_below_be = pnl_features.get("time_below_breakeven", 0.0)
        time_risk = max(0.0, min(1.0, time_below_be / self.om.max_holding_seconds))

        # Combine variables
        protection_score = (
            w_prof * profit_giveback_severity
            + w_pnl * pnl_deterioration
            + w_dd_vel * drawdown_velocity
            + w_rev * reversal_probability
            + w_rec * recovery_probability_pressure
            + w_hscore * hold_score_deterioration
        )

        # Apply escalation multiplier as risk deteriorates (near hard SL or high time risk)
        escalation_factor = 1.0
        if not is_profitable:
            # Escalation based on time underwater and negative trend slope
            escalation_factor += 0.5 * time_risk
            if pnl_slope < 0.0:
                escalation_factor += 0.3 * min(1.0, abs(pnl_slope))

        protection_score *= escalation_factor
        return float(max(0.0, min(100.0, protection_score * 100.0)))

    def _calculate_continuous_giveback_severity(self, ticket: int, current_pnl_usd: float) -> float:
        """
        Calculates a continuous giveback severity metric (0.0 to 1.0).
        0.0 means no giveback (at peak).
        1.0 means catastrophic giveback (at or below the minimum retention floor).
        """
        state = self.om.get_protection_state(ticket)
        peak = state.peak_win_usd
        _MIN_RETENTION, _PEAK_USD, _ = _om_symbols()
        if peak < _PEAK_USD:
            return 0.0

        catastrophic_floor = peak * _MIN_RETENTION
        giveback_range = peak - catastrophic_floor
        if giveback_range <= 0.0:
            return 0.0

        severity = (peak - current_pnl_usd) / giveback_range
        return float(max(0.0, min(1.0, severity)))

    def _calculate_adaptive_evidence_scores(
        self,
        ticket: int,
        pos: Position,
        probs: Any | None,
        features: FeatureVector | None,
    ) -> dict[str, float]:
        """
        Computes normalized evidence scores (recovery_score, adverse_score, continuation_score)
        derived from either live neural network predictions (probs) or a bounded evidence/score model fallback.
        """
        is_buy = pos.type == OrderType.BUY
        pnl_features = self.om._calculate_trajectory_features(ticket)
        pnl_slope = pnl_features.get("pnl_slope", 0.0)

        # 1. Base model predictions if available
        if probs is not None:
            try:
                probs_list = probs.squeeze().tolist()
                if not isinstance(probs_list, list):
                    probs_list = [probs_list]

                # Model predicts: 0=NO_TRADE, 1=BUY, 2=SELL
                p_no_trade = float(probs_list[0]) if len(probs_list) > 0 else 0.4
                p_buy = float(probs_list[1]) if len(probs_list) > 1 else 0.3
                p_sell = float(probs_list[2]) if len(probs_list) > 2 else 0.3

                # Ensure internally consistent normalization
                total_prob = p_no_trade + p_buy + p_sell + 1e-9
                p_no_trade /= total_prob
                p_buy /= total_prob
                p_sell /= total_prob

                if is_buy:
                    continuation_score = p_buy
                    adverse_score = p_sell
                else:
                    continuation_score = p_sell
                    adverse_score = p_buy

            except Exception as err:
                logger.error(
                    "Error parsing neural network probabilities; falling back to heuristic",
                    error=str(err),
                )
                probs = None

        if probs is None:
            # Bounded evidence fallback model (Requirement 1 & 2)
            # Baseline is 0.40
            continuation_score = 0.40
            adverse_score = 0.40

            # Dynamic indicators from feature vector
            if features is not None:
                # Ichimoku trend alignment
                if is_buy and features.is_above_kumo:
                    continuation_score += 0.15
                elif is_buy and features.is_below_kumo:
                    adverse_score += 0.15
                elif not is_buy and features.is_below_kumo:
                    continuation_score += 0.15
                elif not is_buy and features.is_above_kumo:
                    adverse_score += 0.15

                # Choch alignment
                choch_bull = getattr(features, "choch_bullish", False)
                choch_bear = getattr(features, "choch_bearish", False)
                if is_buy and choch_bull:
                    continuation_score += 0.10
                elif is_buy and choch_bear:
                    adverse_score += 0.10
                elif not is_buy and choch_bear:
                    continuation_score += 0.10
                elif not is_buy and choch_bull:
                    adverse_score += 0.10

            # Slope adjustments
            if pnl_slope > 0.0:
                continuation_score += 0.10
                adverse_score -= 0.05
            elif pnl_slope < 0.0:
                adverse_score += 0.10
                continuation_score -= 0.05

            # Strictly normalize
            total = continuation_score + adverse_score + 0.20  # 0.20 represents 'no_trade'
            continuation_score /= total
            adverse_score /= total

        # Compute recovery score
        # Mixture of continuation score and actual recovery trajectory velocity
        rec_vel = pnl_features.get("recovery_velocity", 0.0)
        # Scaled recovery velocity (USD/sec)
        rec_vel_scaled = min(1.0, max(0.0, rec_vel * 5.0))
        recovery_score = 0.70 * continuation_score + 0.30 * rec_vel_scaled

        return {
            "continuation_score": max(0.0, min(1.0, continuation_score)),
            "adverse_score": max(0.0, min(1.0, adverse_score)),
            "recovery_score": max(0.0, min(1.0, recovery_score)),
        }

    def _calculate_trajectory_features(self, ticket: int) -> dict[str, float]:
        """
        Calculates time-aware trajectory features (slopes, velocities, acceleration)
        using an efficient window of the last 10 steps to avoid expensive linear regression.
        """
        history = self._trajectory_history.get(ticket)
        if not history or len(history) < 2:
            return {
                "pnl_slope": 0.0,
                "price_slope": 0.0,
                "drawdown_velocity": 0.0,
                "drawdown_acceleration": 0.0,
                "recovery_velocity": 0.0,
                "time_since_peak": 0.0,
                "time_below_entry": 0.0,
                "time_below_breakeven": 0.0,
                "distance_to_be_velocity": 0.0,
            }

        window = list(history)[-10:]
        first = window[0]
        last = window[-1]
        dt = (last.timestamp - first.timestamp).total_seconds()
        if dt <= 0.0:
            dt = 0.1

        pnl_slope = (last.pnl - first.pnl) / dt
        price_slope = (last.price - first.price) / dt

        prev = window[-2]
        dt_last = (last.timestamp - prev.timestamp).total_seconds()
        if dt_last <= 0.0:
            dt_last = 0.1

        last_dd_vel = (last.drawdown - prev.drawdown) / dt_last

        if len(window) >= 3:
            prev_prev = window[-3]
            dt_prev = (prev.timestamp - prev_prev.timestamp).total_seconds()
            if dt_prev <= 0.0:
                dt_prev = 0.1
            prev_dd_vel = (prev.drawdown - prev_prev.drawdown) / dt_prev
            drawdown_acceleration = (last_dd_vel - prev_dd_vel) / dt_last
        else:
            drawdown_acceleration = 0.0

        drawdown_velocity = last_dd_vel
        recovery_velocity = pnl_slope if pnl_slope > 0.0 else 0.0

        peak_step = max(history, key=lambda s: s.pnl)
        time_since_peak = (last.timestamp - peak_step.timestamp).total_seconds()

        time_below_entry = 0.0
        time_below_breakeven = 0.0
        for i in range(1, len(history)):
            s_prev = history[i - 1]
            s_curr = history[i]
            s_dt = (s_curr.timestamp - s_prev.timestamp).total_seconds()
            if s_curr.pnl < 0.0:
                time_below_entry += s_dt
            if s_curr.pnl < 0.20:
                time_below_breakeven += s_dt

        if last.pnl < 0.0 and prev.pnl < 0.0:
            distance_to_be_velocity = (abs(last.pnl) - abs(prev.pnl)) / dt_last
        else:
            distance_to_be_velocity = 0.0

        return {
            "pnl_slope": pnl_slope,
            "price_slope": price_slope,
            "drawdown_velocity": drawdown_velocity,
            "drawdown_acceleration": drawdown_acceleration,
            "recovery_velocity": recovery_velocity,
            "time_since_peak": max(0.0, time_since_peak),
            "time_below_entry": max(0.0, time_below_entry),
            "time_below_breakeven": max(0.0, time_below_breakeven),
            "distance_to_be_velocity": distance_to_be_velocity,
        }

    def _calculate_hold_value_score(
        self,
        pos: Position,
        price_current: float,
        features: FeatureVector | None,
        impact_price_delta: float,
        atr: float,
        smart_metrics: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[int, list[str]]:
        """
        Calculates position Hold Value Score (0 to 100) dynamically based on real-time metrics:
        Base Score = 100
        Penalty 1 (Drawdown vs Initial Risk/ATR): Subtract up to 40 points as current loss approaches SL.
        Penalty 2 (Time-in-Loss Decay): Subtract up to 30 points if time_loss > 70% of holding duration.
        Penalty 3 (Real-time Spread Expansion): Subtract points if current broker spread exceeds 1.5x rolling average spread.
        Bonus: Add up to 10 points if the PyTorch AI probability still strongly favors the position direction.
        """
        score = 100
        reasons: list[str] = []
        ticket = pos.ticket

        # --- Penalty 1: Drawdown vs Initial Risk/ATR (convex, up to -80) ---
        # Non-linear: as drawdown deepens relative to the planned risk, the penalty
        # accelerates so the engine de-risks gracefully LONG before the emergency
        # horizon. A linear ratio*40 leaves a 50%-of-risk drawdown at score ~80,
        # which keeps the position in the "hold" band until a hard bailout.
        initial_sl = self.om._entry_sls.get(ticket, pos.sl)
        is_buy = pos.type == OrderType.BUY
        current_loss = 0.0
        initial_risk = 0.0

        if is_buy:
            current_loss = max(0.0, pos.price_open - price_current)
            initial_risk = pos.price_open - initial_sl if initial_sl > 0.0 else (atr * 1.5)
        else:
            current_loss = max(0.0, price_current - pos.price_open)
            initial_risk = initial_sl - pos.price_open if initial_sl > 0.0 else (atr * 1.5)

        if current_loss > 0.0:
            ratio = current_loss / max(0.01, initial_risk)
            # Convex curve: ratio=0.2 -> ~9, ratio=0.5 -> ~36, ratio=0.8 -> ~72,
            # ratio=1.0 -> 80. Score drops decisively below the <50 de-risk band
            # around 40-50% of planned risk, well before a hard stop is needed.
            penalty1 = int(min(80.0, 80.0 * (min(ratio, 1.0) ** 1.5)))
            if penalty1 > 0:
                score -= penalty1
                reasons.append(f"DRAWDOWN_PENALTY (-{penalty1}, ratio={ratio:.2f})")

        # --- Penalty 2: Time-in-Loss Decay (up to -30) ---
        # BUG-259 (TASK-HOLD-CLOCK parity): the penalty denominator MUST be the
        # tick-threaded `now` (the management loop passes now = tick.timestamp),
        # never the host wall clock. The old wall-clock fallback mixed clock
        # domains: a host wall clock hours behind the broker/tick domain derived
        # a near-zero (or negative) duration and fired a spurious -30, while the
        # opposite skew inflated the duration and suppressed the penalty forever
        # (production "Age: -10781.6s" class). With no usable `now` (direct/unit
        # callers, or naive/aware domain mismatch) the duration is conservatively
        # 0.0 — the >0.70 ratio gate can then never fire on corrupted arithmetic
        # — plus a rate-limited loud WARNING (G3 HOLD_AGE_FALLBACK pattern).
        entry_time = self.om._entry_timestamps.get(ticket)
        holding_duration = 0.0
        if (
            entry_time is not None
            and now is not None
            and (entry_time.tzinfo is None) == (now.tzinfo is None)
        ):
            holding_duration = (now - entry_time).total_seconds()
        elif entry_time is not None:
            now_mono = time.monotonic()
            if (now_mono - getattr(self, "_hold_age_fallback_warned_at", 0.0)) >= 300.0:
                self._hold_age_fallback_warned_at = now_mono
                logger.warning(
                    "[POSITION] event=HOLD_AGE_FALLBACK "
                    "mode=no_tick_timestamp_conservative_zero "
                    "ticket=%s entry_time_present=%s "
                    "(tick timestamp missing in hold-score call; "
                    "wall-clock duration suppressed — BUG-259)",
                    ticket,
                    True,
                )
        time_loss = self.om._time_in_drawdown_sec.get(ticket, 0.0)

        if holding_duration > 0.0 and (time_loss / holding_duration) > 0.70:
            score -= 30
            reasons.append("TIME_IN_LOSS_DECAY_PENALTY (-30)")

        # --- Penalty 3: Real-time Spread Expansion (up to -20) ---
        if self.om._rolling_spreads:
            current_spread = self.om._rolling_spreads[-1]
            avg_spread = sum(self.om._rolling_spreads) / len(self.om._rolling_spreads)
            if avg_spread > 0.0 and current_spread > 1.5 * avg_spread:
                score -= 20
                reasons.append("SPREAD_EXPANSION_PENALTY (-20)")

        # --- Bonus: AI/Trend alignment (+10), suppressed while meaningfully underwater ---
        # A positive trend signal must never mask a deep drawdown: bonuses are only
        # worth considering when the position is not materially adverse.
        drawdown_ratio = current_loss / max(0.01, initial_risk) if current_loss > 0.0 else 0.0
        underwater = drawdown_ratio >= 0.30
        if features is not None:
            aligned = False
            if is_buy and features.is_above_kumo:
                aligned = True
            elif not is_buy and features.is_below_kumo:
                aligned = True

            if aligned and not underwater:
                score += 10
                reasons.append("TREND_ALIGNMENT_BONUS (+10)")
            elif aligned and underwater:
                reasons.append("TREND_BONUS_SUPPRESSED_UNDERWATER")

        # PROFIT SHIELD GUARD: Winning trades get guaranteed high floor score of 85.
        # The guard is based on ACTUAL floating PnL, not price-vs-open (which can be
        # fooled by spread/whipsaw), and it is disabled once the position is under
        # water by more than 30% of planned risk so a real loss can never be masked.
        is_in_profit = pos.profit >= 0.0
        if is_in_profit and not underwater:
            score = max(85, score)
            reasons.append("PROFIT_SHIELD_SCORE_FLOOR_ACTIVE")

        return max(0, min(100, score)), reasons

    def _evaluate_minimum_loss_optimization(
        self,
        ticket: int,
        current_pnl_usd: float,
        initial_risk_usd: float,
        evidence: dict[str, float],
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        Continuously calculates the expected value of holding vs exiting.
        Returns (should_exit, reason) for exiting at the smallest statistically justified loss.
        """
        if current_pnl_usd >= 0.0:
            return False, ""

        # Calculate time in trade for Spread Overcome Grace Period
        # NOTE: `now` is the CURRENT TICK timestamp threaded from the management
        # loop. Never derive age from the host wall clock: the broker/server clock
        # can be hours ahead of the host, which produced negative ages
        # (e.g. "Age: -10781.6s") and suppressed every time-based exit.
        # BUG-268 (clock-domain parity with G3/BUG-259/BUG-260): the
        # no-threaded-now branch used to fall back to the HOST WALL CLOCK,
        # mixing it with the broker-domain
        # entry anchor. A host clock AHEAD of the tick domain opened the 60s
        # grace gate instantly on a fresh position (the MIN_LOSS exit cluster
        # can fire before the trade has breathed); a host clock BEHIND kept the
        # gate shut for the true grace plus the skew. Now: duration is computed
        # ONLY when both stamps share one tz domain; otherwise conservative
        # 0.0 (grace stays closed) + a rate-limited loud WARNING, exactly the
        # G3 HOLD_AGE_FALLBACK pattern.
        entry_time = self.om._entry_timestamps.get(ticket)
        duration_sec = 0.0
        if entry_time is not None:
            if now is not None and (entry_time.tzinfo is None) == (now.tzinfo is None):
                duration_sec = (now - entry_time).total_seconds()
            else:
                now_mono = time.monotonic()
                if (now_mono - getattr(self, "_hold_age_fallback_warned_at", 0.0)) >= 300.0:
                    self._hold_age_fallback_warned_at = now_mono
                    logger.warning(
                        "[POSITION] event=HOLD_AGE_FALLBACK "
                        "mode=no_tick_timestamp_conservative_zero "
                        "ticket=%s entry_time_present=%s "
                        "(tick timestamp missing/unusable in minimum-loss call; "
                        "wall-clock duration suppressed — BUG-268)",
                        ticket,
                        True,
                    )

        # 60-Second Spread Overcome Grace Period (Prevent instant exit due to spread costs at open)
        if duration_sec < 60.0:
            return False, ""

        recovery_score = evidence.get("recovery_score", 0.50)
        adverse_score = evidence.get("adverse_score", 0.50)

        # Expected Outcomes (payoff magnitudes)
        # Phase 15 audit finding #4 fix (BUG-056): the recovery value MUST be
        # anchored to the PLANNED reward objective (initial risk x minimum
        # risk-reward ratio), never to the CURRENT loss magnitude. The old
        #   expected_recovery_value = max(15.0, abs(current_pnl_usd) * 2.0)
        # grew with the loss while expected_additional_loss = max(1.0, risk -
        # |pnl|) shrank, so EV became MORE positive the deeper the drawdown
        # (verified: EV +55.86 vs threshold -29.53 at pnl -171.12, risk 196.88,
        # rec 0.204, adv 0.542) — the minimum-loss exit could never fire.
        # The payoff is fixed at entry time (reward = initial_risk * RRR) and
        # the additional loss is the REAL remaining distance to the hard SL
        # (initial_risk - |pnl|), which is the honest downside still at stake.
        # A deep-drawdown guard below (drawdown consumed > 60% of risk with
        # weak recovery) catches the case where EV alone stays positive because
        # the remaining SL distance is small — the statistics say exit.
        planned_rr = float(getattr(self.om.algo_config, "min_risk_reward_ratio", 1.8) or 1.8)
        expected_recovery_value = max(15.0, initial_risk_usd * planned_rr)
        expected_additional_loss = max(1.0, initial_risk_usd - abs(current_pnl_usd))

        # Expected Value (EV) calculation
        ev_hold = (
            recovery_score * expected_recovery_value - adverse_score * expected_additional_loss
        )

        # Minimum-loss exit condition: if the EV of holding is severely negative, or if recovery evidence is weak
        if ev_hold < -0.15 * initial_risk_usd:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_EV_BREACH triggered. Ticket: {ticket}, EV: ${ev_hold:.2f}, RecProb: {recovery_score:.2%}, AdvProb: {adverse_score:.2%}, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_EV_BREACH (EV=${ev_hold:.2f}, rec_prob={recovery_score:.2%}, adv_prob={adverse_score:.2%})",
            )

        # Deep-drawdown guard (BUG-056): when the position has consumed most of
        # its planned risk (>60%) and the model sees weak recovery (<30%), the
        # remaining SL distance is small so EV alone can look positive; the
        # statistics say exit before the hard stop is fully consumed.
        drawdown_fraction = abs(current_pnl_usd) / max(initial_risk_usd, 1.0)
        if drawdown_fraction > 0.60 and recovery_score < 0.30:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_DEEP_DRAWDOWN triggered. Ticket: {ticket}, "
                f"RecProb: {recovery_score:.2%}, Drawdown: {drawdown_fraction:.1%} of risk, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_DEEP_DRAWDOWN (rec_prob={recovery_score:.2%}, drawdown={drawdown_fraction:.1%})",
            )

        if recovery_score < 0.25 and adverse_score > 0.60:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_WEAK_RECOVERY triggered. Ticket: {ticket}, RecProb: {recovery_score:.2%}, AdvProb: {adverse_score:.2%}, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_WEAK_RECOVERY (rec_prob={recovery_score:.2%}, adv_prob={adverse_score:.2%})",
            )

        return False, ""

    def _add_trajectory_step(
        self,
        ticket: int,
        timestamp: datetime,
        pnl: float,
        price: float,
        hold_score: int,
        drawdown: float,
        retention: float,
        atr: float,
        volatility: float,
    ) -> None:
        """Appends a new observation step to the ticket's bounded trajectory history."""
        if ticket not in self._trajectory_history:
            self._trajectory_history[ticket] = deque(maxlen=100)

        _, _, _Step = _om_symbols()
        step = _Step(
            timestamp=timestamp,
            pnl=float(pnl),
            price=float(price),
            hold_score=int(hold_score),
            drawdown=float(drawdown),
            retention=float(retention),
            atr=float(atr),
            volatility=float(volatility),
        )
        self._trajectory_history[ticket].append(step)
