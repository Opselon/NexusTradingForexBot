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


def test_evaluate_exposure_limits_under_max_exposure_returns_none():
    """When total exposure is below MAX_TOTAL_EXPOSURE (1), returns None (gate passes)."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = datetime.now(UTC)

    result = policy._evaluate_exposure_limits(
        total_exposure=0,
        active_positions_count=0,
        active_pending_count=0,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
    )
    assert result is None


def test_evaluate_exposure_limits_same_level_reentry_blocked():
    """When a live ticket exists within $0.50 of target_entry_price, returns SAME_LEVEL_REENTRY_BLOCKED."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = datetime.now(UTC)
    om = MockOrderManager(
        live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 2000.20}]
    )

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=om,
        live_tickets=om.live_tickets,
        target_entry_price=2000.00,  # diff = 0.20 < 0.50
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        expected_symbol="XAUUSD",
        expected_magic=888101,
        execution_id="exec-123",
    )

    assert result is not None
    assert result.action == ActionType.NO_TRADE
    assert result.reason_code == "SAME_LEVEL_REENTRY_BLOCKED"
    assert result.execution_id == "exec-123"


def test_evaluate_exposure_limits_max_exposure_active_position():
    """When active_positions_count >= 1 and no same-level order, returns MAX_EXPOSURE_REACHED with EXECUTION_STATE_BLOCK."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = datetime.now(UTC)

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=1,
        active_pending_count=0,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        execution_id="exec-456",
    )

    assert result is not None
    assert result.action == ActionType.NO_TRADE
    assert result.reason_code == "MAX_EXPOSURE_REACHED"
    assert result.blocked_by == "EXECUTION_STATE_BLOCK"
    assert result.decision_stage == "EXPOSURE_GATE"
    assert result.execution_id == "exec-456"


def test_evaluate_exposure_limits_pending_order_locked_time_gate():
    """When active_pending_count >= 1 and time_delta <= 30s, returns PENDING_ORDER_LOCKED."""
    policy = SignalPolicy()
    tick = _make_tick()
    now = datetime.now(UTC)

    # Set locked pending order state within 30s lock window (e.g. 10s ago)
    policy._locked_pending_time = now - timedelta(seconds=10)
    policy._locked_pending_price = 2000.0

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
    )

    assert result is not None
    assert result.action == ActionType.NO_TRADE
    assert result.reason_code == "PENDING_ORDER_LOCKED"
    assert result.blocked_by == "EXECUTION_STATE_BLOCK"


def test_evaluate_exposure_limits_pending_order_locked_drift_gate():
    """When active_pending_count >= 1, time_delta > 30s, but price_drift < 1.0 * atr, returns PENDING_ORDER_LOCKED."""
    policy = SignalPolicy()
    tick = _make_tick()  # bid=2000.0, ask=2000.2
    now = datetime.now(UTC)

    # Pending lock was set 40s ago (unlocked by time)
    policy._locked_pending_time = now - timedelta(seconds=40)
    # Default swing calculation without bars: low = bid - atr = 1998.0, high = ask + atr = 2002.2
    # new_eq_price = round(1998.0 + 0.5 * (2002.2 - 1998.0), 2) = round(2000.1, 2) = 2000.10
    # Set locked price close to new_eq_price so price_drift < 1.0 * atr (drift = 0.10 < 2.0)
    policy._locked_pending_price = 2000.00

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
    )

    assert result is not None
    assert result.action == ActionType.NO_TRADE
    assert result.reason_code == "PENDING_ORDER_LOCKED"


def test_evaluate_exposure_limits_pending_order_unlocked():
    """When active_pending_count >= 1, time_delta > 30s, and price_drift >= 1.0 * atr, returns None (unlocked)."""
    policy = SignalPolicy()
    tick = _make_tick()  # bid=2000.0, ask=2000.2
    now = datetime.now(UTC)

    # Pending lock was set 40s ago (> 30s)
    policy._locked_pending_time = now - timedelta(seconds=40)
    # new_eq_price without bars will be 2000.10.
    # Set locked price far away so drift = abs(2000.10 - 1990.0) = 10.1 >= 1.0 * atr (2.0)
    policy._locked_pending_price = 1990.0

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
    )

    assert result is None


def test_evaluate_exposure_limits_completed_bars_equilibrium_calc():
    """Verify equilibrium price calculation correctly uses completed_bars when 20 or more bars are provided."""
    from types import SimpleNamespace

    policy = SignalPolicy()
    tick = _make_tick()
    now = datetime.now(UTC)

    # Create 20 mock bars with known low (1900.0) and high (2000.0)
    bars = [SimpleNamespace(low=1950.0, high=1980.0) for _ in range(19)]
    bars.append(SimpleNamespace(low=1900.0, high=2000.0))  # swing low = 1900.0, high = 2000.0
    # Expected equilibrium = 1900.0 + 0.5 * (2000.0 - 1900.0) = 1950.00

    policy._locked_pending_time = now - timedelta(seconds=40)
    # Set locked price to 1950.00 so drift = 0 < 2.0 (atr), resulting in PENDING_ORDER_LOCKED
    policy._locked_pending_price = 1950.00

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=bars,
        now=now,
    )

    assert result is not None
    assert result.reason_code == "PENDING_ORDER_LOCKED"

    # Now change locked price to 1940.0 so drift = 10.0 >= 2.0 (atr), resulting in None (unlocked)
    policy._locked_pending_price = 1940.00
    result_unlocked = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=None,
        live_tickets=[],
        target_entry_price=2000.0,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=bars,
        now=now,
    )
    assert result_unlocked is None


def test_evaluate_exposure_limits_expected_symbol_resolution():
    """Verify expected_symbol auto-resolves when expected_symbol is passed as None."""
    policy = SignalPolicy()
    tick = _make_tick()  # symbol="XAUUSD"
    now = datetime.now(UTC)

    # Ticket matching tick symbol ("XAUUSD") and default magic (888101) within same level
    om = MockOrderManager(
        live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 2000.10}]
    )

    result = policy._evaluate_exposure_limits(
        total_exposure=1,
        active_positions_count=0,
        active_pending_count=1,
        order_manager=om,
        live_tickets=om.live_tickets,
        target_entry_price=2000.00,
        current_tick=tick,
        regime_str="TRENDING",
        regime_conf=0.8,
        atr=2.0,
        completed_bars=None,
        now=now,
        expected_symbol=None,  # Should auto-resolve to tick symbol ("XAUUSD")
    )

    assert result is not None
    assert result.reason_code == "SAME_LEVEL_REENTRY_BLOCKED"
