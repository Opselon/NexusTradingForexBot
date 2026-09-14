"""BUG-281 (2026-09-14 wave): HIGH_SPREAD_CHOP chop-boundary recalibration.

EVIDENCE (this box's production ledgers, read-only, machine copy in
docs/audit/wave_20260914/spread_calibration_20260914.json):

  live broker window (audit_signals.spread_usd, 2026-09-06..09-11, n=1406):
    p10=$0.14 p25=$0.17 p50=$0.23 p75=$0.30 p90=$0.36 p95=$0.42 p99=$0.47 max=$0.74
  guardian-killed rows (blocked_by=REGIME_GUARDIAN, n=356):
    min=$0.18 p10=$0.23 p50=$0.30 max=$0.63 — i.e. the FREEZE_ALL regime
    covered NORMAL conditions (24.2% of all decisions; 49% of the bar-plane
    census in candle_intel.db market_regimes).
  old calibration (BUG-132, 2026-05..08 100k M1 bars): p50=$0.04 p95=$0.24
    -> enter $0.25 (~p97). The broker spread regime SHIFTED ~6x; the config
    SSOT (execution_assumptions.json) already recorded both distributions and
    flags the disagreement — live evidence wins for the live engine.

Unit normalization (the class of bug this file also pins): XAUUSD point=$0.01,
so risk.max_spread_points=60 == $0.60 USD and the chop enter must be compared
in the SAME $-USD domain (classifier rounds spreads to cents, regime_
classifier.py:243). After recalibration the veto ordering is coherent:
  normal operation $0.14-$0.42
  chop FREEZE enter $0.45 (~p98 of live)  |  exit $0.30 (~p75, band $0.15)
  risk hard ceiling $0.60 (60 pts) stays ABOVE the chop boundary.
Candidate-relative gates (spread/ATR 0.18, spread/TP 0.15) express a
DIFFERENT fact (entry cost vs setup geometry) and are intentionally kept.

Pins: normal spreads (the ones the old band froze) are NO LONGER chop;
genuinely dangerous spreads (>= enter, and hysteresis-held above exit) are
STILL chop + FREEZE_ALL; boundaries exact; constants match the evidence file.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeClassifier,
    RecommendedExecutionType,
    RegimeType,
)

REPO = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
_clock = {"n": 0}


def _reset_clock() -> None:
    _clock["n"] = 0


def _tick(price: float, spread_usd: float) -> TickData:
    ts = _T0 + timedelta(seconds=_clock["n"])
    _clock["n"] += 1
    half = spread_usd / 2.0
    return TickData(symbol="XAUUSD", timestamp=ts, bid=price - half, ask=price + half, volume=1.0)


def _feed(clf: MarketRegimeClassifier, n: int, spread_usd: float, base: float = 4619.0):
    _reset_clock()
    out = []
    for k in range(n):
        # calm micro-oscillation: no trend, low rv -> spread is the only driver
        price = base + 0.05 * math.sin(k / 9.0)
        out.append(clf.classify_tick(_tick(price, spread_usd)))
    return out


def _regimes(clf, n, spread, base=4619.0):
    return [s.regime_type for s in _feed(clf, n, spread, base)]


# ---------------------------------------------------------------------------
# 1. NORMAL live spreads must NOT freeze the engine any more
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spread", [0.18, 0.23, 0.30, 0.36, 0.42])
def test_normal_live_spread_not_chop(spread: float) -> None:
    """The old band froze all of these (they were 24% of production
    decisions). At the recalibrated boundary they are routine market."""
    clf = MarketRegimeClassifier()
    assert RegimeType.HIGH_SPREAD_CHOP not in _regimes(clf, 300, spread)


# ---------------------------------------------------------------------------
# 2. Genuinely dangerous spreads stay frozen (fail-closed direction kept)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spread", [0.50, 0.63, 1.20])
def test_extreme_spread_still_chop_freeze_all(spread: float) -> None:
    clf = MarketRegimeClassifier()
    st = _feed(clf, 300, spread)
    assert st[-1].regime_type == RegimeType.HIGH_SPREAD_CHOP
    assert st[-1].recommended_execution_type == RecommendedExecutionType.FREEZE_ALL


# ---------------------------------------------------------------------------
# 3. Boundary exactness + hysteresis band
# ---------------------------------------------------------------------------


def test_chop_boundary_exact() -> None:
    clf_lo = MarketRegimeClassifier()
    assert RegimeType.HIGH_SPREAD_CHOP not in _regimes(clf_lo, 300, 0.44)
    clf_hi = MarketRegimeClassifier()
    assert RegimeType.HIGH_SPREAD_CHOP in _regimes(clf_hi, 300, 0.46)


def test_schmitt_exit_band() -> None:
    """Enter at >=$0.45, stay CHOP until spread <= $0.30 (band $0.15)."""
    clf = MarketRegimeClassifier()
    assert _regimes(clf, 60, 0.50)[-1] == RegimeType.HIGH_SPREAD_CHOP
    # mid-band: hysteresis holds the freeze (0.32 > exit 0.30)
    assert _regimes(clf, 60, 0.32)[-1] == RegimeType.HIGH_SPREAD_CHOP
    # below exit: release (unfreezing is always immediate-safe: the guard is
    # on the RELAX side only for HOLD time, not for the margin gate)
    assert _regimes(clf, 120, 0.22)[-1] != RegimeType.HIGH_SPREAD_CHOP


# ---------------------------------------------------------------------------
# 4. Constants single-source + evidence-file agreement + veto ordering
# ---------------------------------------------------------------------------


def test_classifier_defaults_match_live_calibration() -> None:
    clf = MarketRegimeClassifier()
    assert clf.spread_chop_enter == pytest.approx(0.45)
    assert clf.spread_chop_exit == pytest.approx(0.30)


def test_live_engine_wiring_matches_classifier_defaults() -> None:
    """_init_regime_classifier must not smuggle a second divergent pair of
    literals (THRESHOLD OWNERSHIP doctrine: one canonical source)."""
    import inspect

    from nexus_scalp.application import live_engine as le

    src = inspect.getsource(le.LiveEngine._init_regime_classifier)
    assert "spread_chop_enter_usd" not in src and "spread_chop_exit_usd" not in src, (
        "wiring must NOT override the spread band — the classifier defaults "
        "are the single source of truth (threshold ownership)"
    )


def test_risk_ceiling_still_above_chop_enter() -> None:
    """Veto ordering coherence: the hard risk reject (max_spread_points,
    XAUUSD point=$0.01) must stay ABOVE the chop boundary so chop fires
    first (guard, not race)."""
    from nexus_scalp.configuration.config import RiskConfig

    ceiling_usd = RiskConfig().max_spread_points * 0.01
    assert ceiling_usd >= 0.45 * 1.2, (
        f"risk ceiling {ceiling_usd} must clearly exceed chop enter 0.45"
    )


def test_calibration_evidence_file_agrees_with_code() -> None:
    ev = REPO / "docs/audit/wave_20260914/spread_calibration_20260914.json"
    assert ev.exists(), "recalibration must cite a machine-readable evidence file"
    d = json.loads(ev.read_text(encoding="utf-8"))
    assert d["live_window"]["p50"] == pytest.approx(0.23, abs=0.02)
    assert d["chosen"]["enter_usd"] == pytest.approx(MarketRegimeClassifier().spread_chop_enter)
    assert d["chosen"]["exit_usd"] == pytest.approx(MarketRegimeClassifier().spread_chop_exit)
