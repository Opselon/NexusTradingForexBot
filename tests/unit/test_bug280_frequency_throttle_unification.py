"""BUG-280 (2026-09-14 wave): order-frequency throttle must read the ONE
configured cooldown policy.

Production evidence (wave lane-02, RC-4): the early-return throttle
``SignalPolicy._evaluate_frequency_throttle`` carried a HARDCODED 60.0s floor
while the composition root passes ``cooldown_seconds=4.0``
(live_engine.py) and a SECOND gate (COOLDOWN_ACTIVE, policy.py final-proposal
chain) reads that configured value off the SAME ``_last_signal_time`` field.
The 60s floor pre-empted the 4s gate on every evaluation: 78,798 swallowed
evaluations vs 1,469 persisted decisions (96:1) across ~45 active hours —
the entry rate was capped at 1/minute by an invisible constant, and
protective AI-reversal re-entries shared the same clock.

Contract pinned here:
  1. The throttle window IS ``policy.cooldown_seconds`` — configurable, strict
     when configured strict (a configured 60s still blocks at 60), and never
     a hardcoded magic literal in the throttle source.
  2. Both enforcement points (early throttle + COOLDOWN_ACTIVE gate) consult
     the SAME configured value — no silent divergence possible.
  3. The engine composition passes a configured cooldown (source pin).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.signals.policy import SignalPolicy


def _fv():
    import importlib.util
    import sys
    from pathlib import Path

    helper = Path(__file__).with_name("test_policy_flip_protection_pins_bug227.py")
    spec = importlib.util.spec_from_file_location("_flip_pins280", helper)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_flip_pins280", mod)
    spec.loader.exec_module(mod)
    return mod._feature_vector()


def _tick(ts: datetime, seq: int = 0) -> TickData:
    bid = 2000.10 + seq * 0.01
    return TickData(symbol="XAUUSD", timestamp=ts, bid=bid, ask=bid + 0.05, volume=1.0)


_PROBS = torch.tensor([[0.65, 0.04, 0.24, 0.07]])


def test_throttle_window_follows_configured_cooldown_strict() -> None:
    """A configured STRICT cooldown (60s) must still block at 60 — the fix
    unifies the SOURCE OF TRUTH, it does not loosen any configured value."""
    policy = SignalPolicy(cooldown_seconds=60.0)
    now = datetime.now(UTC)
    policy._last_signal_time = now - timedelta(seconds=10.0)
    proposal = policy.evaluate_probabilities(
        probabilities=_PROBS, current_tick=_tick(now), feature_vector=_fv()
    )
    assert proposal.reason_code == "ORDER_FREQUENCY_THROTTLED"


def test_throttle_window_follows_configured_cooldown_fast() -> None:
    """The engine-configured fast value (4.0s) is honored: 10s elapsed is
    OUTSIDE the window and must not be throttled (pre-fix: always blocked —
    this test is the RED-before pin for the 60s hardcode)."""
    policy = SignalPolicy(cooldown_seconds=4.0)
    now = datetime.now(UTC)
    policy._last_signal_time = now - timedelta(seconds=10.0)
    proposal = policy.evaluate_probabilities(
        probabilities=_PROBS, current_tick=_tick(now), feature_vector=_fv()
    )
    assert proposal.reason_code != "ORDER_FREQUENCY_THROTTLED"
    # inside the configured window still throttles (fresh quote: the
    # duplicate-tick dedupe sits upstream of the throttle):
    policy._last_signal_time = now - timedelta(seconds=1.0)
    now2 = now + timedelta(seconds=0.5)
    proposal2 = policy.evaluate_probabilities(
        probabilities=_PROBS, current_tick=_tick(now2, seq=1), feature_vector=_fv()
    )
    assert proposal2.reason_code == "ORDER_FREQUENCY_THROTTLED"


def test_no_hardcoded_60s_floor_in_throttle_source() -> None:
    src = inspect.getsource(SignalPolicy._evaluate_frequency_throttle)
    assert "60.0" not in src, "the magic constant must not return to the throttle"
    assert "self.cooldown_seconds" in src


def test_both_cooldown_sites_share_one_policy() -> None:
    """COOLDOWN_ACTIVE (final chain) and the early throttle must read the
    same configured field — divergence was the bug class."""
    full = inspect.getsource(SignalPolicy.evaluate_probabilities)
    assert "COOLDOWN_ACTIVE" in full
    assert "self.cooldown_seconds" in full
    # and no literal-seconds comparison adjacent to the cooldown gate
    assert "elapsed < 60" not in full and "< 60.0" not in full


def test_engine_composition_supplies_configured_cooldown() -> None:
    from nexus_scalp.application import live_engine as le

    src = inspect.getsource(le)
    assert "cooldown_seconds=" in src, "composition root must pass the cooldown explicitly"
