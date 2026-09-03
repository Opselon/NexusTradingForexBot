"""BUG-227 Wave B regression — pin the behavioral effect of SignalPolicy
flip-protection constants (mutation-census gap).

Census finding: ``flip_confidence_penalty`` (default 0.10) and
``flip_memory_seconds`` (default 8.0) had ZERO test pins — a mutation of
either value (or of the FLIP_PROTECTION gate at policy.py:1006-1030) would
survive CI silently.

These tests pin the BEHAVIOR, not the literal values:
  1. Inside the flip-memory window, an opposite-direction candidate needs
     MORE confidence than the active threshold (the penalty is additive).
  2. After the flip-memory window expires, the penalty no longer applies
     (the gate is a hysteresis window, not a permanent block).
  3. A same-direction candidate inside the window is NOT flip-blocked.

Fixture note: with ``order_manager=None`` the policy releases the prior-
direction state before the flip gate (the no-live-order lock release at
policy.py:933-939), so a MockOrderManager carrying one live 888101 ticket is
required for the prior direction to reach the flip gate. The candidate is
routed through the STANDARD sell channel (ichimoku_bearish + relative sell
bias), NOT the fast-reversal channel — the flip gate is explicitly bypassed
for fast reversals (policy.py:1009), and the zone-quality gate is skipped by
keeping fvg/ob/sweep all neutral.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


class _MockOrderManager:
    """Keeps one live 888101 ticket so the prior-direction state survives."""

    def __init__(self) -> None:
        self._tickets = [{"symbol": "XAUUSD", "magic": 888101, "price": 1990.0}]

    def get_active_live_tickets(self):
        return self._tickets


def _feature_vector(**overrides) -> FeatureVector:
    """Below-kumo bearish standard-channel fixture: drives the SELL_MARKET
    candidate through the ichimoku branch with zone-neutral flags and
    is_range_market=False (kumo below + wide tenkan/kijun distance)."""
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
        is_above_kumo=False,
        is_below_kumo=True,
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


def _tick(ts: datetime | None = None) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=ts or datetime.now(UTC),
        bid=2000.10,
        ask=2000.15,
        volume=1.0,
    )


def test_flip_penalty_raises_required_confidence_within_window() -> None:
    """Inside the flip-memory window, an opposite-direction STANDARD-channel
    candidate must be flip-blocked when its confidence sits in
    [base_threshold, base_threshold + flip_confidence_penalty) — the penalty
    is additive on top of the base gate."""
    policy = SignalPolicy()  # defaults: base 0.20, flip penalty 0.10, memory 8s
    now = datetime.now(UTC)
    fv = _feature_vector()  # ichimoku_bearish standard channel, zone-neutral

    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_active_direction_time = now - timedelta(seconds=1.0)
    policy._last_signal_time = None  # keep COOLDOWN out of the way

    # sell=0.24/(0.04+0.24+0.65) => conf ~0.258 in [0.20, 0.30): passes the
    # base gate, fails the flip gate.
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.04, 0.24, 0.07]]),
        current_tick=_tick(now),
        feature_vector=fv,
        order_manager=_MockOrderManager(),
    )
    assert proposal.blocked_by == "FLIP_PROTECTION", (
        f"expected flip-block inside window, got blocked_by={proposal.blocked_by} "
        f"stage={proposal.decision_stage} reason={proposal.reason_code}"
    )
    assert "FLIP_PROTECTION_BLOCKED" in (proposal.reason_code or "")


def test_flip_memory_window_expiry_releases_penalty() -> None:
    """After flip_memory_seconds the penalty must not apply: the same
    sweep-reversal candidate that is flip-blocked inside the window proceeds
    to the LATER gates (not FLIP_PROTECTION) outside it — hysteresis window
    semantics, not a permanent block."""
    policy = SignalPolicy()
    fv = _feature_vector(liquidity_sweep_signal=-1)
    now = datetime.now(UTC)

    policy._last_active_direction = ActionType.BUY_MARKET
    # 9s > default flip_memory_seconds (8s): outside the window.
    policy._last_active_direction_time = now - timedelta(seconds=9.0)
    policy._last_signal_time = None

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.04, 0.24, 0.07]]),
        current_tick=_tick(now),
        feature_vector=fv,
        order_manager=_MockOrderManager(),
    )
    # The gate under test must NOT be the blocker here.
    assert proposal.blocked_by != "FLIP_PROTECTION", (
        "flip penalty must expire with flip_memory_seconds"
    )


def test_same_direction_reentry_not_flip_blocked() -> None:
    """The flip gate only guards DIRECTION changes: a same-direction
    continuation inside the window must not be flip-blocked."""
    policy = SignalPolicy()
    fv = _feature_vector(
        tenkan_sen=2000.5,
        kijun_sen=1999.0,
        senkou_span_a=2000.5,
        senkou_span_b=1999.5,
        tk_cross_signal=1,
        is_below_kumo=False,
        is_above_kumo=True,
    )  # ichimoku_bullish standard channel
    now = datetime.now(UTC)

    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_active_direction_time = now - timedelta(seconds=1.0)
    policy._last_signal_time = None

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.24, 0.04, 0.07]]),
        current_tick=_tick(now),
        feature_vector=fv,
        order_manager=_MockOrderManager(),
    )
    # Whatever the other gates decide, FLIP_PROTECTION must not be the
    # blocker for a same-direction candidate.
    assert proposal.blocked_by != "FLIP_PROTECTION", proposal.reason_code
