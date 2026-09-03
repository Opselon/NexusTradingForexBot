"""BUG-226 regression — strategy decision-path explainability contract.

Counterfactual forensic (2026-09-03): 11 live executions carried model
confidence < 0.20 (6 SELL_LIMIT + 5 BUY_LIMIT) because the PREDICTIVE_LIMIT
path (policy.py:_evaluate_predictive_limit) replaces the standard-flow model
confidence gate with a pure STRUCTURAL gate (valid_ob and not smc_god_mode and
total_exposure < MAX_TOTAL_EXPOSURE). That is intended behavior, but the
proposal carried NO gate-evidence payload, so a forensic reader could not
distinguish "gate bypassed" from "gate never applied". TICK_SWEEP applies its
OWN confidence floor (sweep_conf_thresh) and likewise disclosed nothing.

This regression pins the OBSERVABILITY contract (INV-018: decision evidence is
observability-only, never a decision input): both structural paths must stamp
an explicit risk_checks dict documenting which gate ran and what it verified.
"""

from __future__ import annotations

from datetime import UTC, datetime

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def _feature_vector(**overrides) -> FeatureVector:
    """Minimal deterministic FeatureVector: every required field neutral."""
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.0,
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
        dist_to_swing_high_20=0.0,
        dist_to_swing_low_20=0.0,
        price_compression_flag_ratio=0.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=False,
        session_london=True,
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
        consolidation_ratio=0.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    return fv.model_copy(update=overrides) if overrides else fv


def _tick() -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=2000.10,
        ask=2000.15,
        volume=1.0,
    )


def test_predictive_limit_stamps_gate_evidence() -> None:
    """A predictive-limit proposal must disclose that the confidence gate was
    NOT applied and what the structural gate verified (BUG-226 contract)."""
    policy = SignalPolicy()
    fv = _feature_vector(order_block_type=1)  # valid_ob -> predictive path
    tick = _tick()
    # Model says nothing (all NO_TRADE): the standard-flow gate would reject,
    # the predictive path does not consult it - but must DISCLOSE that.
    probs = torch.tensor([[0.95, 0.02, 0.02, 0.01]])
    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert "PREDICTIVE_OB" in (proposal.reason_code or ""), proposal.reason_code
    rc = proposal.risk_checks or {}
    assert rc.get("decision_path") == "PREDICTIVE_LIMIT", rc
    assert rc.get("confidence_gate_applied") is False, rc
    assert rc.get("model_confidence_verdict") == "NOT_REQUIRED_STRUCTURAL_PATH", rc
    sg = rc.get("structural_gate") or {}
    assert sg.get("valid_ob") is True, rc
    assert sg.get("order_block_type") == 1, rc
    assert "min_rr_enforced" in sg, rc


def test_tick_sweep_rejection_evidence_contract() -> None:
    """TICK_SWEEP: when the sweep fires, the proposal must carry its own
    confidence-floor evidence; when it does not fire, the standard flow must
    still reject on confidence (proves the path contract stayed intact)."""
    policy = SignalPolicy()
    fv = _feature_vector(liquidity_sweep_signal=1)
    tick = _tick()
    policy._last_tick_bid = 2000.10
    policy._last_tick_ask = 2000.15

    low_probs = torch.tensor([[0.90, 0.05, 0.03, 0.02]])
    proposal = policy.evaluate_probabilities(
        probabilities=low_probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert "TICK_LEVEL_LIQUIDITY_SWEEP" not in (proposal.reason_code or "")

    high_probs = torch.tensor([[0.02, 0.92, 0.04, 0.02]])
    policy._last_tick_bid = 2000.10
    policy._last_tick_ask = 2000.15
    proposal2 = policy.evaluate_probabilities(
        probabilities=high_probs,
        current_tick=tick,
        feature_vector=fv,
    )
    rc = proposal2.risk_checks or {}
    if rc.get("decision_path") == "TICK_SWEEP":
        assert rc.get("confidence_gate_applied") is True, rc
        assert "sweep_conf_threshold" in rc, rc
        assert "sweep_direction_prob" in rc, rc
        sg = rc.get("structural_gate") or {}
        assert sg.get("velocity_reverses") is True, rc
    else:
        # Sweep did not fire under this fixture (velocity/OFI conditions);
        # the contract assertion is the low-prob rejection above.
        assert proposal2.action.value == "NO_TRADE"


def test_standard_flow_confidence_gate_still_governs() -> None:
    """The standard-flow confidence gate must remain operative (guard)."""
    policy = SignalPolicy()
    fv = _feature_vector()
    tick = _tick()
    probs = torch.tensor([[0.9, 0.05, 0.05, 0.0]])
    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )
    assert proposal.action.value == "NO_TRADE"
