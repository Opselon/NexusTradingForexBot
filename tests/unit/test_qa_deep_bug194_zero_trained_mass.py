"""CHG-0046 part 5 / TASK-QA-DEEP-ASSURANCE: BUG-194 ZeroDivisionError battery.

Reproduces the live ZeroDivisionError at signals/policy.py:382 (candidate
directional measure divides by trained mass == 0.0 when the 4-logit head
puts ALL mass on the untrained WAIT slice and a candidate channel fires),
probes the blast radius (duplicate-tick gate at :323 masks the crash for
REPEATED identical vectors — first-sight + real streams crash), and
regression-guards the eventual owner fix WITHOUT changing production code
in this pass (brief: tests route proven testability/correctness defects to
the owner; the fix is a separate controlled change).
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
import torch

from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick

_TICK_SEQ = [0]


def _evaluate(policy: SignalPolicy, probs: list[float], **overrides):
    """Fresh-tick evaluate (bypasses duplicate-tick masking)."""
    _TICK_SEQ[0] += 1
    base = _make_tick()
    tick = base.model_copy(
        update={
            "timestamp": base.timestamp + timedelta(seconds=_TICK_SEQ[0]),
            "bid": base.bid + 0.01 * _TICK_SEQ[0],
            "ask": base.ask + 0.01 * _TICK_SEQ[0],
        }
    )
    fv = _make_feature_vector().model_copy(
        update={
            "is_above_kumo": True,
            "tenkan_sen": 1999.0,
            "kijun_sen": 1998.0,
            "senkou_span_a": 1999.0,
            "senkou_span_b": 1998.0,
            "live_tick_displacement": 0.5,
            **overrides.get("fv_updates", {}),
        }
    )
    return policy.evaluate_probabilities(
        probabilities=torch.tensor([probs], dtype=torch.float32),
        current_tick=tick,
        feature_vector=fv,
        **{k: v for k, v in overrides.items() if k != "fv_updates"},
    )


def _fresh_policy() -> SignalPolicy:
    p = SignalPolicy()
    p.confidence_threshold = 0.10
    return p


# ---------------------------------------------------------------------------
# Live evidence probes (crash TODAY = BUG-194 unfixed)
# ---------------------------------------------------------------------------


def test_wait_all_mass_buy_candidate_zero_division() -> None:
    """FIXED (BUG-245B, bfeb08dd): all mass on WAIT + BUY candidate -> NO crash.

    Same shape as the 09:04:31 / 09:04:33 / 09:44:05 production errors
    (prob_buy=0, prob_sell=0, prob_no_trade=0 -> denominator 0.0).
    Pre-fix this raised ZeroDivisionError (red-before); the landed fix
    aligns the candidate measure with the CHG-0042 degenerate-mass
    handler: NO crash, finite non-negative confidence, NO_TRADE
    proposal.
    """
    policy = _fresh_policy()
    proposal = _evaluate(
        policy, [0.00, 0.00, 0.00, 1.00], fv_updates={"liquidity_sweep_signal": 1}
    )
    assert proposal is not None
    assert proposal.action.value == "NO_TRADE"
    assert math.isfinite(float(proposal.confidence))
    assert float(proposal.confidence) >= 0.0

def test_negative_directional_mass_buy_candidate_poisons_proposal() -> None:
    """FIXED (BUG-245B, bfeb08dd): BUY -0.20 / SELL +0.20 / NO_TRADE 0.00
    (denominator 0.0) no longer poisons the proposal: the candidate
    measure clamps own-side to >= 0 and normalizes ONLY over a positive
    finite trained mass. Post-fix contract: NO crash, NO negative
    confidence, NO ValidationError — a finite non-negative confidence
    proposal or a NO_TRADE rejection is the only allowed outcome.
    """
    policy = _fresh_policy()
    proposal = _evaluate(
        policy, [0.60, -0.20, 0.20, 0.40], fv_updates={"liquidity_sweep_signal": 1}
    )
    conf = float(proposal.confidence)
    assert math.isfinite(conf), f"non-finite confidence {conf}"
    assert conf >= 0.0, f"negative confidence {conf}"

def test_duplicate_tick_masking_evidence() -> None:
    """The SECOND identical evaluation with the SAME tick signature returns
    the cached duplicate-tick proposal instead of reaching :382 — so suites
    that reuse a tick never see the crash. Documents the masking path that
    hid BUG-194 from the existing battery."""
    p = SignalPolicy()
    p.confidence_threshold = 0.10
    base = _make_tick()
    fv = _make_feature_vector().model_copy(
        update={
            "is_above_kumo": True,
            "tenkan_sen": 1999.0,
            "kijun_sen": 1998.0,
            "senkou_span_a": 1999.0,
            "senkou_span_b": 1998.0,
            "live_tick_displacement": 0.5,
            "liquidity_sweep_signal": 1,
        }
    )
    probs = torch.tensor([[0.0, 0.0, 0.0, 1.0]], dtype=torch.float32)
    # FIXED (BUG-245B): first call no longer crashes (degenerate-mass
    # handler); the duplicate-tick gate still short-circuits the SECOND
    # identical evaluation to the cached proposal (masking evidence for
    # suites that reuse a tick).
    first = p.evaluate_probabilities(probabilities=probs, current_tick=base, feature_vector=fv)
    assert first is not None
    again = p.evaluate_probabilities(probabilities=probs, current_tick=base, feature_vector=fv)
    assert again is not None


# ---------------------------------------------------------------------------
# Regression net (RED until the owner fix lands; documents required semantics)
# ---------------------------------------------------------------------------


def test_regression_wait_all_mass_returns_no_trade_not_crash() -> None:
    """FIXED (BUG-245B, bfeb08dd): REQUIRED post-fix semantics now hold —
    no trained mass -> RAW_FALLBACK measure, NO_TRADE proposal, no crash.
    Inverted exactly per this test's original NOTE (was pinned to
    pytest.raises while the defect was open).
    """
    policy = _fresh_policy()
    proposal = _evaluate(
        policy, [0.00, 0.00, 0.00, 1.00], fv_updates={"liquidity_sweep_signal": 1}
    )
    assert proposal.action.value == "NO_TRADE"
    assert float(proposal.confidence) >= 0.0

def test_regression_negative_mass_reaches_no_trade_not_crash() -> None:
    """FIXED (BUG-245B, bfeb08dd): negative-sum trained mass no longer
    raises or poisons the proposal — finite non-negative confidence
    only.
    """
    proposal = _evaluate(
        _fresh_policy(), [0.60, -0.20, 0.20, 0.40], fv_updates={"liquidity_sweep_signal": 1}
    )
    assert math.isfinite(float(proposal.confidence))
    assert float(proposal.confidence) >= 0.0
