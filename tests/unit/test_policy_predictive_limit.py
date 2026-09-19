"""Unit tests for SignalPolicy._evaluate_predictive_limit."""

from datetime import UTC, datetime

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


class MockBar:
    def __init__(self, low: float, high: float):
        self.low = low
        self.high = high


def _make_tick(bid: float = 2000.0, ask: float = 2000.2) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def _make_feature_vector(order_block_type: int = 1) -> FeatureVector:
    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.1,
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
        order_block_type=order_block_type,
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


def test_evaluate_predictive_limit_returns_none_when_ob_invalid():
    """Verify _evaluate_predictive_limit returns None when valid_ob is False."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp

    proposal = policy._evaluate_predictive_limit(
        valid_ob=False,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-TEST-001",
        now=now,
        confidence=0.8,
        confidence_before_filters=0.8,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )
    assert proposal is None


def test_evaluate_predictive_limit_returns_none_when_god_mode_active():
    """Verify _evaluate_predictive_limit returns None when smc_god_mode_active is True."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=True,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-TEST-002",
        now=now,
        confidence=0.8,
        confidence_before_filters=0.8,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )
    assert proposal is None


def test_evaluate_predictive_limit_returns_none_when_exposure_reached():
    """Verify _evaluate_predictive_limit returns None when total_exposure >= MAX_TOTAL_EXPOSURE."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = tick.timestamp

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=1,  # MAX_TOTAL_EXPOSURE is 1
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-TEST-003",
        now=now,
        confidence=0.8,
        confidence_before_filters=0.8,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )
    assert proposal is None


def test_evaluate_predictive_limit_buy_limit_default_swings():
    """Verify BUY_LIMIT generation with default swing calculations when completed_bars is None."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-TEST-004",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    assert proposal.execution_mode == "PREDICTIVE_LIMIT"
    assert proposal.reason_code == "PREDICTIVE_OB_BUY_LIMIT_EQUILIBRIUM"
    assert proposal.decision_stage == "PREDICTIVE_LIMIT_GENERATION"
    assert proposal.override_reason == "PREDICTIVE_OB_PLACEMENT"

    # Default swing low: bid - atr = 2000.0 - 2.0 = 1998.0
    # Default swing high: ask + atr = 2000.2 + 2.0 = 2002.2
    # Equilibrium 50%: 1998.0 + 0.5 * (2002.2 - 1998.0) = 2000.1
    assert proposal.proposed_entry == 2000.1
    # Deepest wick = swing_low_20 = 1998.0
    # Stop loss = round(1998.0 - 2.0 * atr_sl_buffer_multiplier (1.5), 2) = 1995.0
    assert proposal.stop_loss == 1995.0

    assert proposal.risk_checks["decision_path"] == "PREDICTIVE_LIMIT"
    assert proposal.risk_checks["confidence_gate_applied"] is False
    assert proposal.risk_checks["structural_gate"]["valid_ob"] is True
    assert proposal.risk_checks["structural_gate"]["order_block_type"] == 1


def test_evaluate_predictive_limit_buy_limit_with_completed_bars():
    """Verify BUY_LIMIT generation using completed_bars min low and max high."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2005.0)
    now = tick.timestamp

    # Create 20 bars with lows from 1990 to 2000 and highs from 2000 to 2010
    bars = [MockBar(low=1990.0 + i * 0.5, high=2000.0 + i * 0.5) for i in range(20)]

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=bars,
        execution_id="EXEC-TEST-005",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    # 20-bar min low = 1990.0
    # 20-bar max high = 2009.5
    # Equilibrium = 1990.0 + 0.5 * (2009.5 - 1990.0) = 1999.75
    assert proposal.proposed_entry == 1999.75
    # Stop loss = 1990.0 - 2.0 * 1.5 = 1987.0
    assert proposal.stop_loss == 1987.0


def test_evaluate_predictive_limit_buy_limit_clamped_when_above_ask():
    """Verify BUY_LIMIT entry price is clamped to ask - 0.12 when target entry >= ask."""
    policy = SignalPolicy()
    tick = _make_tick(bid=1999.8, ask=2000.0)
    now = tick.timestamp

    # 20-bar window yielding equilibrium 2005.0 which is > ask (2000.0)
    bars = [MockBar(low=1995.0, high=2015.0) for _ in range(20)]

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=bars,
        execution_id="EXEC-TEST-006",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    # target_entry_price clamped to ask - 0.12 = 2000.0 - 0.12 = 1999.88
    assert proposal.proposed_entry == 1999.88


def test_evaluate_predictive_limit_buy_limit_min_rr_enforcement():
    """Verify take_profit is adjusted when initial RR is below active_min_rr."""
    policy = SignalPolicy()
    policy.algo_config.min_risk_reward_ratio = 2.0
    tick = _make_tick(bid=2000.0, ask=2002.0)
    now = tick.timestamp

    # Setup bars so swing_high is very close to target entry, giving low initial RR
    # Low = 1990.0, High = 2003.0 => Eq = 1996.5
    bars = [MockBar(low=1990.0, high=2003.0) for _ in range(20)]

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=5.0,  # Large ATR creates large risk = target - SL
        completed_bars=bars,
        execution_id="EXEC-TEST-007",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.BUY_LIMIT
    # SL = 1990.0 - 5.0 * 1.5 = 1982.5
    # Risk = abs(1996.5 - 1982.5) = 14.0
    # Required min TP distance = 14.0 * 2.0 = 28.0
    # Adjusted TP = 1996.5 + 28.0 = 2024.5
    assert proposal.take_profit == 2024.5
    assert proposal.risk_reward_ratio >= 2.0


def test_evaluate_predictive_limit_sell_limit_default_swings():
    """Verify SELL_LIMIT generation with default swing calculations when completed_bars is None."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=-1,
        current_tick=tick,
        atr=2.0,
        completed_bars=None,
        execution_id="EXEC-TEST-008",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.SELL_LIMIT
    assert proposal.execution_mode == "PREDICTIVE_LIMIT"
    assert proposal.reason_code == "PREDICTIVE_OB_SELL_LIMIT_EQUILIBRIUM"
    assert proposal.decision_stage == "PREDICTIVE_LIMIT_GENERATION"

    # Default swing low: bid - atr = 1998.0
    # Default swing high: ask + atr = 2002.2
    # Equilibrium 50%: 2000.10
    assert proposal.proposed_entry == 2000.1
    # Deepest wick = swing_high_20 = 2002.2
    # Stop loss = round(2002.2 + 2.0 * 1.5, 2) = 2005.2
    assert proposal.stop_loss == 2005.2
    assert proposal.risk_checks["structural_gate"]["order_block_type"] == -1


def test_evaluate_predictive_limit_sell_limit_clamped_when_below_bid():
    """Verify SELL_LIMIT entry price is clamped to bid + 0.12 when target entry <= bid."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    now = tick.timestamp

    # Bars yielding equilibrium 1990.0 which is <= bid (2000.0)
    bars = [MockBar(low=1980.0, high=2000.0) for _ in range(20)]

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=-1,
        current_tick=tick,
        atr=2.0,
        completed_bars=bars,
        execution_id="EXEC-TEST-009",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.SELL_LIMIT
    # target_entry_price clamped to bid + 0.12 = 2000.0 + 0.12 = 2000.12
    assert proposal.proposed_entry == 2000.12


def test_evaluate_predictive_limit_sell_limit_min_rr_enforcement():
    """Verify take_profit is adjusted for SELL_LIMIT when initial RR is below active_min_rr."""
    policy = SignalPolicy()
    policy.algo_config.min_risk_reward_ratio = 2.0
    tick = _make_tick(bid=2000.0, ask=2002.0)
    now = tick.timestamp

    # Setup bars so swing_low is very close to target entry
    # Low = 1995.0, High = 2010.0 => Eq = 2002.5
    bars = [MockBar(low=1995.0, high=2010.0) for _ in range(20)]

    proposal = policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=-1,
        current_tick=tick,
        atr=5.0,
        completed_bars=bars,
        execution_id="EXEC-TEST-010",
        now=now,
        confidence=0.85,
        confidence_before_filters=0.85,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )

    assert proposal is not None
    assert proposal.action == ActionType.SELL_LIMIT
    # Deepest wick = 2010.0, SL = 2010.0 + 5.0 * 1.5 = 2017.5
    # Target entry = 2002.5
    # Risk = abs(2002.5 - 2017.5) = 15.0
    # Required min TP distance = 15.0 * 2.0 = 30.0
    # Adjusted TP = 2002.5 - 30.0 = 1972.5
    assert proposal.take_profit == 1972.5
    assert proposal.risk_reward_ratio >= 2.0


def test_evaluate_probabilities_triggers_predictive_limit():
    """Integration test verifying evaluate_probabilities triggers _evaluate_predictive_limit."""
    policy = SignalPolicy()
    tick = _make_tick(bid=2000.0, ask=2000.2)
    fv = _make_feature_vector(order_block_type=1)

    # Moderate probability vector where standard directional rules do not trigger market orders,
    # but order_block_type=1 triggers predictive limit
    probs = torch.tensor([[0.80, 0.10, 0.10, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action == ActionType.BUY_LIMIT
    assert proposal.execution_mode == "PREDICTIVE_LIMIT"
    assert proposal.reason_code == "PREDICTIVE_OB_BUY_LIMIT_EQUILIBRIUM"
    assert policy._last_signal_time == tick.timestamp
