"""BUG-229 regression — default NO_TRADE reason codes must not masquerade as
regime events (TASK-TDF Q3 misattribution fix).

Census finding (TDF Q3): the DEFAULT no-candidate branch in
``SignalPolicy.evaluate_probabilities`` (policy.py:783-788) labels a routine
"no rule fired" NO_TRADE as ``REGIME_<regime_type>`` /
``RANGE_BOUND_SIDEWAYS`` / ``NEUTRAL_MARKET``. Downstream reason-code
analytics therefore blamed the market regime for every idle tick.

Contract after the fix (strings ONLY — no policy semantics change):
  * default branch  -> ``NO_CANDIDATE_<regime_value>`` when a regime state
    exists (regime value preserved as an embedded suffix, recoverable),
  *                 -> ``NO_CANDIDATE_RANGE_MARKET``  when is_range_market
  *                 -> ``NO_CANDIDATE_NEUTRAL_MARKET`` otherwise,
  * every EXPLICIT rule-path code stays byte-identical
    (FAST_LIQUIDITY_SWEEP_REVERSAL_*, BLOCKED_BY_GUARDIAN_UNSAFE_REGIME,
    INSUFFICIENT_CONFIDENCE, ...),
  * the new codes deliberately avoid the ``REGIME_`` substring so the
    audit_repository regime-fallback partition
    (``reason_code.partition("REGIME_")``) never fires on them.

xdist-safe: no shared filesystem state, fresh SignalPolicy per test,
module-logger untouched (BUG-112/118 rules).
"""

from __future__ import annotations

from datetime import UTC, datetime

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy

NO_CANDIDATE_PREFIX = "NO_CANDIDATE_"


def _tick() -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=2000.10,
        ask=2000.15,
        volume=1.0,
    )


def _feature_vector(**overrides) -> FeatureVector:
    """Zone-neutral flat fixture: no candidate channel may fire.

    tenkan(1999.0) < kijun(2000.5) with is_above_kumo=True kills both
    ichimoku directional flags while keeping is_range_market=False
    (tk_distance 1.5 >= atr*0.20 = 0.40). Override to build variants.
    """
    base = dict(
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
        tenkan_sen=1999.0,
        kijun_sen=2000.5,
        senkou_span_a=1999.5,
        senkou_span_b=2000.5,
        tk_cross_signal=-1,
        is_above_kumo=True,
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
    base.update(overrides)
    return FeatureVector(**base)


#: Flat directional probabilities: no sweep/choch trigger, buy and sell
#: biases balanced at 0.5 (neither > 0.50), so NO rule path fires and the
#: evaluation falls through to the default NO_TRADE branch.
_FLAT_PROBS = torch.tensor([[0.90, 0.03, 0.03, 0.04]])


def _regime_state(
    regime_type: RegimeType = RegimeType.TRENDING_MOMENTUM,
) -> MarketRegimeState:
    return MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=regime_type,
        regime_probability=0.82,
        order_flow_imbalance=0.0,
        realized_volatility_5m=1.0,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.05,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.IOC_MARKET,
        reason=RegimeReason.OFI_TREND_ALIGN,
    )


# ---------------------------------------------------------------------------
# 1. Default branch codes (RED before the fix: REGIME_*/RANGE_BOUND/NEUTRAL)
# ---------------------------------------------------------------------------


def test_default_no_trade_with_regime_uses_no_candidate_code() -> None:
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=_FLAT_PROBS,
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=_regime_state(RegimeType.TRENDING_MOMENTUM),
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "NO_CANDIDATE_TRENDING_MOMENTUM", (
        f"default no-candidate NO_TRADE must be NO_CANDIDATE_<regime>, got {proposal.reason_code!r}"
    )


def test_default_no_trade_without_regime_range_market_code() -> None:
    # Inside kumo (both flags False) => is_range_market=True, no candidate.
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=_FLAT_PROBS,
        current_tick=_tick(),
        feature_vector=_feature_vector(
            is_above_kumo=False,
            is_below_kumo=False,
            tenkan_sen=1999.0,
            kijun_sen=2001.0,
        ),
        regime_state=None,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "NO_CANDIDATE_RANGE_MARKET", proposal.reason_code


def test_default_no_trade_without_regime_neutral_market_code() -> None:
    # Above kumo + tenkan < kijun => is_range_market=False, no candidate.
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=_FLAT_PROBS,
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=None,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "NO_CANDIDATE_NEUTRAL_MARKET", proposal.reason_code


def test_default_code_never_misattributes_the_regime() -> None:
    """The core misattribution pin: a no-candidate NO_TRADE must never carry
    the old REGIME_* / RANGE_BOUND_SIDEWAYS / NEUTRAL_MARKET labels."""
    policy = SignalPolicy()
    for regime_state in (None, _regime_state(RegimeType.RANGING_MEAN_REVERSION)):
        proposal = policy.evaluate_probabilities(
            probabilities=_FLAT_PROBS,
            current_tick=_tick(),
            feature_vector=_feature_vector(
                is_above_kumo=False,
                is_below_kumo=False,
            ),
            regime_state=regime_state,
        )
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.reason_code.startswith(NO_CANDIDATE_PREFIX), proposal.reason_code
        assert "REGIME_" not in proposal.reason_code, proposal.reason_code


# ---------------------------------------------------------------------------
# 2. Regime context recoverable from the new code
# ---------------------------------------------------------------------------


def test_regime_value_still_recoverable_from_new_code() -> None:
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=_FLAT_PROBS,
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=_regime_state(RegimeType.VOLATILITY_EXPANSION),
    )
    expected = NO_CANDIDATE_PREFIX + RegimeType.VOLATILITY_EXPANSION.value
    assert proposal.reason_code == expected
    # The embedded suffix round-trips to the exact regime value.
    assert proposal.reason_code.removeprefix(NO_CANDIDATE_PREFIX) == (
        RegimeType.VOLATILITY_EXPANSION.value
    )


# ---------------------------------------------------------------------------
# 3. Explicit rule-path codes stay byte-identical (pin, green pre+post fix)
# ---------------------------------------------------------------------------


def test_guardian_block_code_byte_identical() -> None:
    policy = SignalPolicy()
    unsafe = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=RegimeType.HIGH_SPREAD_CHOP,
        regime_probability=0.9,
        order_flow_imbalance=0.0,
        realized_volatility_5m=1.0,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.9,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.FREEZE_ALL,
        reason=RegimeReason.SPREAD_SCHMITT,
    )
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.1, 0.8, 0.05, 0.05]]),
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=unsafe,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "BLOCKED_BY_GUARDIAN_UNSAFE_REGIME"


def test_zone_quality_gate_rule_code_unchanged() -> None:
    """A real (weak) sweep candidate must still be rejected by the zone
    quality gate with the exact ZONE_QUALITY_BELOW_THRESHOLD code —
    rule-path codes are not touched by the default-branch rename."""
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.10, 0.30, 0.55, 0.05]]),
        current_tick=_tick(),
        feature_vector=_feature_vector(liquidity_sweep_signal=1),
        regime_state=None,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code.startswith("ZONE_QUALITY_BELOW_THRESHOLD"), proposal.reason_code


def test_asymmetric_rr_rule_code_unchanged() -> None:
    """A strong sweep-reversal candidate that survives every earlier gate
    keeps the exact ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD code (the
    fixture's synthetic levels produce RR 1.1 < min_allowed_rr 1.10 cutoff
    path) — explicit rule codes stay byte-identical."""
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.05, 0.60, 0.30, 0.05]]),
        current_tick=_tick(),
        feature_vector=_feature_vector(liquidity_sweep_signal=1),
        regime_state=None,
    )
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD", proposal.reason_code


# ---------------------------------------------------------------------------
# 4. Default-branch decision metadata unchanged (analytics filters rely on it)
# ---------------------------------------------------------------------------


def test_default_branch_stage_and_blocked_by_unchanged() -> None:
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=_FLAT_PROBS,
        current_tick=_tick(),
        feature_vector=_feature_vector(),
        regime_state=_regime_state(),
    )
    assert proposal.decision_stage == "STANDARD_EVAL"
    assert proposal.blocked_by is None
    assert proposal.risk_allowed is False
    assert proposal.rejection_reason == proposal.reason_code
