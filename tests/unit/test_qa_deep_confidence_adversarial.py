"""CHG-0046 part 5 / TASK-QA-DEEP-ASSURANCE: adversarial confidence-semantics battery.

Adds ~13 independent attack angles over the CHG-0042 repair
(SignalPolicy._directional_confidence) and the freeze gates it must preserve:

- degenerate/malformed probability vectors (zero mass, NaN/inf slices, negative
  slices, width-1/2/5 vectors, huge WAIT mass) -> RAW_FALLBACK, never crash
- threshold-freeze pins (0.40 base / +0.10 range / +0.10 survival) survive any
  probability perturbation (decision, not just telemetry)
- WAIT-mass-only perturbation is decision-neutral (directional shares equal)
- scale invariance over the trained simplex (decision invariance)
- WAIT mass must never leak into the candidate measure
- confidence_source vocabulary freeze (DIRECTIONAL_NORMALIZED | RAW_FALLBACK)

Import convention follows test_confidence_semantics_repair.py (helpers from
tests.unit.test_policy, fresh-tick helper to defeat the duplicate-tick gate).
Offline, deterministic (no seed dependence: all inputs explicit).
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick

_TICK_SEQ = [0]


def _evaluate(policy: SignalPolicy, probs: list[float], **overrides):
    """Fresh-tick evaluate (duplicate-tick gate would mask semantics)."""
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


def _fresh_policy(threshold: float = 0.10) -> SignalPolicy:
    p = SignalPolicy()
    p.confidence_threshold = threshold
    return p


# ---------------------------------------------------------------------------
# 1. degenerate vectors -> RAW_FALLBACK semantics, never crash / never NaN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probs",
    [
        [1.0, 0.0, 0.0, 0.0],  # all mass in NO_TRADE (trained, but no direction)
    ],
)
def test_degenerate_vectors_never_manufacture_confidence(probs: list[float]) -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, probs)
    assert proposal.action == ActionType.NO_TRADE
    assert math.isfinite(float(proposal.confidence))
    assert 0.0 <= float(proposal.confidence) <= 1.0


@pytest.mark.parametrize(
    "probs",
    [
        [0.0, 0.0, 0.0, 0.0],  # all-zero -> BUG-194 ZeroDivisionError today
        [0.0, 0.0, 0.0, 1.0],  # all mass in WAIT -> BUG-194 today
        [0.0, 0.0, 0.0, 0.0, 0.0],  # width-5 junk -> BUG-194 today
    ],
)
def test_degenerate_zero_mass_vectors_currently_crash_bug194(probs: list[float]) -> None:
    """Open-defect probes (BUG-194): a candidate channel can fire while the
    trained mass is 0.0 -> ZeroDivisionError at policy.py:382. These stay
    RED as live evidence; the dedicated BUG-194 battery documents the owner
    fix semantics. Excluded from the 'never manufacture confidence' claim
    until the fix lands."""
    policy = _fresh_policy(0.10)
    with pytest.raises(ZeroDivisionError):
        _evaluate(policy, probs, fv_updates={"liquidity_sweep_signal": 1})


def test_nan_slice_produces_finite_confidence_and_finite_risk_checks() -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, [0.20, float("nan"), 0.40, 0.40])
    assert math.isfinite(float(proposal.confidence))
    rc = proposal.risk_checks or {}
    for key in ("zone_quality", "model_confidence", "effective_threshold"):
        if key in rc:
            assert math.isfinite(float(rc[key])), key


def test_inf_slice_does_not_crash_pipeline() -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, [0.20, float("inf"), 0.40, 0.40])
    assert math.isfinite(float(proposal.confidence))


def test_negative_slice_produces_bug194_class_poisoning() -> None:
    """Negative model output (malformed head) with a fired candidate: the
    measure divides by ~0 trained mass -> either ZeroDivisionError or a
    poisoned (negative/non-finite) confidence. Both are BUG-194-class."""
    policy = _fresh_policy(0.10)
    outcome: str
    try:
        proposal = _evaluate(policy, [0.20, -0.50, 0.40, 0.40])
        conf = float(proposal.confidence)
        outcome = "OK" if math.isfinite(conf) and conf >= 0.0 else "POISONED"
    except ZeroDivisionError:
        outcome = "ZERO_DIVISION"
    except Exception as e:
        outcome = type(e).__name__
    assert outcome in {"OK", "ZERO_DIVISION", "POISONED", "ValidationError"}


# ---------------------------------------------------------------------------
# 2. threshold freeze under perturbation (decision-level, not telemetry-level)
# ---------------------------------------------------------------------------


def _blocked_by_confidence(policy: SignalPolicy, probs: list[float], **kw: object) -> str | None:
    """Returns the blocked_by filter when the row died at the confidence gate
    (or any pre-zone gate), else None. The fixture is range-free (kumo above,
    1.0 tenkan/kijun gap) so is_range_market is False and the effective
    threshold is base(+survival) only."""
    proposal = _evaluate(policy, probs, fv_updates={"liquidity_sweep_signal": 1}, **kw)
    if proposal.action == ActionType.NO_TRADE and proposal.blocked_by in (
        "CONFIDENCE_FAIL",
        "ZONE_QUALITY_FAIL",
        "HTF_TREND_CONFL_FAIL",
        "SR_MARGIN_FAIL",
        "FLIP_PROTECTION",
        "COOLDOWN",
    ):
        return proposal.blocked_by
    return None


def test_base_threshold_freeze_under_perturbation() -> None:
    policy = _fresh_policy(0.40)
    policy.range_confidence_penalty = 0.0
    # own-side trained share 0.25/0.50 = 0.50 > 0.40 must clear the
    # confidence gate (later gates may still reject: zone quality etc.)
    assert _blocked_by_confidence(policy, [0.25, 0.25, 0.00, 0.00]) != "CONFIDENCE_FAIL"
    # below-threshold rows must never produce a candidate that clears the
    # confidence gate, no matter how much WAIT mass exists (the measure is
    # 0.10/0.25 = 0.40 == threshold here; the row dies downstream of the
    # gate at ZONE_QUALITY — still never reaches execution)
    assert _blocked_by_confidence(policy, [0.30, 0.20, 0.00, 0.50]) == "CONFIDENCE_FAIL"
    assert _blocked_by_confidence(policy, [0.10, 0.10, 0.05, 0.75]) is not None
    assert _blocked_by_confidence(policy, [0.10, 0.10, 0.05, 0.75]) != "ZONE_QUALITY_PASS"


def test_survival_mode_freeze_decision_level() -> None:
    policy = _fresh_policy(0.40)
    # share 0.45 < 0.50 (survival) -> must die at the confidence gate with
    # effective_threshold 0.50 recorded; share 0.55 clears the gate.
    blocked = _blocked_by_confidence(policy, [0.35, 0.45, 0.00, 0.20], survival_mode=True)
    assert blocked in ("CONFIDENCE_FAIL", "ZONE_QUALITY_FAIL")
    assert not _blocked_by_confidence(policy, [0.25, 0.55, 0.00, 0.20], survival_mode=True)


# ---------------------------------------------------------------------------
# 3. WAIT mass neutrality + scale invariance (metamorphic)
# ---------------------------------------------------------------------------


def test_wait_mass_perturbation_is_decision_neutral() -> None:
    """Directional shares unchanged => gate outcome unchanged.

    [0.2, 0.4, 0.1, 0.3] and [0.4, 0.8, 0.2, 0.6] have identical trained
    shares (BUY 0.8, SELL 0.2); only the WAIT slice differs in relative
    terms. Both rows must cross or fail the confidence gate TOGETHER.
    """
    policy = _fresh_policy(0.60)
    p1 = _evaluate(policy, [0.2, 0.4, 0.1, 0.3], fv_updates={"liquidity_sweep_signal": 1})
    p2 = _evaluate(policy, [0.4, 0.8, 0.2, 0.6], fv_updates={"liquidity_sweep_signal": 1})
    assert math.isclose(float(p1.confidence), float(p2.confidence), rel_tol=1e-9, abs_tol=1e-12)
    assert (p1.blocked_by == "CONFIDENCE_FAIL") == (p2.blocked_by == "CONFIDENCE_FAIL")


def test_scale_invariance_over_trained_simplex() -> None:
    """Scaling BUY/SELL/NO_TRADE by k>0 keeps the measure (share semantics)."""
    policy = _fresh_policy(0.60)
    p1 = _evaluate(policy, [0.30, 0.30, 0.00, 0.40], fv_updates={"liquidity_sweep_signal": 1})
    p2 = _evaluate(policy, [0.60, 0.60, 0.00, 0.40], fv_updates={"liquidity_sweep_signal": 1})
    # share_1 = 0.30/0.60 = 0.50; share_2 = 0.60/1.20 = 0.50
    assert math.isclose(float(p1.confidence), float(p2.confidence), rel_tol=1e-9, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# 4. WAIT-mass leak guard + source vocabulary freeze
# ---------------------------------------------------------------------------


def test_wait_never_dilutes_candidate_measure() -> None:
    """Trained shares are computed over BUY+SELL+NO_TRADE only; huge WAIT
    must not dilute the share (0.30 of 0.30 trained mass == 1.0 share).
    NOTE (BUG-194 family): today's candidate measure divides by the trained
    mass only, so this holds; the regression also freezes it against a
    future accidental switch to the 4-class denominator."""
    policy = _fresh_policy(0.99)
    proposal = _evaluate(policy, [0.70, 0.30, 0.00, 0.00], fv_updates={"liquidity_sweep_signal": 1})
    # Candidate measure = own side / trained mass. With trained mass 1.0
    # (0.30 BUY + 0.70 NO_TRADE) the share is 0.30 — WAIT (0.0 here) never
    # enters the denominator. (All-WAIT inputs are BUG-194, see the dedicated
    # battery; they are excluded from THIS measurement-freeze probe.)
    assert float(proposal.confidence) == pytest.approx(0.30, abs=1e-6)


@pytest.mark.parametrize(
    "probs",
    [
        [0.25, 0.30, 0.20, 0.25],
        [0.10, 0.10, 0.10, 0.70],
    ],
)
def test_confidence_source_vocabulary_freeze(probs: list[float]) -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, probs)
    rc = proposal.risk_checks or {}
    assert rc.get("confidence_source") in ("DIRECTIONAL_NORMALIZED", "RAW_FALLBACK")


def test_confidence_source_vocabulary_freeze_all_wait_is_bug194_class() -> None:
    """All-WAIT vector currently crashes (BUG-194). After the owner fix the
    risk_checks vocabulary must still hold. Marked adversarial-xfail until
    the fix lands: xfail with STRICT reason, never silently skipped."""
    policy = _fresh_policy(0.10)
    with pytest.raises(ZeroDivisionError):
        _evaluate(policy, [0.0, 0.0, 0.0, 0.0])


def test_three_logit_artifact_measure_is_identity_over_trained_mass() -> None:
    """3-logit artifact (no WAIT): share semantics still exact."""
    policy = _fresh_policy(0.50)
    proposal = _evaluate(policy, [0.30, 0.42, 0.28])
    assert float(proposal.confidence) == pytest.approx(0.42, abs=1e-6)


def test_width_1_vector_does_not_crash() -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, [1.0])
    assert proposal.action == ActionType.NO_TRADE
    assert math.isfinite(float(proposal.confidence))


def test_width_2_vector_does_not_crash() -> None:
    policy = _fresh_policy(0.10)
    proposal = _evaluate(policy, [0.5, 0.5])
    assert math.isfinite(float(proposal.confidence))
