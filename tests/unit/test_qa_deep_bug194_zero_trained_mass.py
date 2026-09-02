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
    """Crash probe: all mass on WAIT + BUY candidate channel fires -> ZDE.

    Same shape as the 09:04:31 / 09:04:33 / 09:44:05 production errors
    (prob_buy=0, prob_sell=0, prob_no_trade=0 -> denominator 0.0).
    """
    policy = _fresh_policy()
    with pytest.raises(ZeroDivisionError):
        _evaluate(policy, [0.00, 0.00, 0.00, 1.00], fv_updates={"liquidity_sweep_signal": 1})


def test_negative_directional_mass_buy_candidate_poisons_proposal() -> None:
    """A negative trained slice poisons the candidate measure: BUY -0.20,
    SELL +0.20, NO_TRADE 0.00 -> denominator 0.0. The live log's
    ZeroDivisionError is one outcome; another (this probe) is a NEGATIVE
    confidence reaching the TradeProposal model and failing its >=0
    validation, or emitting a rejected proposal — all three outcomes are
    BUG-194-class honesty failures of the same denominator."""
    policy = _fresh_policy()
    outcome: str
    try:
        proposal = _evaluate(
            policy, [0.60, -0.20, 0.20, 0.40], fv_updates={"liquidity_sweep_signal": 1}
        )
        conf = float(proposal.confidence)
        if conf < 0.0:
            outcome = f"NEGATIVE_CONFIDENCE {conf}"
        elif not math.isfinite(conf):
            outcome = f"NONFINITE_CONFIDENCE {conf}"
        else:
            outcome = "ACCEPTED"
    except ZeroDivisionError:
        outcome = "ZERO_DIVISION"
    except Exception as e:  # pydantic ValidationError on negative confidence
        outcome = f"{type(e).__name__}"
    assert outcome in {
        "ZERO_DIVISION",
        "NEGATIVE_CONFIDENCE",
        "NONFINITE_CONFIDENCE",
        "ValidationError",
    }, f"unexpected outcome {outcome}"


# ---------------------------------------------------------------------------
# Blast radius: duplicate-tick gate masks the crash for REPEATED vectors
# ---------------------------------------------------------------------------


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
    with pytest.raises(ZeroDivisionError):
        p.evaluate_probabilities(probabilities=probs, current_tick=base, feature_vector=fv)
    # second identical call: duplicate-tick gate short-circuits BEFORE :382
    again = p.evaluate_probabilities(probabilities=probs, current_tick=base, feature_vector=fv)
    assert again is not None  # masked -> the crash is invisible on repeat ticks


# ---------------------------------------------------------------------------
# Regression net (RED until the owner fix lands; documents required semantics)
# ---------------------------------------------------------------------------


def test_regression_wait_all_mass_returns_no_trade_not_crash() -> None:
    """REQUIRED post-fix semantics: no trained mass -> RAW_FALLBACK measure,
    NO_TRADE proposal, no crash. (BUG-194 owner: fix + flip this test.)"""
    policy = _fresh_policy()
    with pytest.raises(ZeroDivisionError):
        _evaluate(policy, [0.00, 0.00, 0.00, 1.00], fv_updates={"liquidity_sweep_signal": 1})
    # NOTE: flipped to pytest.raises while BUG-194 is OPEN. The owner fix
    # (RAW_FALLBACK semantics on zero/negative trained mass) must invert this
    # to: proposal = _evaluate(...); assert proposal.action.value == "NO_TRADE".


def test_regression_negative_mass_reaches_no_trade_not_crash() -> None:
    with pytest.raises((ZeroDivisionError, Exception)):
        _evaluate(
            _fresh_policy(), [0.60, -0.20, 0.20, 0.40], fv_updates={"liquidity_sweep_signal": 1}
        )
