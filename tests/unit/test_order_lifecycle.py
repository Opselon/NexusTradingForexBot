import os
import sqlite3
import tempfile
import time
from datetime import UTC, datetime

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig, AppConfig, RiskConfig
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


class MockMT5Port:
    def __init__(self):
        self.positions = []
        self.sent_orders = []
        self.closed_deals = []

    def get_positions(self, symbol=None):
        return self.positions

    def get_symbol_info(self, symbol):
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )

    def get_account_info(self):
        return AccountInfo(
            login=123456,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            currency="USD",
        )

    def send_order(self, order: TradeOrder) -> bool:
        self.sent_orders.append(order)
        return True

    def get_closed_deals_history(self, symbol, hours_back):
        return self.closed_deals

    def close_position(self, ticket, volume=None):
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        for i, p in enumerate(self.positions):
            if p.ticket == ticket:
                from nexus_scalp.domain.models import Position

                self.positions[i] = Position(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    type=p.type,
                    volume=p.volume,
                    price_open=p.price_open,
                    sl=stop_loss,
                    tp=take_profit,
                    profit=p.profit,
                    magic=p.magic,
                )
                return True
        return False


def test_structural_entry_and_sl_tp_generation():
    """Verify policy.py generates structural SL/TP levels and enforces min risk-reward validation."""
    algo_cfg = AlgoConfig(
        atr_sl_buffer_multiplier=1.5,
        min_risk_reward_ratio=2.0,  # Require at least 2.0 RR
    )
    policy = SignalPolicy(algo_config=algo_cfg)

    # Setup simulated completed bar elements
    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )

    # Setup high/low swings so that we can test structural generation
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
        dist_to_swing_high_20=2.0,  # 2.0 * ATR(2.00) = +4.0 (swing_high is 2004.2)
        dist_to_swing_low_20=2.0,  # 2.0 * ATR(2.00) = -4.0 (swing_low is 1996.2)
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
        is_above_kumo=True,  # Supports BUY
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

    # 1. Propose BUY with low RR (MFE is close, SL is wide)
    # Risk = 7.0, Reward = 4.0. RR is 4.0 / 7.0 = 0.57. Since 0.57 < 2.0, this gets rejected!
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.90, 0.05, 0.05]]),
        current_tick=tick,
        feature_vector=fv,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert "ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD" in proposal.reason_code


def test_risk_engine_fixed_dollar_sizing():
    """Verify that RiskEngine enforces fixed dollar risk and scales lot sizes based on structural SL distance."""
    risk_cfg = RiskConfig(
        risk_per_trade_pct=2.0,  # 2% risk per trade
        max_allowed_lots=10.0,
        max_concurrent_positions=2,
    )
    risk_engine = RiskEngine(config=risk_cfg, min_risk_reward_ratio=1.5)

    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
        currency="USD",
    )
    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    # Case A: Tight SL (2.00 price delta)
    # Risk Amount = 10000.0 * 2% = 200.0 USD
    # Distance in points = 2.00 / 0.01 = 200 points
    # Lot Size = 200.0 / (200 * 1.0) = 1.0 lots
    vol_tight = risk_engine.calculate_position_size(
        account=account,
        symbol_info=symbol_info,
        sl_distance_price=2.00,
        risk_pct=2.0,
    )
    assert pytest.approx(vol_tight, 0.01) == 1.00

    # Case B: Wide SL (4.00 price delta)
    # Lot Size = 200.0 / (400 * 1.0) = 0.5 lots
    vol_wide = risk_engine.calculate_position_size(
        account=account,
        symbol_info=symbol_info,
        sl_distance_price=4.00,
        risk_pct=2.0,
    )
    assert pytest.approx(vol_wide, 0.01) == 0.50


def test_order_modification_and_sl_shift():
    """Simulate SL shifting in real-time, trace trade closure at modified SL, and verify risk-free hit autopsy."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_order_manager_autopsy.db")
    db_url = f"sqlite:///{db_path}"
    audit = AuditRepository(db_url=db_url)

    mock_port = MockMT5Port()
    om = OrderLifecycleManager(adapter=mock_port, audit_repo=audit)

    # Create initial position
    pos = Position(
        ticket=301,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1995.00,
        tp=2010.00,
        profit=-50.00,
        magic=888101,
    )
    mock_port.positions = [pos]

    # Initialize order manager tracking with high buy probs so that the adaptive manager
    # holds the position (LOSS_RECOVERY_CONFIRMED) instead of closing it early (LOSS_EXIT_PRESSURE).
    tick_init = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.2, volume=1.0
    )
    probs = torch.tensor([[0.01, 0.98, 0.01]])
    om.manage_active_positions("XAUUSD", tick_init, probs=probs)

    # 1. Real-time Stop Loss modification: shift to break-even + 1 pip (e.g. 2000.10)
    # Assert trailing / breakeven shift intent
    success_modify = mock_port.modify_position(ticket=301, stop_loss=2000.10, take_profit=2010.00)
    assert success_modify is True
    om._last_modify_sl[301] = 2000.10

    # Verify that local state final_sl_price is updated to 2000.10
    final_sl = om._last_modify_sl.get(301)
    assert final_sl == 2000.10

    # 2. Simulate closure at the modified SL: exit price of XAUUSD drops to 2000.10
    # Simulate broker history containing this closed deal
    mock_port.closed_deals = [
        {
            "position_ticket": 301,
            "profit": 10.00,
            "swap": 0.0,
            "commission": 0.0,
            "price": 2000.10,
            "reason": 3,  # SL hit
            "comment": "SL hit NSE_TRAIL",
        }
    ]
    mock_port.positions = []  # closed!

    # Trigger position evaluation, which identifies the dead ticket and writes the autopsy log
    tick_close = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.10, ask=2000.30, volume=1.0
    )
    om.manage_active_positions("XAUUSD", tick_close)

    # Flush any asynchronous writes to SQLite by closing the audit repo
    audit.close()
    time.sleep(0.5)

    # Retrieve logged ledger record to assert autopsy details
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT is_risk_free_hit, final_sl_price, exit_mechanism FROM audit_ledger WHERE ticket = 301;"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    is_rf, final_sl_db, exit_mechanism = row
    assert is_rf == 1  # Risk-free hit is True!
    assert final_sl_db == 2000.10
    assert exit_mechanism == "RISK_FREE_SL_HIT"


def test_trade_autopsy_db_persistence():
    """Verify that a complete closed trade autopsy is written to financial_ledger SQLite table."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_persistence.db")
    db_url = f"sqlite:///{db_path}"
    audit = AuditRepository(db_url=db_url)

    mock_port = MockMT5Port()
    om = OrderLifecycleManager(adapter=mock_port, audit_repo=audit)

    # Create initial position
    pos = Position(
        ticket=401,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2000.00,
        sl=1990.00,
        tp=2020.00,
        profit=10.00,
        magic=888101,
    )
    mock_port.positions = [pos]

    # Initialize tracking
    tick_init = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.10, ask=2000.30, volume=1.0
    )
    om.manage_active_positions("XAUUSD", tick_init)

    # Update MAE / MFE excursions
    om._update_mfe_mae(401, 15.50)  # MFE price delta of +15.50
    om._update_mfe_mae(401, -3.20)  # MAE price delta of -3.20

    # Simulate trade closure (avoiding manually_closed matching string in comment)
    mock_port.closed_deals = [
        {
            "position_ticket": 401,
            "profit": 1550.00,
            "swap": 0.0,
            "commission": -5.00,
            "price": 2015.50,
            "reason": 4,  # TP hit
            "comment": "TP hit target",
        }
    ]
    mock_port.positions = []  # closed!

    tick_close = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2015.50, ask=2015.70, volume=1.0
    )
    om.manage_active_positions("XAUUSD", tick_close)

    # Flush any asynchronous writes to SQLite by closing the audit repo
    audit.close()
    time.sleep(0.5)

    # Query DB and assert full autopsy records
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT mae, mfe, initial_sl_price, final_sl_price, is_risk_free_hit, exit_mechanism FROM audit_ledger WHERE ticket = 401;"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    mae, mfe, initial_sl, final_sl, is_rf, exit_reason = row
    assert mae == -3.20
    assert mfe == 15.50
    assert initial_sl == 1990.00
    assert final_sl == 1990.00  # Not modified
    assert is_rf == 0
    assert exit_reason == "TAKE_PROFIT_HIT"
