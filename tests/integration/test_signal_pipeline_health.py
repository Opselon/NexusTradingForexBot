import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def test_signal_pipeline_health_integration():
    """
    Integration test verifying signal generation, risk transparency,
    and database integrity checks using SQLite queries.
    """
    db_path = "artifacts/test_pipeline_health.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass

    # Initialize AuditRepository
    audit_repo = AuditRepository(db_url=f"sqlite:///{db_path}")

    # Build policy with calibrated low thresholds
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 1.2
    policy.algo_config.ai_zone_confidence_threshold = 0.60

    # Mock market tick and features
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
        fvg_bullish_active=True,  # Zone is active
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

    # 1. Evaluate probabilities leading to a valid BUY_LIMIT candidate
    probabilities = torch.tensor([[0.01, 0.98, 0.01, 0.0]])
    proposal = policy.evaluate_probabilities(
        probabilities=probabilities,
        current_tick=tick,
        feature_vector=fv,
    )

    # Log to audit database
    audit_repo.log_signal(proposal)

    # 2. Evaluate probabilities leading to a rejected candidate (confidence below threshold)
    policy_rejected = SignalPolicy()
    policy_rejected.confidence_threshold = 0.99  # extremely high, will reject
    policy_rejected.algo_config.ai_zone_confidence_threshold = 0.99
    proposal_rejected = policy_rejected.evaluate_probabilities(
        probabilities=probabilities,
        current_tick=tick,
        feature_vector=fv,
    )

    # Log rejected signal
    audit_repo.log_signal(proposal_rejected)

    # Safely close repository and flush background worker queues
    audit_repo.close()

    # Query and verify database integrity
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query 1: Verify at least one BUY_LIMIT or SELL_LIMIT candidate exists under model_action
    cursor.execute("""
        SELECT COUNT(*)
        FROM audit_signals
        WHERE json_extract(payload, '$.model_action') IN ('BUY_LIMIT', 'SELL_LIMIT');
    """)
    candidate_count = cursor.fetchone()[0]
    print(f"Verified {candidate_count} BUY_LIMIT / SELL_LIMIT candidate signals.")
    assert candidate_count > 0, "No BUY_LIMIT or SELL_LIMIT candidate signals found in database."

    # Query 2: Verify total count of logged signals is at least 2
    cursor.execute("SELECT COUNT(*) FROM audit_signals;")
    total_signals = cursor.fetchone()[0]
    print(f"Total logged signals: {total_signals}")
    assert total_signals >= 2, "Signals were not correctly audited in SQLite database."

    # Query 3: Check risk transparency for rejected signals
    cursor.execute("""
        SELECT payload
        FROM audit_signals
        WHERE action = 'NO_TRADE'
        LIMIT 1;
    """)
    rejected_payload_raw = cursor.fetchone()[0]
    rejected_payload = json.loads(rejected_payload_raw)

    assert (
        "rejection_reason" in rejected_payload and rejected_payload["rejection_reason"] is not None
    ), "Rejected signal must contain rejection_reason."
    assert "risk_checks" in rejected_payload, "Payload must contain risk_checks."
    assert rejected_payload["risk_checks"]["zone_quality"] is not None, (
        "risk_checks must contain zone_quality."
    )

    conn.close()
    print("All signal pipeline health integration verifications completed successfully!")
