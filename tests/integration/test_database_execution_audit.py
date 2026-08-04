import os
import sqlite3
import json
import uuid
import time
from datetime import datetime, UTC
import pytest
import torch

from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TickData, TradeProposal, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.adapters.database.audit_repository import AuditRepository


def test_database_execution_audit_pipeline():
    """
    Integration test verifying the complete decision and execution pipeline logging.
    Ensures that original model decisions, risk filter decisions, and final execution decisions
    are stored transparently in the SQLite database artifacts/test_audit.db.
    """
    db_path = "artifacts/test_audit.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    # 1. Initialize AuditRepository with test SQLite database
    audit_repo = AuditRepository(db_url=f"sqlite:///{db_path}")

    # 2. Build mock market tick and features to trigger a standard BUY signal candidate
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.10, volume=1.0)
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

    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10

    # Probabilities: highest class is BUY_MARKET at index 1
    probabilities = torch.tensor([[0.01, 0.98, 0.01, 0.0]])

    # Evaluate probabilities and get approved proposal
    proposal_approved = policy.evaluate_probabilities(
        probabilities=probabilities,
        current_tick=tick,
        feature_vector=fv,
    )

    # Log signal to database
    audit_repo.log_signal(proposal_approved)

    # Log successful execution to database
    order = TradeOrder(
        order_id=proposal_approved.request_id,
        symbol=proposal_approved.symbol,
        order_type=OrderType.BUY,
        volume=1.0,
        price=proposal_approved.proposed_entry,
        stop_loss=proposal_approved.stop_loss,
        take_profit=proposal_approved.take_profit,
        magic_number=888101,
        comment="NSE_TEST",
    )
    audit_repo.log_execution(order, "FILLED")

    # 3. Simulate a Rejected Signal Scenario (due to high spread)
    tick_rejected = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2005.0, volume=1.0) # High spread
    proposal_rejected = policy.evaluate_probabilities(
        probabilities=probabilities,
        current_tick=tick_rejected,
        feature_vector=fv,
    )

    # Log rejected signal to database
    audit_repo.log_signal(proposal_rejected)

    # Flush the background insertion queue synchronously before querying
    audit_repo.close()

    # 4. Query and Verify with SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query 1: Retrieve actions count
    cursor.execute("SELECT action, COUNT(*) FROM audit_signals GROUP BY action;")
    actions_count = cursor.fetchall()
    print("audit_signals action counts:", actions_count)

    # Query 2: Signal actions where we have buy, sell, limit orders
    cursor.execute("""
        SELECT COUNT(*)
        FROM audit_signals
        WHERE action IN (
            'BUY_LIMIT',
            'SELL_LIMIT',
            'BUY',
            'SELL',
            'BUY_MARKET',
            'SELL_MARKET'
        );
    """)
    candidate_signals_count = cursor.fetchone()[0]
    assert candidate_signals_count > 0, "At least one candidate signal must exist in audit_signals database."

    # Query 3: Verify executions
    cursor.execute("SELECT COUNT(*) FROM audit_executions;")
    executions_count = cursor.fetchone()[0]
    assert executions_count > 0, "Successful executions must be stored in audit_executions."

    # Query 4: Check if payloads contain the newly added Task 3 diagnostic fields
    cursor.execute("SELECT payload FROM audit_signals WHERE action != 'NO_TRADE' LIMIT 1;")
    approved_payload_raw = cursor.fetchone()[0]
    approved_payload = json.loads(approved_payload_raw)

    required_fields = [
        "model_action",
        "buy_probability",
        "sell_probability",
        "no_trade_probability",
        "regime",
        "regime_confidence",
        "risk_allowed",
        "guardian_status",
        "rejection_reason",
        "final_action"
    ]
    for field in required_fields:
        assert field in approved_payload, f"Diagnostic field '{field}' is missing from transparent signal payload."

    # Query 5: Check rejected payload contains rejection reason
    cursor.execute("SELECT payload FROM audit_signals WHERE action = 'NO_TRADE' LIMIT 1;")
    rejected_payload_raw = cursor.fetchone()[0]
    rejected_payload = json.loads(rejected_payload_raw)

    assert rejected_payload["risk_allowed"] is False, "Rejected signal should have risk_allowed=False"
    assert "rejection_reason" in rejected_payload and rejected_payload["rejection_reason"] is not None, \
        "Rejected signal must have a populated rejection_reason."

    conn.close()
    print("Integration Verification Test Passed Successfully!")
