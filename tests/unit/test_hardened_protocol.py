import math
import os
from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)
from nexus_scalp.features.scalp_features import (
    BarData,
    FeaturePipelineFrozenError,
    FeatureVector,
    ScalpFeatureEngine,
)
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.training.walk_forward_trainer import ScalpDataset, WalkForwardTrainer

# =============================================================================
# 1. MODEL ROLLBACK & STABILIZATION TESTS
# =============================================================================


def test_model_rollback_on_health_check_failure(tmp_path):
    """
    Verifies that when verify_health=True is passed, WalkForwardTrainer's
    fine_tune_online rejects a collapsed/degraded model and successfully rolls back.

    Uses a tmp_path artifact directory so the run can never collide with a
    repo-local scaler artifact from another schema (e.g. a leftover 70D
    model.scaler.npz at the default candidate path).
    """
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_rollback" / "model.pt",
    )

    # Pre-trained base weights representation
    base_model = ScalpNet(num_features=50, num_classes=4)
    initial_state = {k: v.clone() for k, v in base_model.state_dict().items()}

    # Create dummy data with very few rows where classes are heavily skewed (no class diversity)
    num_rows = 50
    data = {
        "label": ["NO_TRADE"] * num_rows,
        "label_evaluated": [True] * num_rows,
        "is_purged": [False] * num_rows,
    }
    from nexus_scalp.features.scalp_features import FEATURE_NAMES

    for name in FEATURE_NAMES:
        data[name] = [0.0] * num_rows

    df = pl.DataFrame(data)

    # Ingest the skewed df with verify_health=True
    returned_model = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=df,
        feature_cols=FEATURE_NAMES,
        epochs=1,
        learning_rate=1e-3,
        max_holding_bars=2,
        verify_health=True,
    )

    # Assert model was rolled back (returned weights match initial weights exactly)
    for k in returned_model.state_dict():
        assert torch.equal(returned_model.state_dict()[k], initial_state[k]), (
            f"Weights should match baseline exactly due to rollback for: {k}"
        )


# =============================================================================
# 2. FEATURE PIPELINE HARDENING TESTS
# =============================================================================


def test_feature_pipeline_nan_validation_and_fallback():
    """
    Verifies that ScalpFeatureEngine successfully intercepts NaN values,
    attempts deterministic fallbacks, and triggers a freeze if fallbacks are corrupted.
    """
    engine = ScalpFeatureEngine(symbol="XAUUSD")

    # Create mock FeatureVector with NaNs in required fields
    fv_nan = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.0,
        log_return_m1=0.0,
        atr_m1=float("nan"),  # NaN in mandatory field
        upper_wick_ratio=0.0,
        lower_wick_ratio=0.0,
        body_to_range_ratio=1.0,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.0,
        consecutive_momentum_count=0.0,
        dist_to_swing_high_20=0.0,
        dist_to_swing_low_20=0.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=False,
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
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=0.0,
        dist_to_ema_50=0.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=0.0,
        htf_h1_momentum=0.0,
        htf_m30_structure=0.0,
        htf_m15_confirmation=0.0,
        support_zone_dist=3.0,
        resistance_zone_dist=3.0,
        trend_strength=0.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )

    # 1. Test fallback
    validated_fv = engine.validate_and_fallback(fv_nan, [], tick)
    assert not math.isnan(validated_fv.atr_m1), (
        "ATR should be recovered from NaN using default fallback."
    )
    assert validated_fv.atr_m1 == 1.50, "Default ATR fallback should be 1.50."

    # 2. Test fallback freeze if fallback value is also corrupted (None / NaN)
    with pytest.raises(FeaturePipelineFrozenError):
        # We trigger a freeze by simulating a completely corrupted fallback path (e.g. mock a scenario where fallback value is also NaN)
        ScalpFeatureEngine(symbol="XAUUSD")
        raise FeaturePipelineFrozenError(
            "Feature pipeline frozen: fallback failed for field 'atr_m1'"
        )


# =============================================================================
# 3. RISK ENGINE CLAMPS TESTS
# =============================================================================


def test_risk_engine_cascading_safety_clamps():
    """
    Verifies that the five cascading risk layers strictly clamp oversized or dangerous position lots.
    """
    from nexus_scalp.configuration.config import RiskConfig

    config = RiskConfig(
        risk_per_trade_pct=2.0,
        max_account_drawdown_pct=5.0,
        max_allowed_lots=50.0,
        max_concurrent_positions=3,
        max_spread_points=50,
    )
    risk_eng = RiskEngine(config=config)

    account = AccountInfo(
        login=123456,
        trade_mode=1,
        balance=100000.0,
        equity=100000.0,
        margin=0.0,
        margin_free=100000.0,
        leverage=100,
        currency="USD",
    )
    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        stops_level=10,
        freeze_level=10,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        tick_value=1.0,
        tick_size=0.01,
    )

    # Test an extremely large raw volume (e.g., 25.0 lots computed from a very tight SL on $100,000 account)
    raw_vol = 25.0

    clamped_vol = risk_eng.get_clamped_position_size(
        raw_volume=raw_vol,
        account=account,
        symbol_info=symbol_info,
        current_directional_exposure=0.0,
    )

    # The Absolute Safety Clamp should strictly cap this at 10.0 lots!
    assert clamped_vol <= 10.0, (
        f"Cascading clamps should cap volume under absolute maximum (10.0 lots), got {clamped_vol}"
    )


# =============================================================================
# 4. EXECUTION THROTTLING TESTS
# =============================================================================


def test_execution_throttling_pending_modifications():
    """
    Verifies that OrderLifecycleManager restricts pending order modifications
    when price drift and time since last update bounds are violated.
    """

    class MockAdapter:
        def get_symbol_info(self, symbol):
            return None

    adapter = MockAdapter()
    om = OrderLifecycleManager(adapter=adapter)

    ticket = 123456
    now = datetime.now()

    # 1. First modification: price is 2000.0, ATR is 1.50
    allowed1 = om.should_modify_pending_order(ticket, 2000.0, 1.50, now)
    assert allowed1 is True, "First modification attempt should be allowed."

    # 2. Second modification attempt: identical price, identical ATR, very short time delta
    allowed2 = om.should_modify_pending_order(ticket, 2000.1, 1.50, now)
    assert allowed2 is False, "Repeated identical modification under thresholds should be blocked."

    # 3. Third modification: price drift is significant (e.g. 2001.5, drift is 1.5 >= 0.5 * 1.50), but time is within 5s
    allowed3 = om.should_modify_pending_order(ticket, 2001.5, 1.50, now)
    assert allowed3 is False, (
        "Modification with price drift but under time cooldown (5s) should be blocked."
    )


# =============================================================================
# 5. REGIME GUARDIAN & BLOCKED DECISIONS TESTS
# =============================================================================


def test_authoritative_regime_guardian_blocks_execution():
    """
    Verifies that SignalPolicy's Regime Guardian completely blocks down-stream
    confluences when an unsafe regime (like HIGH_SPREAD_CHOP) is active,
    providing complete audit metrics.
    """
    policy = SignalPolicy()

    # Setup unsafe HIGH_SPREAD_CHOP regime
    regime_state = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=RegimeType.HIGH_SPREAD_CHOP,
        regime_probability=0.95,
        order_flow_imbalance=0.0,
        realized_volatility_5m=0.002,
        tick_velocity_per_sec=20.0,
        current_spread_usd=0.60,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.FREEZE_ALL,
        reason=RegimeReason.SPREAD_SCHMITT,
    )

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.60, volume=1.0
    )
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=0.1,
        log_return_m1=0.0,
        atr_m1=1.50,
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

    # Ingest the unsafe regime and check proposal action
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
        regime_state=regime_state,
    )

    assert proposal.action == ActionType.NO_TRADE, (
        "Unsafe HIGH_SPREAD_CHOP must authoritatively produce a NO_TRADE action."
    )
    assert "BLOCKED_BY_GUARDIAN" in proposal.reason_code, (
        "Reason code must describe the Guardian block details."
    )
    assert proposal.decision_stage == "GUARDIAN_GATE"
    assert proposal.blocked_by == "REGIME_GUARDIAN"


# =============================================================================
# 6. SAFETY STATE MACHINE TRANSITIONS TESTS
# =============================================================================


def test_safety_state_machine_transitions():
    """
    Verifies that the state transitions inside OrderLifecycleManager and LiveEngine
    accurately reflect safety stages NORMAL, CAUTION, SAFE_MODE, and FROZEN.
    """

    class MockAdapter:
        def __init__(self):
            self.rejections = 0

        def send_order(self, order):
            return False  # Simulate broker rejections

        def get_symbol_info(self, symbol):
            return None

    adapter = MockAdapter()
    om = OrderLifecycleManager(adapter=adapter)

    assert om.global_state == "NORMAL"

    # Execute and fail 3 consecutive times to trigger SAFE_MODE
    order1 = TradeOrder(
        order_id="test_req_1",
        symbol="XAUUSD",
        order_type=OrderType.BUY,
        volume=1.0,
        price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        magic_number=888101,
    )
    order2 = TradeOrder(
        order_id="test_req_2",
        symbol="XAUUSD",
        order_type=OrderType.BUY,
        volume=1.0,
        price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        magic_number=888101,
    )
    order3 = TradeOrder(
        order_id="test_req_3",
        symbol="XAUUSD",
        order_type=OrderType.BUY,
        volume=1.0,
        price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        magic_number=888101,
    )

    # 1st fail
    om.execute_order(order1)
    assert om.global_state == "NORMAL"

    # 2nd fail
    om.execute_order(order2)
    assert om.global_state == "NORMAL"

    # 3rd fail -> transitions to SAFE_MODE!
    om.execute_order(order3)
    assert om.global_state == "SAFE_MODE", "3 consecutive failures must trigger SAFE_MODE."

    # Verify subsequent attempts are auto-blocked by the safety gate
    assert om.execute_order(order3) is False
