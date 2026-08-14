import math
import uuid
from datetime import UTC, datetime

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig, RiskConfig
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
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy


class DummyPendingOrder:
    def __init__(self, ticket, symbol, price_open, order_type, volume, sl=0.0, tp=0.0):
        self.ticket = ticket
        self.symbol = symbol
        self.price_open = price_open
        self.type = order_type
        self.volume = volume
        self.sl = sl
        self.tp = tp


class DummyMT5Port:
    def __init__(self):
        self.pending_orders = []
        self.positions = []
        self.cancelled_tickets = []

    def get_pending_orders(self, symbol):
        return self.pending_orders

    def cancel_pending_order(self, ticket):
        self.cancelled_tickets.append(ticket)
        self.pending_orders = [o for o in self.pending_orders if o.ticket != ticket]
        return True

    def get_positions(self, symbol):
        return self.positions

    def get_closed_deals_history(self, symbol, hours_back):
        return []


def test_god_mode_override():
    policy = SignalPolicy()
    policy.confidence_threshold = 0.50
    policy.algo_config.ai_zone_confidence_threshold = 0.40
    policy.algo_config.min_risk_reward_ratio = 1.0
    policy.min_allowed_rr = 1.0

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.1, volume=1.0
    )

    # Feature vector that satisfies ALL God Mode conditions:
    # 1. Confirmed BOS or CHOCH
    # 2. Valid Order Block
    # 3. Zone Quality >= threshold
    # 4. Liquidity sweep detected
    # 5. Imbalance/FVG supports entry
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=0.5,
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
        fvg_bullish_active=True,  # imbalance FVG supports entry
        fvg_bearish_active=False,
        order_block_type=1,  # valid OB
        liquidity_sweep_signal=1,  # liquidity sweep detected
        choch_bullish=True,  # confirmed CHOCH
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
        htf_h4_trend=-1.0,  # Opposing HTF filter! Normally rejects standard trades
        htf_h1_momentum=-1.0,
        htf_m30_structure=-1.0,
        htf_m15_confirmation=-1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=-1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
        feat_ob_valid_bos=1.0,  # valid BOS
        feat_ob_equilibrium_ratio=0.6,
        feat_ob_liquidity_swept=1.0,
        feat_ob_fib_50_60_alignment=1.0,
    )

    # Trigger signal policy evaluation (high buy prob to clear threshold)
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )

    # Opposing HTF was bypassed!
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.execution_mode == "SMC_GOD_MODE"
    assert proposal.override_reason == "HTF_BYPASSED"


def test_predictive_limit_orders():
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.ai_zone_confidence_threshold = 0.10

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.1, volume=1.0
    )

    # Feature vector with valid OB, but not meeting complete God Mode (e.g. no liquidity sweep)
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        live_tick_displacement=0.5,
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
        order_block_type=1,  # Valid OB
        liquidity_sweep_signal=0,  # No liquidity sweep
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
        feat_ob_valid_bos=1.0,
        feat_ob_equilibrium_ratio=0.6,
        feat_ob_liquidity_swept=1.0,
        feat_ob_fib_50_60_alignment=1.0,
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )

    # Predictive limit order is triggered
    assert proposal.action == ActionType.BUY_LIMIT
    assert proposal.execution_mode == "PREDICTIVE_LIMIT"
    assert "PREDICTIVE_OB" in proposal.reason_code


def test_tick_sweep_execution():
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.ai_zone_confidence_threshold = 0.10

    # Set historical tick to simulate a fast swing
    policy._last_tick_bid = 2005.00
    policy._last_tick_ask = 2005.20

    current_tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.1, volume=1.0
    )

    # Feature vector showing liquidity sweep and quick reversal
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=current_tick.timestamp.isoformat(),
        live_tick_displacement=-5.0,
        log_return_m1=-0.01,
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
        liquidity_sweep_signal=1,  # sweep detected
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

    # Mock regime state with velocity and positive OFI
    from nexus_scalp.features.regime_classifier import (
        MarketRegimeState,
        RecommendedExecutionType,
        RegimeReason,
        RegimeType,
    )

    regime = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=current_tick.timestamp.isoformat(),
        regime_type=RegimeType.VOLATILITY_EXPANSION,
        regime_probability=0.85,
        order_flow_imbalance=0.60,
        realized_volatility_5m=0.002,
        tick_velocity_per_sec=12.0,  # Tick velocity high
        current_spread_usd=0.10,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.IOC_MARKET,
        reason=RegimeReason.RV_SPIKE,
    )

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=current_tick,
        feature_vector=fv,
        regime_state=regime,
    )

    # Tick-level sweep execution triggers immediately
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.execution_mode == "TICK_SWEEP"
    assert "TICK_LEVEL_LIQUIDITY_SWEEP" in proposal.reason_code


def test_pending_order_manager_and_falling_knife():
    from datetime import timedelta

    adapter = DummyMT5Port()
    audit_repo = AuditRepository(db_url="sqlite:///:memory:")
    manager = OrderLifecycleManager(adapter=adapter, audit_repo=audit_repo)

    # Add a pending BUY_LIMIT order
    setup_time = datetime.now(UTC)
    adapter.pending_orders.append(
        DummyPendingOrder(
            ticket=5001,
            symbol="XAUUSD",
            price_open=2000.0,
            order_type=OrderType.BUY_LIMIT,
            volume=0.1,
            sl=1995.0,
            tp=2010.0,
        )
    )

    current_tick = TickData(
        symbol="XAUUSD", timestamp=setup_time, bid=2010.0, ask=2010.1, volume=1.0
    )
    # Set the order setup time to be older than the 30-second lock (Requirement 5)
    manager._pending_orders_setup_time[5001] = setup_time - timedelta(seconds=40)

    # Evaluate every tick: distance is too far ($10.0 > ATR * 1.2) -> should be cancelled
    manager.manage_pending_orders(
        symbol="XAUUSD", current_tick=current_tick, atr=1.5, max_pending_dist_atr_mult=1.2
    )

    assert 5001 in adapter.cancelled_tickets

    # Reset
    adapter.cancelled_tickets = []
    adapter.pending_orders = [
        DummyPendingOrder(
            ticket=5002,
            symbol="XAUUSD",
            price_open=2000.0,
            order_type=OrderType.BUY_LIMIT,
            volume=0.1,
            sl=1995.0,
            tp=2010.0,
        )
    ]

    # Falling Knife Protection: We have an active SELL position with strong unrealized profit
    pos = Position(
        ticket=1001,
        symbol="XAUUSD",
        type=OrderType.SELL,
        volume=0.5,
        price_open=2040.0,  # Opened at 2040.0
        sl=2050.0,
        tp=2000.0,
        profit=500.0,  # Large floating profit ($40.0 move down)
        magic=888101,
    )

    manager.evaluate_falling_knife_protection(
        symbol="XAUUSD",
        current_tick=current_tick,  # Ask is 2010.1
        positions=[pos],
        atr=1.5,
    )

    # Opposite BUY_LIMIT must be cancelled by falling knife protection
    assert 5002 in adapter.cancelled_tickets


def test_risk_sizing_exposure_and_portfolio_context():
    config = RiskConfig(
        max_account_drawdown_pct=2.5,
        risk_per_trade_pct=0.5,
        max_concurrent_positions=2,
        max_spread_points=20,
        enforce_stop_loss=True,
    )
    risk_engine = RiskEngine(config=config, max_allowed_lots=5.0)

    proposal = TradeProposal(
        request_id="test_req",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_LIMIT,
        confidence=0.88,
        proposed_entry=2000.0,
        stop_loss=1995.0,
        take_profit=2010.0,
        risk_reward_ratio=2.0,
        reason_code="TEST",
    )

    account = AccountInfo(
        login=12345,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
    )

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
        stops_level=5,
        freeze_level=5,
        trade_contract_size=100.0,
    )

    # Portfolio Context: opposite SELL exposure is present -> rejects new opposite BUY_LIMIT
    active_positions = [
        Position(
            ticket=2001,
            symbol="XAUUSD",
            type=OrderType.SELL,
            volume=0.5,
            price_open=2010.0,
            sl=2020.0,
            tp=1990.0,
            profit=0.0,
            magic=888101,
        )
    ]

    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.1, volume=1.0
    )

    order = risk_engine.evaluate_proposal(
        proposal=proposal,
        account=account,
        symbol_info=symbol_info,
        active_positions=active_positions,
        current_tick=tick,
        atr=1.5,
    )

    assert order is None  # Blocked by opposing exposure!
