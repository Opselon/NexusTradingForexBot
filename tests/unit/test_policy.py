from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


class MockOrderManager:
    def __init__(self, live_tickets=None):
        self.live_tickets = live_tickets or []

    def get_active_live_tickets(self):
        return self.live_tickets


def _make_tick():
    from nexus_scalp.domain.models import TickData

    return TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )


def _make_feature_vector():
    from nexus_scalp.features.scalp_features import FeatureVector

    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )


def test_same_level_reentry_blocked_cleared_when_no_live_orders():
    """Verify SAME_LEVEL_REENTRY_BLOCKED is cleared when there are no live orders on the MT5 terminal chart."""
    policy = SignalPolicy()
    # Mock order manager with no live tickets
    om = MockOrderManager(live_tickets=[])

    # Tick and features
    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )

    # Let's propose a BUY trade first with some historical price lock
    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_executed_price = 2000.0
    policy.last_order_price = 2000.0

    # Since om has no live orders, the re-entry lock should be released instantly,
    # allowing another BUY_MARKET at the exact same level!
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
        order_manager=om,
    )

    assert proposal.action == ActionType.BUY_MARKET
    assert "SAME_LEVEL_REENTRY_BLOCKED" not in proposal.reason_code


def test_same_level_reentry_blocked_triggers_with_live_order():
    """Verify SAME_LEVEL_REENTRY_BLOCKED triggers when there is a live order near the proposed entry price."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10

    # Mock order manager with a live ticket on XAUUSD, magic 888101 at price 2000.00
    om = MockOrderManager(
        live_tickets=[
            {
                "ticket": 999,
                "symbol": "XAUUSD",
                "price": 2000.00,
                "magic": 888101,
                "type": "POSITION",
            }
        ]
    )

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )

    # Propose buy (ask is 2000.10, which is within $0.50 of the live ticket price 2000.00)
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
        order_manager=om,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert "SAME_LEVEL_REENTRY_BLOCKED" in proposal.reason_code


def test_sr_support_margin_relaxation_strong_bearish():
    """Verify support margin check is relaxed for short trades when HTF Bearish Momentum is strong."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0
    )
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=-0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=-0.5,
        consecutive_momentum_count=-1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=False,
        is_below_kumo=True,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=-1.0,  # Strong Bearish
        htf_h1_momentum=-2.5,  # Strong Bearish
        htf_m30_structure=-1.0,
        htf_m15_confirmation=-1.0,
        support_zone_dist=0.15,  # Distance is 0.15 (< 0.25)
        resistance_zone_dist=5.0,
        trend_strength=-1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )

    # Propose SELL
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.01, 0.98, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action != ActionType.NO_TRADE
    assert "SELL_REJECTED_SR_SUPPORT_MARGIN_FAIL" not in proposal.reason_code


def test_tick_sweep_requires_model_confidence():
    """PERF FORENSICS (2026-08-18): tick-level liquidity sweeps must NOT
    fire with zero model confidence. Previously the path returned before the
    confidence gate, entering trades at conf=0.00 that lost -$189/-$190
    (1.5-ATR SL, no probability support)."""

    policy = SignalPolicy()
    fv = _make_feature_vector()
    # FeatureVector is frozen; rebuild with the sweep signal via model_copy.
    fv = fv.model_copy(update={"liquidity_sweep_signal": 1})  # type: ignore[attr-defined]

    # LOW probability (0.10 buy): sweep must be REJECTED
    low_probs = torch.tensor([[0.90, 0.10, 0.00, 0.0]])
    tick = _make_tick()
    policy._last_tick_bid = 2000.10
    policy._last_tick_ask = 2000.15
    proposal = policy.evaluate_probabilities(
        probabilities=low_probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert "TICK_LEVEL_LIQUIDITY_SWEEP" not in proposal.reason_code

    # HIGH probability (0.95 buy): if the sweep fires, confidence must be REAL
    high_probs = torch.tensor([[0.02, 0.95, 0.03, 0.0]])
    policy._last_tick_bid = 2000.10
    policy._last_tick_ask = 2000.15
    proposal2 = policy.evaluate_probabilities(
        probabilities=high_probs,
        current_tick=tick,
        feature_vector=fv,
    )
    if "TICK_LEVEL_LIQUIDITY_SWEEP" in proposal2.reason_code:
        assert proposal2.confidence >= 0.5, (
            f"sweep confidence must be real, got {proposal2.confidence}"
        )


def test_candidate_confidence_is_raw_probability_not_floor():
    """PERF FORENSICS (2026-08-18): the synthetic `0.55 + prob*0.35` floor
    inflated every candidate to >= 0.61, so the confidence gate never rejected
    weak signals (ledger: 192/233 trades at conf 0.0-0.4, bulk of the loss).
    Confidence must be the REAL directional model probability."""
    import copy

    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())
    tick = _make_tick()

    probs = torch.tensor([[0.55, 0.42, 0.03, 0.0]])
    policy.confidence_threshold = 0.35
    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    if proposal.action != ActionType.NO_TRADE:
        assert proposal.confidence <= 0.45, (
            f"confidence {proposal.confidence} must reflect raw prob, not the 0.55+ floor"
        )


def test_execution_id_stamped_on_no_trade_confidence_block():
    """PHASE 13 forensic audit (2026-08-20): every evaluation must carry a
    unique EXEC-... id even when the signal is rejected at the confidence
    gate — the id is the single join key across radar logs, audit_signals,
    audit_orders and the broker ticket."""
    import copy

    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())
    tick = _make_tick()

    probs = torch.tensor([[0.05, 0.20, 0.75, 0.0]])
    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.execution_id is not None
    assert proposal.execution_id.startswith("EXEC-"), proposal.execution_id
    assert len(proposal.execution_id) >= 20, proposal.execution_id


def test_execution_id_unique_across_evaluations():
    """PHASE 13: consecutive evaluations must NOT reuse the same EXEC id
    (two ticks one second apart -> two distinct trace ids)."""
    import copy

    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())
    t1 = _make_tick()
    t2 = _make_tick()
    t2 = t2.model_copy(update={"timestamp": t1.timestamp + timedelta(seconds=1)})

    p1 = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=t1,
        feature_vector=fv,
    )
    p2 = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.20, 0.75, 0.0]]),
        current_tick=t2,
        feature_vector=fv,
    )
    assert p1.execution_id != p2.execution_id


def test_execution_id_stamped_on_actionable_proposal():
    """PHASE 13: an actionable proposal (high confidence directional prob)
    also carries the EXEC id so dispatch is joinable."""
    import copy

    policy = SignalPolicy()
    fv = copy.deepcopy(_make_feature_vector())
    tick = _make_tick()

    probs = torch.tensor([[0.05, 0.95, 0.00, 0.0]])
    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    # A 0.95 directional probability must survive the confidence gate; the
    # proposal (whatever its final action) must carry the trace id.
    assert proposal.execution_id is not None
    assert proposal.execution_id.startswith("EXEC-")


def test_confidence_rejection_telemetry_breakdown():
    """Verify that insufficient confidence rejections contain a fully transparent
    breakdown (model confidence, base threshold, range penalty, survival mode adjustment,
    effective threshold) in both reason_code/rejection_reason and risk_checks payload."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    policy.range_confidence_penalty = 0.15

    fv = _make_feature_vector().model_copy(
        update={
            "is_above_kumo": True,
            "tenkan_sen": 1999.0,
            "kijun_sen": 1998.0,
            "live_tick_displacement": 0.5,
        }
    )

    tick = _make_tick()

    # Case A: raw buy 0.45 under a 4-logit head. Candidate-side trained-
    # class measure = 0.45 / (0.05 + 0.45 + 0.50) = 0.45 (the SELL slice
    # does not dilute the BUY side), which still fails survival
    # 0.40 + 0.10 = 0.50 -> CONFIDENCE_FAIL (thresholds unchanged).
    probs = torch.tensor([[0.05, 0.45, 0.50, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
        survival_mode=True,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "CONFIDENCE_FAIL"
    assert "INSUFFICIENT_CONFIDENCE" in proposal.reason_code
    assert "Model Confidence (0.45)" in proposal.reason_code
    assert "Effective Threshold (0.50)" in proposal.reason_code
    assert proposal.risk_checks["confidence_source"] == "DIRECTIONAL_NORMALIZED"
    assert "Base: 0.40" in proposal.reason_code
    assert "Range Penalty: +0.00" in proposal.reason_code
    assert "Survival Mode: +0.10" in proposal.reason_code

    rc = proposal.risk_checks
    assert rc is not None
    assert abs(rc["model_confidence"] - 0.45) < 1e-4
    assert rc["base_threshold"] == 0.40
    assert rc["range_penalty"] == 0.0
    assert rc["survival_mode_adjustment"] == 0.10
    assert rc["effective_threshold"] == 0.50


def test_confidence_telemetry_payload_always_carries_breakdown():
    """Every rejected signal must carry the complete threshold breakdown in its
    risk_checks audit payload (base, range penalty, survival adjustment, effective)."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    policy.range_confidence_penalty = 0.15

    fv = _make_feature_vector().model_copy(
        update={
            "is_above_kumo": False,
            "is_below_kumo": False,  # inside kumo => range market => +range penalty
            "live_tick_displacement": 0.01,
        }
    )
    tick = _make_tick()

    # prob_buy=0.30 -> candidate confidence 0.30, well below any combination; reaches a reject gate.
    probs = torch.tensor([[0.05, 0.30, 0.65, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
        survival_mode=True,  # effective = 0.40 + 0.15 + 0.10 = 0.65
    )

    assert proposal.action == ActionType.NO_TRADE
    rc = proposal.risk_checks
    assert rc is not None
    # The breakdown must always be present and reflect the configured adjustments.
    assert rc["base_threshold"] == 0.40
    assert rc["range_penalty"] == 0.15
    assert rc["survival_mode_adjustment"] == 0.10
    assert rc["effective_threshold"] == 0.65
    # Reason code carries the human-readable breakdown when rejected at confidence gate.
    if proposal.blocked_by == "CONFIDENCE_FAIL":
        assert "INSUFFICIENT_CONFIDENCE" in proposal.reason_code
        assert "Range Penalty: +0.15" in proposal.reason_code
        assert "Survival Mode: +0.10" in proposal.reason_code


def test_is_numeric_validation_invalid_entry_price():
    """Verify is_numeric raises ValueError when target_entry_price is non-finite or boolean."""
    import math

    import pytest

    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    probs = torch.tensor([[0.01, 0.98, 0.01, 0.0]])  # BUY candidate
    fv = _make_feature_vector()

    invalid_entry_prices = [math.nan, math.inf, -math.inf, True, False]

    for invalid_val in invalid_entry_prices:
        policy._dedup_last_bid = 0.0
        tick = _make_tick().model_copy(update={"ask": invalid_val, "bid": 2000.0})
        with pytest.raises(ValueError, match=r"Invalid entry price:"):
            policy.evaluate_probabilities(
                probabilities=probs,
                current_tick=tick,
                feature_vector=fv,
            )


def test_is_numeric_validation_invalid_swing_low_and_high():
    """Verify is_numeric raises ValueError when dist_to_swing_low_20 or dist_to_swing_high_20 is invalid."""
    import math

    import pytest

    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    probs = torch.tensor([[0.01, 0.98, 0.01, 0.0]])  # BUY candidate

    invalid_swing_values = [math.nan, math.inf, -math.inf, None, True, False, "invalid"]

    for invalid_val in invalid_swing_values:
        policy._dedup_last_bid = 0.0
        tick = _make_tick()
        fv_low = _make_feature_vector().model_copy(update={"dist_to_swing_low_20": invalid_val})
        with pytest.raises(ValueError, match=r"Invalid dist_to_swing_low_20:"):
            policy.evaluate_probabilities(
                probabilities=probs,
                current_tick=tick,
                feature_vector=fv_low,
            )

        policy._dedup_last_bid = 0.0
        tick = _make_tick()
        fv_high = _make_feature_vector().model_copy(update={"dist_to_swing_high_20": invalid_val})
        with pytest.raises(ValueError, match=r"Invalid dist_to_swing_high_20:"):
            policy.evaluate_probabilities(
                probabilities=probs,
                current_tick=tick,
                feature_vector=fv_high,
            )


def test_is_numeric_validation_valid_numeric_inputs():
    """Verify is_numeric accepts valid int and float values for target_entry_price, atr, dist_to_swing_low_20, dist_to_swing_high_20."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    probs = torch.tensor([[0.01, 0.98, 0.01, 0.0]])  # BUY candidate
    tick = _make_tick().model_copy(update={"ask": 2000.20})

    # Test with valid integer and float values
    fv = _make_feature_vector().model_copy(
        update={
            "dist_to_swing_low_20": 2,  # integer
            "dist_to_swing_high_20": 2.5,  # float
            "atr_m1": 1.80,  # float
        }
    )

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert proposal.action == ActionType.BUY_MARKET


def test_evaluate_tick_sweep_initial_tick_state():
    """Verify _evaluate_tick_sweep returns None when _last_tick_bid <= 0.0 (first tick)."""
    policy = SignalPolicy()
    assert policy._last_tick_bid == 0.0

    tick = _make_tick()
    proposal = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-TEST-001",
        now=datetime.now(UTC),
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert proposal is None


def test_evaluate_tick_sweep_buy_success():
    """Verify BUY tick level sweep triggers when all conditions are met."""
    policy = SignalPolicy(confidence_threshold=0.40)
    policy._last_tick_bid = 2000.50
    policy._last_tick_ask = 2000.60

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.00, ask=2000.10, volume=1.0)

    proposal = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick,
        ofi=0.25,  # > 0 for BUY
        tick_velocity=8.0,  # > 5.0
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-TEST-BUY",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.confidence == 0.85
    assert proposal.proposed_entry == 2000.10  # ask
    assert proposal.stop_loss == 2000.10 - 2.0 * 1.5  # 1997.10
    assert proposal.take_profit == 2000.10 + 2.0 * 3.0  # 2006.10
    assert proposal.risk_reward_ratio == 2.0
    assert proposal.reason_code == "TICK_LEVEL_LIQUIDITY_SWEEP_BUY"
    assert proposal.execution_mode == "TICK_SWEEP"
    assert proposal.override_reason == "TICK_VELOCITY_TRIGGERED"
    assert proposal.decision_stage == "TICK_SWEEP_EXECUTION"
    assert proposal.risk_checks["decision_path"] == "TICK_SWEEP"
    assert proposal.risk_checks["structural_gate"]["direction"] == "BUY"


def test_evaluate_tick_sweep_sell_success():
    """Verify SELL tick level sweep triggers when all conditions are met."""
    policy = SignalPolicy(confidence_threshold=0.40)
    policy._last_tick_bid = 2000.50
    policy._last_tick_ask = 2000.60

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.70, ask=2000.80, volume=1.0)

    proposal = policy._evaluate_tick_sweep(
        sweep_sig=-1,
        current_tick=tick,
        ofi=-0.25,  # < 0 for SELL
        tick_velocity=8.0,  # > 5.0
        raw_prob_buy=0.05,
        raw_prob_sell=0.85,
        is_range_market=False,
        execution_id="EXEC-TEST-SELL",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=-1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.SELL_MARKET
    assert proposal.confidence == 0.85
    assert proposal.proposed_entry == 2000.70  # bid
    assert proposal.stop_loss == 2000.70 + 2.0 * 1.5  # 2003.70
    assert proposal.take_profit == 2000.70 - 2.0 * 3.0  # 1994.70
    assert proposal.risk_reward_ratio == 2.0
    assert proposal.reason_code == "TICK_LEVEL_LIQUIDITY_SWEEP_SELL"
    assert proposal.execution_mode == "TICK_SWEEP"
    assert proposal.risk_checks["structural_gate"]["direction"] == "SELL"


def test_evaluate_tick_sweep_range_market_penalty():
    """Verify tick sweep confidence threshold accounts for range market penalty."""
    policy = SignalPolicy(confidence_threshold=0.40, range_confidence_penalty=0.10)
    policy._last_tick_bid = 2000.50
    policy._last_tick_ask = 2000.60

    now = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=now, bid=2000.00, ask=2000.10, volume=1.0)

    # Effective threshold in range = 0.40 + 0.10 = 0.50
    # Case A: buy prob 0.45 < 0.50 -> rejected (returns None)
    proposal_rejected = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.45,
        raw_prob_sell=0.05,
        is_range_market=True,
        execution_id="EXEC-TEST-RANGE-REJ",
        now=now,
        atr=2.0,
        regime_str="RANGING_MEAN_REVERSION",
        regime_conf=0.80,
        trend_strength=0.0,
    )
    assert proposal_rejected is None

    # Case B: buy prob 0.55 >= 0.50 -> accepted
    proposal_accepted = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.55,
        raw_prob_sell=0.05,
        is_range_market=True,
        execution_id="EXEC-TEST-RANGE-ACC",
        now=now,
        atr=2.0,
        regime_str="RANGING_MEAN_REVERSION",
        regime_conf=0.80,
        trend_strength=0.0,
    )
    assert proposal_accepted is not None
    assert proposal_accepted.action == ActionType.BUY_MARKET


def test_evaluate_tick_sweep_gating_failures():
    """Verify _evaluate_tick_sweep returns None when any single gating condition fails."""
    policy = SignalPolicy(confidence_threshold=0.40)
    policy._last_tick_bid = 2000.50
    policy._last_tick_ask = 2000.60

    now = datetime.now(UTC)
    tick_buy_pass = TickData(symbol="XAUUSD", timestamp=now, bid=2000.00, ask=2000.10, volume=1.0)

    # 1. sweep_sig == 0
    p1 = policy._evaluate_tick_sweep(
        sweep_sig=0,
        current_tick=tick_buy_pass,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-FAIL-1",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert p1 is None

    # 2. Price movement mismatch: BUY signal but bid did NOT pierce lower (bid >= last_bid)
    tick_no_pierce = TickData(symbol="XAUUSD", timestamp=now, bid=2000.50, ask=2000.60, volume=1.0)
    p2 = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick_no_pierce,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-FAIL-2",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert p2 is None

    # 3. OFI direction mismatch: BUY sweep but OFI <= 0
    p3 = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick_buy_pass,
        ofi=-0.10,  # <= 0
        tick_velocity=8.0,
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-FAIL-3",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert p3 is None

    # 4. Low velocity: tick_velocity <= 5.0
    p4 = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick_buy_pass,
        ofi=0.25,
        tick_velocity=5.0,  # Needs > 5.0
        raw_prob_buy=0.85,
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-FAIL-4",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert p4 is None

    # 5. Insufficient directional confidence: raw_prob_buy < threshold
    p5 = policy._evaluate_tick_sweep(
        sweep_sig=1,
        current_tick=tick_buy_pass,
        ofi=0.25,
        tick_velocity=8.0,
        raw_prob_buy=0.35,  # < 0.40
        raw_prob_sell=0.05,
        is_range_market=False,
        execution_id="EXEC-FAIL-5",
        now=now,
        atr=2.0,
        regime_str="TRENDING",
        regime_conf=0.90,
        trend_strength=1.0,
    )
    assert p5 is None


def test_evaluate_probabilities_tick_sweep_integration():
    """Verify full evaluate_probabilities pipeline correctly invokes _evaluate_tick_sweep across ticks."""
    policy = SignalPolicy(confidence_threshold=0.40)
    fv = _make_feature_vector().model_copy(
        update={
            "liquidity_sweep_signal": 1,
        }
    )

    t1 = TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=2000.50,
        ask=2000.60,
        volume=1.0,
    )

    # First evaluation seeds _last_tick_bid = 2000.50 and _last_tick_ask = 2000.60
    probs1 = torch.tensor([[0.05, 0.85, 0.10, 0.0]])
    policy.evaluate_probabilities(
        probabilities=probs1,
        current_tick=t1,
        feature_vector=fv,
    )

    # Second tick: bid drops to 2000.00 (pierces 2000.50), timestamp advances
    t2 = TickData(
        symbol="XAUUSD",
        timestamp=t1.timestamp + timedelta(seconds=1),
        bid=2000.00,
        ask=2000.10,
        volume=1.0,
    )

    # Create mock regime state with positive OFI and high velocity
    from nexus_scalp.features.regime_classifier import (
        MarketRegimeState,
        RecommendedExecutionType,
        RegimeReason,
        RegimeType,
    )

    regime_state = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=t2.timestamp.isoformat(),
        regime_type=RegimeType.TRENDING_MOMENTUM,
        regime_probability=0.90,
        order_flow_imbalance=0.30,  # OFI > 0
        realized_volatility_5m=0.001,
        tick_velocity_per_sec=8.0,  # velocity > 5.0
        current_spread_usd=0.10,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.IOC_MARKET,
        reason=RegimeReason.OFI_TREND_ALIGN,
    )

    probs2 = torch.tensor([[0.05, 0.85, 0.10, 0.0]])
    proposal2 = policy.evaluate_probabilities(
        probabilities=probs2,
        current_tick=t2,
        feature_vector=fv,
        regime_state=regime_state,
    )

    assert proposal2.action == ActionType.BUY_MARKET
    assert "TICK_LEVEL_LIQUIDITY_SWEEP_BUY" in proposal2.reason_code
    assert proposal2.execution_mode == "TICK_SWEEP"
