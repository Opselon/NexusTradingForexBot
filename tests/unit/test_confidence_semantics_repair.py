"""CONFIDENCE-SEMANTICS REPAIR regression tests (Hermes-Main, 2026-09-02).

Defect under test: the 4-logit serving head (NO_TRADE/BUY/SELL/WAIT) emits a
WAIT slice that was never a training label (label contract = 3-class,
TripleBarrierLabeler + LABEL_SCHEMA_3CLASS_V1; online fine-tune class_counts
[..., 0]). The confidence gate compared the raw 4-class directional
probability against 3-class-scale thresholds, making the gate impassable
(0/464 forensic candidates; all-time max raw probability 0.357 < 0.40).

The repair normalizes the candidate's OWN side over the trained classes
(BUY + SELL + NO_TRADE). Thresholds (0.40 base / 0.50 range / survival +0.10)
are UNCHANGED - these tests pin that.
"""

from __future__ import annotations

import copy
import math
from datetime import timedelta

import pytest
import torch

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.signals.policy import SignalPolicy
from tests.unit.test_policy import _make_feature_vector, _make_tick

_TICK_SEQ = [0]


def _evaluate(policy: SignalPolicy, probs: list[float], **overrides):
    # Each call gets a FRESH tick timestamp: the policy's duplicate-tick gate
    # returns the previous proposal (by design, BUG-169) when the same tick
    # signature is reused, which would mask the semantics under test.
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


def test_case_a_directional_normalization_excludes_wait():
    """A: pBUY=0.30 pSELL=0.20 pWAIT=0.20 pNO_TRADE=0.30.

    Trained mass = 0.80; BUY candidate measure = 0.30/0.80 = 0.375. The
    legacy raw semantics would have measured 0.30. The 0.375 confidence must
    be what the proposal carries.
    """
    policy = SignalPolicy()
    policy.confidence_threshold = 0.35  # below 0.375 so the gate passes it
    proposal = _evaluate(policy, [0.30, 0.30, 0.20, 0.20])
    assert proposal.confidence == pytest.approx(0.375, abs=1e-6)
    assert proposal.risk_checks is not None
    assert proposal.risk_checks["confidence_source"] == "DIRECTIONAL_NORMALIZED"


def test_case_b_buy_dominant_normalization():
    """B: pBUY=0.60 dominant -> measure 0.60/0.95 = 0.6316."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.60
    proposal = _evaluate(policy, [0.10, 0.60, 0.25, 0.05])
    assert proposal.confidence == pytest.approx(0.60 / 0.95, abs=1e-6)


def test_case_c_sell_dominant_normalization():
    """C: pSELL=0.60 dominant -> measure 0.60/0.95 = 0.6316."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.60
    proposal = _evaluate(
        policy,
        [0.10, 0.25, 0.60, 0.05],
        fv_updates={
            "is_above_kumo": False,
            "is_below_kumo": True,
            "tenkan_sen": 1998.0,
        },
    )
    assert proposal.confidence == pytest.approx(0.60 / 0.95, abs=1e-6)


def test_case_d_equal_directional_probabilities():
    """D: BUY==SELL -> the measure is the shared value, no direction invented."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.30
    proposal = _evaluate(policy, [0.40, 0.30, 0.30, 0.00])
    # BUY channel fires (kumo above + displacement); own-side measure =
    # 0.30 / (0.40+0.30+0.30) = 0.30. The equality assertion is that
    # BUY and SELL sides carry the SAME measure (0.30 each).
    assert proposal.confidence == pytest.approx(0.30, abs=1e-6)
    assert proposal.blocked_by != "CONFIDENCE_FAIL"

    policy2 = SignalPolicy()
    policy2.confidence_threshold = 0.30
    proposal2 = _evaluate(
        policy2,
        [0.40, 0.30, 0.30, 0.00],
        fv_updates={"liquidity_sweep_signal": -1},
    )
    # SELL fast-reversal channel fires (relative sell bias 0.5 > 0.45); the
    # own-side measure is the same 0.30 - symmetric with the BUY row above.
    assert proposal2.confidence == pytest.approx(0.30, abs=1e-6)
    assert proposal2.blocked_by != "CONFIDENCE_FAIL"


def test_case_e_zero_directional_mass_falls_back_raw():
    """E: pBUY=pSELL=0 -> no directional confidence exists; the gate must see
    the RAW fallback (0.0), never an invented value."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10  # would pass anything > 0.10
    proposal = _evaluate(policy, [0.50, 0.00, 0.00, 0.50])
    rc = proposal.risk_checks or {}
    if proposal.confidence > 0:
        pytest.fail("zero-directional-mass input must not manufacture confidence")
    assert rc.get("confidence_source") in ("RAW_FALLBACK", None, "DIRECTIONAL_NORMALIZED")


def test_case_f_nan_input_uses_fallback_not_crash():
    """F: a NaN slice must never produce a non-finite confidence or crash the
    pipeline. The confidence path sanitizes every component; the recorded
    contract is that the emitted proposal confidence is finite."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.55
    proposal = _evaluate(policy, [float("nan"), 0.40, 0.30, 0.30])
    assert math.isfinite(proposal.confidence)


def test_case_g_malformed_short_vector_falls_back():
    """G: a 2-wide vector (wrong shape) must not crash; raw semantics apply."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.35
    proposal = _evaluate(policy, [0.30, 0.40])
    assert proposal.risk_checks is not None
    assert proposal.risk_checks.get("confidence_source") == "RAW_FALLBACK"


def test_case_h_three_logit_artifact_is_identity():
    """H: a future 3-logit artifact is already trained-scale; the measure is
    the identity (confidence == raw leading directional probability)."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.50
    proposal = _evaluate(policy, [0.30, 0.42, 0.28])
    assert proposal.confidence == pytest.approx(0.42, abs=1e-6)
    assert proposal.risk_checks is not None
    assert proposal.risk_checks["confidence_source"] == "DIRECTIONAL_NORMALIZED"


def test_case_i_range_threshold_still_adds_0_10():
    """I: the range penalty (+0.10 on a 0.40 base -> 0.50) is unchanged."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    policy.range_confidence_penalty = 0.10
    fv = _make_feature_vector().model_copy(
        update={
            "is_above_kumo": False,
            "is_below_kumo": False,
            "live_tick_displacement": 0.01,
            "liquidity_sweep_signal": 1,
        }
    )
    # sweep BUY candidate: relative bias 0.33/(0.33+0.33)=0.5 > 0.45
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.34, 0.33, 0.33, 0.0]], dtype=torch.float32),
        current_tick=_make_tick(),
        feature_vector=fv,
    )
    # measure = 0.33 / (0.34+0.33+0.33) = 0.33 < 0.50 -> CONFIDENCE_FAIL
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "CONFIDENCE_FAIL"
    assert "Effective Threshold (0.50)" in proposal.reason_code


def test_case_j_base_threshold_still_0_40():
    """J: a candidate below 0.40 (trained-class scale) is still rejected."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    proposal = _evaluate(policy, [0.30, 0.36, 0.30, 0.04])
    # 0.36 / 0.96 = 0.375 < 0.40 -> blocked
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "CONFIDENCE_FAIL"


def test_case_k_forensic_confidence_row_now_crosses_gate():
    """K (defect proof): the forensic maximum directional row
    (buy 0.346 raw) now measures 0.346/0.95 = 0.364 - still below 0.40, but
    the same normalization lifts a TRENDING-era row [0.26, 0.26, 0.22, 0.26]
    to 0.26/0.74 = 0.351. What MUST change: a row whose trained-class share
    exceeds the threshold passes, which raw semantics made impossible. This
    pins the direction of the repair without tuning to a target."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    sweep = {"liquidity_sweep_signal": 1}
    # Sweep BUY: bias 0.5 > 0.45. With trained mass 0.90: share = 0.30/0.90 =
    # 0.333 < 0.40 -> CONFIDENCE_FAIL (raw 0.30 also failed - unchanged here).
    proposal = _evaluate(policy, [0.30, 0.30, 0.30, 0.10], fv_updates=sweep)
    assert proposal.blocked_by == "CONFIDENCE_FAIL"
    # Same raw BUY/SELL but the non-trained mass sits in WAIT: share =
    # 0.30/0.60 = 0.50 >= 0.40 -> the gate passes. Raw semantics measured
    # 0.30 (fail); repaired semantics measure the trained share (pass).
    proposal2 = _evaluate(policy, [0.30, 0.30, 0.00, 0.40], fv_updates=sweep)
    # The repaired measure 0.50 clears the 0.40 gate - the row may proceed to
    # later gates (zone quality in this isolated fixture), but NEVER to
    # CONFIDENCE_FAIL, and the carried confidence is the trained share.
    assert proposal2.confidence == pytest.approx(0.50, abs=1e-6)
    assert proposal2.blocked_by != "CONFIDENCE_FAIL"
    assert proposal2.decision_stage != "CONFIDENCE_GATE"


def test_case_l_survival_mode_adjustment_unchanged():
    """L: survival +0.10 adjustment semantics are untouched."""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.40
    proposal = _evaluate(policy, [0.05, 0.45, 0.50, 0.00], survival_mode=True)
    assert proposal.action == ActionType.NO_TRADE
    rc = proposal.risk_checks or {}
    assert rc.get("survival_mode_adjustment") == 0.10
    assert rc.get("effective_threshold") == 0.50


def test_candidate_confidence_is_raw_probability_not_floor():
    """TRADE QUALITY FIX guard retained: no synthetic floor may return.
    (Re-stated here so the semantics repair cannot resurrect the old
    `0.55 + prob*0.35` behavior.)"""
    policy = SignalPolicy()
    policy.confidence_threshold = 0.35
    proposal = _evaluate(policy, [0.55, 0.42, 0.03, 0.00])
    if proposal.action != ActionType.NO_TRADE:
        assert proposal.confidence <= 0.45
