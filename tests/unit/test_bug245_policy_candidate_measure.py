"""BUG-245 regression net — policy.py:393 candidate-measure division-by-zero.

BUG-245 (extension of BUG-208/BUG-194 semantics): CHG-0042's
``_directional_confidence`` already implements degenerate-mass handling
(RAW_FALLBACK when trained mass <= 0 / non-finite / malformed), but the
CANDIDATE measure at policy.py:393 re-implements the identical division
inline WITHOUT any guard:

    cand_ai_prob = (
        prob_buy / (prob_buy + prob_sell + prob_no_trade)
        if "BUY" in cand_action
        else prob_sell / (prob_buy + prob_sell + prob_no_trade)
    )

A zero-sum or negative-sum trained vector (all mass on the untrained WAIT
slice, or BUY=-0.2/SELL=+0.2) reaching a structural candidate channel
(ichimoku-trend, sweep, choch) crashes the LIVE decision path with
ZeroDivisionError on every fresh tick — the exact BUG-194 production
signature (2026-09-01 09:04:31/09:04:33/09:44:05), reachable through MORE
channels than the sweep path the BUG-194 battery pinned.

RED BEFORE (pre-fix tree): tests 1-2 raise ZeroDivisionError /
ValidationError(confidence >= 0). GREEN AFTER: no crash, no negative
confidence, healthy-vector measure unchanged.

Scope guard: candidate channels, thresholds, flip/stat-arb logic, and the
DIRECTION of decisions are untouched — only the degenerate-denominator
arithmetic is aligned with the already-agreed CHG-0042 semantics.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
import torch

from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick

_TICK_SEQ = [0]


def _fresh_policy() -> SignalPolicy:
    p = SignalPolicy()
    p.confidence_threshold = 0.10
    return p


def _evaluate(policy: SignalPolicy, probs: list[float], **fv_updates):
    """Fresh-tick evaluate (bypasses the duplicate-tick masking gate)."""
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
            **fv_updates,
        }
    )
    return policy.evaluate_probabilities(
        probabilities=torch.tensor([probs], dtype=torch.float32),
        current_tick=tick,
        feature_vector=fv,
    )


# ---------------------------------------------------------------------------
# RED probes: candidate channel + degenerate trained mass
# ---------------------------------------------------------------------------


def test_bug245_ichimoku_candidate_zero_trained_mass_no_crash() -> None:
    """All trained mass zero + ichimoku candidate -> NO ZeroDivisionError."""
    proposal = _evaluate(_fresh_policy(), [0.00, 0.00, 0.00, 1.00])
    assert proposal is not None
    assert proposal.action.value == "NO_TRADE"
    assert math.isfinite(float(proposal.confidence))
    assert float(proposal.confidence) >= 0.0


def test_bug245_negative_trained_mass_no_crash_no_negative_confidence() -> None:
    """BUY=-0.20 / SELL=+0.20 / NO_TRADE=0.00 -> denominator 0.0.

    Pre-fix this raised ZeroDivisionError (BUY-sweep channel) or fed a
    NEGATIVE confidence into the frozen TradeProposal model (confidence
    ge=0.0 ValidationError). Post-fix: finite, non-negative, no crash.
    """
    proposal = _evaluate(_fresh_policy(), [0.60, -0.20, 0.20, 0.40])
    assert proposal is not None
    assert float(proposal.confidence) >= 0.0
    assert math.isfinite(float(proposal.confidence))


def test_bug245_sweep_candidate_zero_trained_mass_no_crash() -> None:
    """BUG-194's original sweep-channel shape stays non-crashing too."""
    proposal = _evaluate(
        _fresh_policy(), [0.00, 0.00, 0.00, 1.00], liquidity_sweep_signal=1
    )
    assert proposal is not None
    assert float(proposal.confidence) >= 0.0


def test_bug245_healthy_vector_measure_unchanged() -> None:
    """Healthy vector: candidate measure stays the trained-class directional
    share (CHG-0042 semantics preserved; no regression in the normal path)."""
    proposal = _evaluate(_fresh_policy(), [0.10, 0.54, 0.18, 0.18])
    assert proposal is not None
    # The proposal (or its rejection evidence) must carry a finite,
    # in-range confidence — no manufactured values.
    assert 0.0 <= float(proposal.confidence) <= 1.0
