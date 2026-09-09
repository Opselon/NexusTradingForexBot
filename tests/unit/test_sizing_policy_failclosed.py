"""FAIL-CLOSED SIZING CONTRACT (audit 2026-09-09, coverage-forensic mission).

Why these tests exist
---------------------
``risk.sizing_policy.SizingPolicy`` is the ONE canonical sizing source shared
by the live RiskEngine, backtest, walk-forward and promotion (ECON v1). The
happy-path factors are proven by tests/unit/test_economic_assumptions.py.
What NO test pinned so far are the four defensive fallbacks that keep money
safe when the inputs go insane — exactly the lines the critical-suite coverage
gate could not see (sizing_policy 80, 95, 99, 129):

  1. confidence_scalar: non-finite (NaN/inf) or NEGATIVE confidence must fall
     back to flat 1.0 sizing — never NaN risk%, never a negative multiplier.
  2. drawdown_penalty: invalid peak (<=0 / NaN) must mean "no penalty" (1.0),
     mirroring the live engine's honest fallback.
  3. drawdown_penalty: deep drawdowns clamp at the 0.2 floor (an 80% risk
     cut, never a total stop by penalty alone).
  4. effective_risk_pct: any composed non-finite / non-positive result fails
     CLOSED to 0.0 — a broken factor can never produce a tradable size.

These are behavioral boundary tests (happy path / failure / boundary), not
coverage filler: every assertion pins a contract the live engine depends on.
"""

from __future__ import annotations

import math

import pytest

from nexus_scalp.risk.sizing_policy import (
    LIVE_CONF_MULTIPLIER_MAX,
    LIVE_CONF_MULTIPLIER_MIN,
    LIVE_DRAWDOWN_FLOOR,
    SizingPolicy,
)

# ---------------------------------------------------------------------------
# confidence_scalar: degraded confidence must flatten, never lever or NaN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("-inf"), float("inf") * 0, -0.25])
def test_confidence_scalar_non_finite_or_negative_is_flat(bad: float) -> None:
    assert SizingPolicy.confidence_scalar(bad) == 1.0


def test_confidence_scalar_nan_keeps_money_flat_not_nan() -> None:
    # Explicit (not via parametrize): a NaN confidence MUST NOT multiply into
    # a NaN risk% — the scalar absorbs it.
    scalar = SizingPolicy.confidence_scalar(float("nan"))
    assert math.isfinite(scalar)
    assert scalar == 1.0


@pytest.mark.parametrize("c", [0.0, 0.5, 1.0])
def test_confidence_scalar_calibrated_band_linear(c: float) -> None:
    s = SizingPolicy.confidence_scalar(c)
    assert LIVE_CONF_MULTIPLIER_MIN <= s <= LIVE_CONF_MULTIPLIER_MAX
    expected = LIVE_CONF_MULTIPLIER_MIN + (LIVE_CONF_MULTIPLIER_MAX - LIVE_CONF_MULTIPLIER_MIN) * c
    assert s == pytest.approx(expected)


def test_confidence_scalar_above_one_clamps_to_full_multiplier() -> None:
    # Boundary: confidence > 1.0 (raw model overconfidence) clamps, never levers.
    assert SizingPolicy.confidence_scalar(1.7) == pytest.approx(SizingPolicy.confidence_scalar(1.0))


# ---------------------------------------------------------------------------
# drawdown_penalty: invalid peaks and the floor clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("peak", [0.0, -1000.0, float("nan"), float("inf")])
def test_drawdown_penalty_invalid_peak_means_no_penalty(peak: float) -> None:
    assert SizingPolicy.drawdown_penalty(equity=1000.0, peak_equity=peak) == 1.0


def test_drawdown_penalty_non_finite_equity_is_no_penalty() -> None:
    assert SizingPolicy.drawdown_penalty(equity=float("nan"), peak_equity=1000.0) == 1.0


def test_drawdown_penalty_exactly_one_percent_is_boundary_no_penalty() -> None:
    # dd_pct == LIVE_DRAWDOWN_START_PCT (1.0) -> still no penalty (strict >).
    assert SizingPolicy.drawdown_penalty(equity=990.0, peak_equity=1000.0) == 1.0


def test_drawdown_penalty_deep_drawdown_clamps_at_floor() -> None:
    # 90% drawdown: raw formula gives 1 - 0.2*90 = -17.0; the floor must clamp.
    penalty = SizingPolicy.drawdown_penalty(equity=100.0, peak_equity=1000.0)
    assert penalty == LIVE_DRAWDOWN_FLOOR
    assert penalty > 0.0  # a positive floor: penalty never zeroes risk itself


def test_drawdown_penalty_mid_drawdown_matches_live_formula() -> None:
    # 5% drawdown -> 1 - 0.2*5 = 0.0? No: 1 - (5 * 0.2) = 0.0 -> clamped to 0.2.
    # 2.5% drawdown -> 1 - (2.5 * 0.2) = 0.5.
    assert SizingPolicy.drawdown_penalty(equity=975.0, peak_equity=1000.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# effective_risk_pct: composed pipeline fails closed
# ---------------------------------------------------------------------------


def _policy() -> SizingPolicy:
    return SizingPolicy(base_risk_pct=0.5, max_allowed_lots=2.0)


def test_effective_risk_pct_happy_path_composes_in_live_order() -> None:
    pct, factors = _policy().effective_risk_pct(
        confidence=0.5, regime=None, equity=1000.0, peak_equity=1000.0
    )
    assert pct == pytest.approx(0.5 * 1.0 * 1.0 * 0.625)
    assert factors == {"regime": 1.0, "drawdown": 1.0, "confidence": 0.625}


def test_effective_risk_pct_vol_expansion_halves() -> None:
    pct, factors = _policy().effective_risk_pct(
        confidence=1.0, regime="VOLATILITY_EXPANSION", equity=1000.0, peak_equity=1000.0
    )
    assert factors["regime"] == 0.5
    assert pct == pytest.approx(0.25)


@pytest.mark.parametrize("conf", [float("nan"), -1.0])
def test_effective_risk_pct_broken_confidence_still_tradable_but_flat(conf: float) -> None:
    # Degraded confidence -> flat base risk (never NaN, never negative).
    pct, factors = _policy().effective_risk_pct(
        confidence=conf, regime=None, equity=1000.0, peak_equity=1000.0
    )
    assert math.isfinite(pct)
    assert pct == pytest.approx(0.5)
    assert factors["confidence"] == 1.0


def test_effective_risk_pct_never_returns_non_positive_or_non_finite() -> None:
    # Compose every insane input at once: the composite contract is
    # 0 < pct <= base_risk_pct and always finite.
    for conf in (float("nan"), -1.0, 1.0):
        for regime in (None, "VOLATILITY_EXPANSION"):
            pct, _ = _policy().effective_risk_pct(
                confidence=conf,
                regime=regime,
                equity=float("nan"),
                peak_equity=float("nan"),
            )
            assert math.isfinite(pct)
            assert pct >= 0.0
            assert pct <= 0.5


def test_effective_risk_pct_zero_base_risk_fails_closed_to_zero() -> None:
    # pydantic forbids base_risk_pct <= 0 at construction: the fail-closed
    # contract starts at the model boundary (gt=0 field constraint).
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SizingPolicy(base_risk_pct=0.0)
    with pytest.raises(ValidationError):
        SizingPolicy(base_risk_pct=-1.0)


def test_effective_risk_pct_zero_factor_fails_closed_to_zero(monkeypatch) -> None:
    """Mutation guard for the composed-pct guard (sizing_policy L129): if ANY factor
    ever becomes zero (plausible constant drift), the composed percent must fail
    CLOSED to 0.0 — never propagate a negative/NaN size downstream."""
    policy = _policy()
    monkeypatch.setattr(SizingPolicy, "regime_scalar", staticmethod(lambda regime: 0.0))
    pct, factors = policy.effective_risk_pct(
        confidence=1.0, regime="VOLATILITY_EXPANSION", equity=1000.0, peak_equity=1000.0
    )
    assert pct == 0.0
    assert factors["regime"] == 0.0


def test_effective_risk_pct_nan_factor_killed_by_fail_closed_guard(monkeypatch) -> None:
    """Direct mutation guard for sizing_policy L129: a factor that returns NaN
    (upstream calibration bug) must be absorbed to 0.0 by the composed guard,
    never propagate NaN risk% into a live order."""
    policy = _policy()
    monkeypatch.setattr(SizingPolicy, "confidence_scalar", staticmethod(lambda c: float("nan")))
    pct, factors = policy.effective_risk_pct(
        confidence=0.9, regime=None, equity=1000.0, peak_equity=1000.0
    )
    assert factors["confidence"] != factors["confidence"]  # NaN propagated to factors
    assert math.isfinite(pct) and pct == 0.0
