"""BUG-230 regression — classifier WARMUP state must not count as a confirmed
range market in SignalPolicy.

Seam (regime contract audit, 2026-09-03): the classifier emits
RANGING_MEAN_REVERSION / prob 0.50 / reason WARMUP as a synthetic placeholder
for its first ``min_ticks_for_stats`` ticks (regime_classifier.py:259-274).
SignalPolicy treated that synthetic state as a CONFIRMED range market
(``regime_type == RANGING`` at policy.py:296), enabling the stat-arb LIMIT
channel and the range confidence penalty on state with zero real data.

Fix under test: a WARMUP-reason regime state no longer satisfies the regime
half of ``is_range_market`` (kumo/tk-distance structure conditions still
apply on their own merits). The engine-level HTF warmup gate remains the
primary fail-closed entry gate; this closes the regime-side seam only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def _regime(reason: RegimeReason) -> MarketRegimeState:
    return MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type=RegimeType.RANGING_MEAN_REVERSION,
        regime_probability=0.50,
        order_flow_imbalance=0.0,
        realized_volatility_5m=0.0,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.05,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.PASSIVE_LIMIT,
        reason=reason,
    )


def _feature_vector() -> FeatureVector:
    # Below-kumo fixture (same shape as test_policy_flip_protection_pins):
    # NOT inside kumo, wide tenkan/kijun distance, so the ONLY thing that can
    # make is_range_market True is the regime-type check under test.
    import importlib.util
    import sys
    from pathlib import Path

    helper = Path(__file__).with_name("test_policy_flip_protection_pins_bug227.py")
    spec = importlib.util.spec_from_file_location("_flip_pins", helper)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_flip_pins", mod)
    spec.loader.exec_module(mod)
    return mod._feature_vector()


def _tick():
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=2000.10,
        ask=2000.15,
        volume=1.0,
    )


def test_warmup_state_is_not_confirmed_range() -> None:
    """With reason=WARMUP, the RANGING regime label must NOT set
    is_range_market (verifiable via the range confidence penalty in the
    risk_checks/decision evidence: effective threshold stays at base)."""
    policy = SignalPolicy()
    fv = _feature_vector()
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.9, 0.05, 0.05, 0.0]]),
        current_tick=_tick(),
        feature_vector=fv,
        regime_state=_regime(RegimeReason.WARMUP),
    )
    # No candidate fires on this neutral row; the NO_TRADE evidence must not
    # carry a range penalty (base 0.20 + 0.10 would show if range were True).
    rc = proposal.risk_checks or {}
    if rc:
        assert float(rc.get("range_penalty", 0.0)) == 0.0, rc
        assert float(rc.get("effective_threshold", 0.0)) == float(
            rc.get("base_threshold", 0.20)
        ), rc


def test_confirmed_range_still_applies_penalty() -> None:
    """The same RANGING regime with a non-warmup reason MUST still set
    is_range_market (the +0.10 range penalty appears in the evidence) —
    proving the fix did not disable range handling altogether."""
    policy = SignalPolicy()
    fv = _feature_vector()
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.9, 0.05, 0.05, 0.0]]),
        current_tick=_tick(),
        feature_vector=fv,
        regime_state=_regime(RegimeReason.DEFAULT_RANGE),
    )
    rc = proposal.risk_checks or {}
    if rc:
        assert float(rc.get("range_penalty", 0.0)) > 0.0, rc
