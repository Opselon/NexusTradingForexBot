from datetime import UTC, datetime

import pytest
import torch

from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
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
    import copy

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
