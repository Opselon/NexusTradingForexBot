"""
Unit tests specifically targeting the build_nt function in SignalPolicy (policy.py:841).
Verifies active confidence resolution, min_rr adjustment for high confidence threshold,
preservation of candidate vs default non-candidate geometry, risk checks evidence payload,
survival mode & range market adjustments, and blocked_by filter propagation.
"""

from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def _make_tick(
    bid: float = 2000.0, ask: float = 2000.2, symbol: str = "XAUUSD", ts_offset_ms: int = 0
) -> TickData:
    ts = datetime.now(UTC) + timedelta(milliseconds=ts_offset_ms)
    return TickData(
        symbol=symbol,
        timestamp=ts,
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def _make_feature_vector(
    symbol: str = "XAUUSD",
    atr_m1: float = 2.0,
    is_above_kumo: bool = True,
    is_below_kumo: bool = False,
    tenkan_sen: float = 2000.0,
    kijun_sen: float = 2000.0,
    htf_h4_trend: float = 1.0,
    htf_h1_momentum: float = 1.0,
    htf_m30_structure: float = 1.0,
    htf_m15_confirmation: float = 1.0,
    support_zone_dist: float = 5.0,
    resistance_zone_dist: float = 5.0,
    trend_strength: float = 1.0,
    live_tick_displacement: float = 0.5,
) -> FeatureVector:
    return FeatureVector(
        symbol=symbol,
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=live_tick_displacement,
        log_return_m1=0.0,
        atr_m1=atr_m1,
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
        tenkan_sen=tenkan_sen,
        kijun_sen=kijun_sen,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=is_above_kumo,
        is_below_kumo=is_below_kumo,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=htf_h4_trend,
        htf_h1_momentum=htf_h1_momentum,
        htf_m30_structure=htf_m30_structure,
        htf_m15_confirmation=htf_m15_confirmation,
        support_zone_dist=support_zone_dist,
        resistance_zone_dist=resistance_zone_dist,
        trend_strength=trend_strength,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )


def test_build_nt_fallback_to_cand_confidence_when_confidence_is_zero():
    """Verify that when confidence is 0 and proposed_action is ActionType.NO_TRADE,
    build_nt sets active_conf = cand_confidence."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40

    # HTF unaligned => triggers HTF_TREND_FILTER rejection via build_nt
    fv = _make_feature_vector(htf_h4_trend=-1.0, htf_h1_momentum=-1.0, trend_strength=-0.5)
    tick = _make_tick()

    # Model gives BUY candidate (prob_buy = 0.80)
    probs = torch.tensor([[0.10, 0.80, 0.10, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "HTF_TREND_CONFL_FAIL"
    assert proposal.decision_stage == "HTF_TREND_FILTER"

    # cand_confidence was computed as 0.80 / (0.10 + 0.80 + 0.10) = 0.80
    assert abs(proposal.confidence - 0.80) < 1e-4
    assert abs(proposal.smc_score - 0.80) < 1e-4
    assert abs(proposal.confidence_after_filters - 0.80) < 1e-4


def test_build_nt_uses_confidence_when_positive():
    """Verify that when confidence > 0, active_conf uses confidence instead of cand_confidence."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.50

    # fv is valid, but confidence (0.35) is below effective threshold (0.50)
    fv = _make_feature_vector()
    tick = _make_tick()

    # prob_buy = 0.35, prob_no_trade = 0.60, prob_sell = 0.05 -> cand_confidence = 0.35
    probs = torch.tensor([[0.60, 0.35, 0.05, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "CONFIDENCE_FAIL"
    assert abs(proposal.confidence - 0.35) < 1e-4


def test_build_nt_high_confidence_threshold_adjusts_min_rr():
    """Verify that act_rr in risk_checks switches to min_rr_high_confidence when active_conf >= high_confidence_threshold."""
    algo_cfg = AlgoConfig(
        high_confidence_threshold=0.70,
        min_risk_reward_ratio=1.8,
        min_rr_high_confidence=1.2,
    )

    # 1. Candidate with high confidence (cand_confidence = 0.75 >= 0.70) rejected by SR margin filter
    policy1 = SignalPolicy(algo_config=algo_cfg)
    fv_sr_fail = _make_feature_vector(resistance_zone_dist=0.10)  # resistance dist < 0.25 => fails
    tick1 = _make_tick(bid=2000.0, ask=2000.2, ts_offset_ms=0)
    high_conf_probs = torch.tensor([[0.20, 0.75, 0.05, 0.0]])

    proposal_high_conf = policy1.evaluate_probabilities(
        probabilities=high_conf_probs,
        current_tick=tick1,
        feature_vector=fv_sr_fail,
    )

    assert proposal_high_conf.action == ActionType.NO_TRADE
    assert proposal_high_conf.blocked_by == "SR_RESISTANCE_MARGIN_FAIL"
    assert proposal_high_conf.risk_checks["min_rr"] == 1.2

    # 2. Candidate with low confidence (cand_confidence = 0.50 < 0.70) rejected by SR margin filter
    policy2 = SignalPolicy(algo_config=algo_cfg)
    tick2 = _make_tick(bid=2001.0, ask=2001.2, ts_offset_ms=200)
    low_conf_probs = torch.tensor([[0.45, 0.50, 0.05, 0.0]])

    proposal_low_conf = policy2.evaluate_probabilities(
        probabilities=low_conf_probs,
        current_tick=tick2,
        feature_vector=fv_sr_fail,
    )

    assert proposal_low_conf.action == ActionType.NO_TRADE
    assert proposal_low_conf.blocked_by == "SR_RESISTANCE_MARGIN_FAIL"
    assert proposal_low_conf.risk_checks["min_rr"] == 1.8


def test_build_nt_preserves_candidate_geometry_for_blocked_candidate():
    """Verify that when a directional candidate is formed but blocked by a filter,
    build_nt preserves target_entry_price, cand_stop_loss, cand_take_profit, cand_actual_rr, and model_action."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.30

    # Resistance margin filter will block the candidate
    fv = _make_feature_vector(resistance_zone_dist=0.10, atr_m1=2.0)
    tick = _make_tick(bid=2000.0, ask=2000.2)

    probs = torch.tensor([[0.10, 0.85, 0.05, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert proposal.model_action == "BUY_MARKET"
    # For a BUY candidate, proposed_entry should be tick.ask (2000.2)
    assert proposal.proposed_entry == 2000.2
    # cand_stop_loss and cand_take_profit are computed based on features and entry price
    assert proposal.stop_loss < proposal.proposed_entry
    assert proposal.take_profit > proposal.proposed_entry
    assert proposal.risk_reward_ratio > 0.0


def test_build_nt_non_candidate_uses_default_geometry():
    """Verify that when no candidate is formed (cand_action == 'NO_TRADE'),
    build_nt outputs default tick.bid entry, SL at 0.99*bid, TP at 1.01*bid, RR=1.0, model_action='NO_TRADE'."""
    policy = SignalPolicy()

    # Neutral features, no ICT/stat-arb/Ichi signals -> cand_action == "NO_TRADE"
    fv = _make_feature_vector(
        is_above_kumo=False,
        is_below_kumo=False,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        live_tick_displacement=0.01,
    )
    tick = _make_tick(bid=2000.0, ask=2000.2)

    # Pure NO_TRADE probabilities
    probs = torch.tensor([[0.90, 0.05, 0.05, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
    )

    assert proposal.action == ActionType.NO_TRADE
    assert proposal.model_action == "NO_TRADE"
    assert proposal.proposed_entry == 2000.0
    assert proposal.stop_loss == 2000.0 * 0.99
    assert proposal.take_profit == 2000.0 * 1.01
    assert proposal.risk_reward_ratio == 1.0


def test_build_nt_risk_checks_payload_and_survival_mode_and_range_penalty():
    """Verify build_nt populates all fields in risk_checks dict including survival mode and range penalty adjustments."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    policy.range_confidence_penalty = 0.15

    # Inside kumo => is_range_market = True => range_penalty = 0.15
    fv = _make_feature_vector(
        is_above_kumo=False,
        is_below_kumo=False,
        live_tick_displacement=0.01,
    )
    tick = _make_tick(bid=2000.0, ask=2000.2)

    probs = torch.tensor([[0.30, 0.35, 0.35, 0.0]])

    proposal = policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=tick,
        feature_vector=fv,
        survival_mode=True,  # adds +0.10 adjustment
    )

    rc = proposal.risk_checks
    assert rc is not None
    assert rc["base_threshold"] == 0.40
    assert rc["range_penalty"] == 0.15
    assert rc["survival_mode_adjustment"] == 0.10
    # BUG-312 (2026-09-22): the range penalty is scaled against the base
    # (0.40 + 0.15*0.40 + 0.10 = 0.56) rather than added raw (0.40 + 0.15 +
    # 0.10 = 0.65), so the effective threshold stays inside the serving head's
    # achievable probability range.
    assert rc["effective_threshold"] == 0.56
    assert rc["spread_usd"] == 0.20
    assert "spread_atr_ratio" in rc
    assert "max_spread_atr_ratio" in rc
    assert rc["expected_symbol"] == "XAUUSD"
    assert rc["expected_magic"] == 888101


def test_build_nt_propagation_across_rejection_gates():
    """Verify build_nt sets blocked_by and rejection_reason across different policy rejection filters."""
    # 1. HTF trend filter rejection for SELL
    policy1 = SignalPolicy()
    policy1.confidence_threshold = 0.10

    fv_sell_htf_fail = _make_feature_vector(
        is_above_kumo=False,
        is_below_kumo=True,
        tenkan_sen=1999.0,
        kijun_sen=2000.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        trend_strength=1.0,
        live_tick_displacement=-0.5,
    )
    tick1 = _make_tick(bid=2000.0, ask=2000.2, ts_offset_ms=0)
    sell_probs = torch.tensor([[0.10, 0.05, 0.85, 0.0]])

    proposal1 = policy1.evaluate_probabilities(
        probabilities=sell_probs,
        current_tick=tick1,
        feature_vector=fv_sell_htf_fail,
    )
    assert proposal1.action == ActionType.NO_TRADE
    assert proposal1.blocked_by == "HTF_TREND_CONFL_FAIL"
    assert proposal1.reason_code == "SELL_REJECTED_HTF_TREND_CONFL_FAIL"
    assert proposal1.rejection_reason == "SELL_REJECTED_HTF_TREND_CONFL_FAIL"

    # 2. SR support margin filter rejection for SELL
    policy2 = SignalPolicy()
    policy2.confidence_threshold = 0.10

    fv_sell_sr_fail = _make_feature_vector(
        is_above_kumo=False,
        is_below_kumo=True,
        tenkan_sen=1999.0,
        kijun_sen=2000.0,
        htf_h4_trend=-1.0,
        htf_h1_momentum=-0.5,
        trend_strength=-0.5,
        support_zone_dist=0.10,  # < 0.25 margin requirement
        live_tick_displacement=-0.5,
    )
    tick2 = _make_tick(bid=2001.0, ask=2001.2, ts_offset_ms=200)

    proposal2 = policy2.evaluate_probabilities(
        probabilities=sell_probs,
        current_tick=tick2,
        feature_vector=fv_sell_sr_fail,
    )
    assert proposal2.action == ActionType.NO_TRADE
    assert proposal2.blocked_by == "SR_SUPPORT_MARGIN_FAIL"
    assert proposal2.reason_code == "SELL_REJECTED_SR_SUPPORT_MARGIN_FAIL"

    # 3. Spread ATR ratio gate rejection
    policy_tight_spread = SignalPolicy(
        max_spread_atr_ratio=0.05,
        algo_config=AlgoConfig(min_risk_reward_ratio=1.0),
    )
    tick_wide_spread = _make_tick(bid=2000.0, ask=2000.50, ts_offset_ms=400)
    fv_pass = _make_feature_vector()
    high_probs = torch.tensor([[0.05, 0.90, 0.05, 0.0]])

    proposal3 = policy_tight_spread.evaluate_probabilities(
        probabilities=high_probs,
        current_tick=tick_wide_spread,
        feature_vector=fv_pass,
    )
    assert proposal3.action == ActionType.NO_TRADE
    assert proposal3.blocked_by == "SPREAD_ATR_RATIO"
    assert "SPREAD_ATR_RATIO_EXCEEDED" in proposal3.reason_code
