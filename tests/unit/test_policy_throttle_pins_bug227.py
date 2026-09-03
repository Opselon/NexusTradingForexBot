"""BUG-227 Wave B2 regression — pin the 60s order-frequency throttle.

Census gap: the hard 60s order-interval throttle in
``SignalPolicy._evaluate_frequency_throttle`` (policy.py:1538-1553) had no
direct behavioral pin — a mutation of the 60.0s constant (or deletion of the
early return) would silently change trade frequency.

Pinned behavior (constant read as the module's contract, not a literal):
  1. A second evaluation within 60s of the last signal returns NO_TRADE with
     reason ORDER_FREQUENCY_THROTTLED.
  2. After the window expires the throttle passes (returns None / lets the
     normal flow proceed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy

THROTTLE_SECONDS = 60.0


def _feature_vector() -> FeatureVector:
    # Import the shared constructor from the flip-pin test module to avoid
    # duplicating the 62-field fixture (same neutral shape).
    import importlib.util
    import sys
    from pathlib import Path

    helper = Path(__file__).with_name("test_policy_flip_protection_pins_bug227.py")
    spec = importlib.util.spec_from_file_location("_flip_pins", helper)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_flip_pins", mod)
    spec.loader.exec_module(mod)
    return mod._feature_vector()


def test_order_throttle_blocks_within_60s() -> None:
    policy = SignalPolicy()
    fv = _feature_vector()
    now = datetime.now(UTC)
    policy._last_signal_time = now - timedelta(seconds=10.0)  # inside window

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.04, 0.24, 0.07]]),
        current_tick=_make_tick(now),
        feature_vector=fv,
    )
    assert proposal.action.value == "NO_TRADE"
    assert proposal.reason_code == "ORDER_FREQUENCY_THROTTLED", proposal.reason_code


def test_order_throttle_releases_after_window() -> None:
    policy = SignalPolicy()
    fv = _feature_vector()
    now = datetime.now(UTC)
    # 61s after the last signal: throttle window expired. The decision path
    # must NOT be blocked by the throttle (any other gate may still reject).
    policy._last_signal_time = now - timedelta(seconds=THROTTLE_SECONDS + 1.0)

    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.65, 0.04, 0.24, 0.07]]),
        current_tick=_make_tick(now),
        feature_vector=fv,
    )
    assert proposal.reason_code != "ORDER_FREQUENCY_THROTTLED", proposal.reason_code


def _make_tick(ts: datetime):
    from nexus_scalp.domain.models import TickData

    return TickData(symbol="XAUUSD", timestamp=ts, bid=2000.10, ask=2000.15, volume=1.0)
