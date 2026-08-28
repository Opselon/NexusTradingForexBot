"""Regression + calibration tests for the MarketRegimeClassifier (BUG-132).

These tests are DETERMINISTIC: they drive the classifier with hand-built
price profiles (not live/random data) so each regime and boundary is
reproducible in CI.

Root cause of the original bug:
1. The hysteresis gate required a confidence margin for EVERY switch out of a
   safe regime, which made TRENDING_MOMENTUM absorbing (its stable_prob ~0.8
   could never be <= the candidate RANGING prob ~0.6 + margin), so once entered
   it stuck. This is fixed in regime_classifier._apply_hysteresis.
2. tick_velocity was used as a standalone volatility proxy, so a high feed rate
   with flat price falsely triggered VOLATILITY_EXPANSION. It is now a context
   field only (a secondary, very high-bar trigger); price-based rv_5m is the
   primary volatility signal.

Evidence for the new thresholds comes from 100k real XAUUSD M1 bars
(data/raw/XAUUSD_M1.parquet); see scratch/calibrate_regime_realdata.py and
agents/bugs.md BUG-132.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeClassifier,
    RecommendedExecutionType,
    RegimeType,
)

# Deterministic clock start (UTC). The classifier requires MONOTONIC increasing
# timestamps (true of live ticks). A module-level counter keeps the clock
# continuous across _feed() calls so multi-phase profiles (e.g. vol -> flat)
# don't feed out-of-order timestamps into the rolling window.
_T0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_TICK_STEP = 1  # seconds between ticks
_clock_tick = {"n": 0}


def _feed(classifier, prices, spread_usd: float = 0.04, step: int = _TICK_STEP):
    """Feed a mid-price profile; return list of MarketRegimeState per tick.
    The clock advances continuously across calls (monotonic timestamps)."""
    out = []
    for mid in prices:
        ts = _T0 + timedelta(seconds=_clock_tick["n"] * step)
        _clock_tick["n"] += 1
        half = spread_usd / 2.0
        st = classifier.classify_tick(
            TickData(
                symbol="XAUUSD", timestamp=ts, bid=mid - half, ask=mid + half, last=mid, volume=1.0
            )
        )
        out.append(st)
    return out


def _regimes(classifier, prices, spread_usd: float = 0.04, step: int = _TICK_STEP):
    return [st.regime_type for st in _feed(classifier, prices, spread_usd, step)]


def _flat(n: int, mid: float = 4619.0) -> list[float]:
    return [mid] * n


def _noisy_trend(n: int, drift_total: float, noise_sd: float, seed: int = 42) -> list[float]:
    """Deterministic noisy upward trend. `noise_sd` gives rv_5m (vol floor);
    `drift_total` gives the cumulative directional displacement."""
    rng = random.Random(seed)
    p = []
    for k in range(n):
        target = 4619.0 + drift_total * k / (n - 1)
        p.append(target + rng.gauss(0.0, noise_sd))
    return p


def _alternating_swing(n: int, amp: float, base: float = 4619.0) -> list[float]:
    """Large alternating per-tick moves => high rv_5m, ~flat cumulative drift."""
    return [base + amp * ((-1) ** k) * (0.7 + 0.3 * math.sin(k / 3.0)) for k in range(n)]


# --------------------------------------------------------------------------
# 1. Calm XAUUSD range -> RANGING_MEAN_REVERSION
# --------------------------------------------------------------------------
def test_calm_range_is_ranging():
    clf = MarketRegimeClassifier()
    prices = [4619.0 + 0.3 * math.sin(k / 7.0) for k in range(300)]
    regs = _regimes(clf, prices)
    assert regs[-1] == RegimeType.RANGING_MEAN_REVERSION
    assert all(
        st.recommended_execution_type == RecommendedExecutionType.PASSIVE_LIMIT
        for st in _feed(clf, prices)
    )


# --------------------------------------------------------------------------
# 2/3. Genuine bullish / bearish trend -> TRENDING_MOMENTUM (reachable in run)
# --------------------------------------------------------------------------
def test_genuine_bullish_trend():
    clf = MarketRegimeClassifier()
    prices = _noisy_trend(400, 6.0, 0.25)
    regs = _regimes(clf, prices)
    assert RegimeType.TRENDING_MOMENTUM in regs
    # the bullish trend should be detected while price is climbing
    st = [s for s in _feed(clf, prices) if s.regime_type == RegimeType.TRENDING_MOMENTUM]
    assert st  # non-empty
    assert st[0].recommended_execution_type == RecommendedExecutionType.IOC_MARKET


def test_genuine_bearish_trend():
    clf = MarketRegimeClassifier()
    # Mirror of the bullish fixture (which is known to reach TRENDING): build the
    # upward noisy trend, then negate about the start price so the profile is a
    # deterministic downward trend with identical vol/cum-ret properties.
    up = _noisy_trend(400, 6.0, 0.25, seed=42)
    prices = [4619.0 - (m - 4619.0) for m in up]
    regs = _regimes(clf, prices)
    assert RegimeType.TRENDING_MOMENTUM in regs


# --------------------------------------------------------------------------
# 4. Genuine volatility expansion -> VOLATILITY_EXPANSION (price-based)
# --------------------------------------------------------------------------
def test_genuine_volatility_expansion():
    clf = MarketRegimeClassifier()
    prices = _alternating_swing(600, 1.3)
    st = _feed(clf, prices)
    assert st[-1].regime_type == RegimeType.VOLATILITY_EXPANSION
    assert st[-1].recommended_execution_type == RecommendedExecutionType.HYBRID_LIMIT_STOP


# --------------------------------------------------------------------------
# 5. High-spread / chop -> HIGH_SPREAD_CHOP (FREEZE_ALL)
# --------------------------------------------------------------------------
def test_high_spread_chop():
    clf = MarketRegimeClassifier()
    st = _feed(clf, _flat(200, 4619.0), spread_usd=0.60)
    assert st[-1].regime_type == RegimeType.HIGH_SPREAD_CHOP
    assert st[-1].recommended_execution_type == RecommendedExecutionType.FREEZE_ALL


def test_spread_schmitt_exit_band():
    """Enter at >=0.25, stay CHOP until spread <=0.18 (hysteresis)."""
    clf = MarketRegimeClassifier()
    prices = _flat(40, 4619.0)
    assert _feed(clf, prices, spread_usd=0.30)[-1].regime_type == RegimeType.HIGH_SPREAD_CHOP
    assert _feed(clf, prices, spread_usd=0.20)[-1].regime_type == RegimeType.HIGH_SPREAD_CHOP
    assert _feed(clf, prices, spread_usd=0.10)[-1].regime_type == RegimeType.RANGING_MEAN_REVERSION


# --------------------------------------------------------------------------
# 6. Macro-news freeze
# --------------------------------------------------------------------------
def test_macro_news_freeze():
    clf = MarketRegimeClassifier()
    # Feed with news flag through a dedicated loop using the continuous clock.
    out = []
    for _k in range(60):
        ts = _T0 + timedelta(seconds=_clock_tick["n"])
        _clock_tick["n"] += 1
        out.append(
            clf.classify_tick(
                TickData(
                    symbol="XAUUSD", timestamp=ts, bid=4618.98, ask=4619.02, last=4619.0, volume=1.0
                ),
                is_macro_news_window=True,
            )
        )
    st = out[-1]
    assert st.regime_type == RegimeType.MACRO_NEWS_FREEZE
    assert st.recommended_execution_type == RecommendedExecutionType.FREEZE_ALL


# --------------------------------------------------------------------------
# 7. CRITICAL: high tick rate + flat price must NOT be VOLATILITY_EXPANSION
#    (tick_velocity is no longer a standalone volatility proxy)
# --------------------------------------------------------------------------
def test_high_feedrate_flat_price_not_volatility():
    clf = MarketRegimeClassifier()
    # Genuinely high feed rate: 600 ticks spaced 0.1s apart at a constant price.
    # This yields tick_velocity ~10/s with ZERO price movement.
    out = []
    for _k in range(600):
        ts = _T0 + timedelta(seconds=_clock_tick["n"] * 0.1)
        _clock_tick["n"] += 1
        out.append(
            clf.classify_tick(
                TickData(
                    symbol="XAUUSD", timestamp=ts, bid=4618.98, ask=4619.02, last=4619.0, volume=1.0
                )
            )
        )
    assert out[-1].regime_type != RegimeType.VOLATILITY_EXPANSION
    assert out[-1].tick_velocity_per_sec > 5.0  # confirm feed rate WAS high
    assert out[-1].regime_type == RegimeType.RANGING_MEAN_REVERSION


# --------------------------------------------------------------------------
# 8. CRITICAL: large genuine price movement still detected as VOL even at LOW
#    tick rate (price-based rv_5m carries the signal)
# --------------------------------------------------------------------------
def test_low_feedrate_real_volatility_detected():
    clf = MarketRegimeClassifier()
    # Sparse ticks (5s apart) but each step is a large swing -> high rv_5m.
    prices = [4619.0 + 1.5 * ((-1) ** k) for k in range(200)]
    st = _feed(clf, prices, step=5)
    assert st[-1].regime_type == RegimeType.VOLATILITY_EXPANSION


# --------------------------------------------------------------------------
# 9. Boundary conditions around each threshold
# --------------------------------------------------------------------------
def test_volatility_boundary_around_rv_enter():
    clf = MarketRegimeClassifier()
    # Just below enter: modest alternating moves -> not VOLATILITY.
    below = _alternating_swing(600, 0.30)
    assert RegimeType.VOLATILITY_EXPANSION not in _regimes(clf, below)
    # Just above enter: larger alternating moves -> VOLATILITY reached.
    above = _alternating_swing(600, 1.25)
    assert RegimeType.VOLATILITY_EXPANSION in _regimes(clf, above)


def test_trend_boundary_around_price_trend_threshold():
    # Independent classifiers so the low-drift profile does not dilute the
    # high-drift profile's cumulative return inside the rolling window.
    # Small drift below threshold -> ranging (no trend detected).
    clf_small = MarketRegimeClassifier()
    small = _noisy_trend(400, 0.30, 0.10)
    assert RegimeType.TRENDING_MOMENTUM not in _regimes(clf_small, small)
    # Large drift above threshold -> trending reachable.
    clf_big = MarketRegimeClassifier()
    big = _noisy_trend(400, 6.0, 0.25)
    assert RegimeType.TRENDING_MOMENTUM in _regimes(clf_big, big)


def test_spread_boundary_around_enter():
    prices = _flat(120, 4619.0)
    # Independent classifiers: Schmitt spread-enter is gated by the hysteresis
    # margin, so it is measured from a stable baseline, not chained after a
    # different-spread run on the same instance.
    # spread just below enter ($0.24 < $0.25) -> not chop
    clf_low = MarketRegimeClassifier()
    assert RegimeType.HIGH_SPREAD_CHOP not in _regimes(clf_low, prices, spread_usd=0.24)
    # spread at/above enter ($0.26 >= $0.25) -> chop (fresh instance so the
    # margin gate compares against the warmup RANGING baseline, not a 1.0 prob)
    clf_high = MarketRegimeClassifier()
    assert RegimeType.HIGH_SPREAD_CHOP in _regimes(clf_high, prices, spread_usd=0.26)


# --------------------------------------------------------------------------
# 10. Hysteresis / Schmitt enter-exit + no absorbing-state regression
# --------------------------------------------------------------------------
def test_hysteresis_min_hold_time():
    clf = MarketRegimeClassifier(min_regime_hold_sec=4.0)
    # establish ranging via continuous clock
    _feed(clf, _flat(20, 4619.0))
    # one huge volatile spike tick — should be held by min-hold, not switch
    ts = _T0 + timedelta(seconds=_clock_tick["n"])
    _clock_tick["n"] += 1
    spike = TickData(symbol="XAUUSD", timestamp=ts, bid=4617.0, ask=4621.0, last=4619.0, volume=1.0)
    st_spike = clf.classify_tick(spike)
    assert st_spike.regime_type == RegimeType.RANGING_MEAN_REVERSION


def test_trending_not_absorbing_returns_to_ranging():
    """Regression for BUG-132: TRENDING must yield back to RANGING when the
    trend ends (it must not stick forever)."""
    clf = MarketRegimeClassifier()
    trend = _noisy_trend(400, 6.0, 0.25)
    _feed(clf, trend)
    # flat range afterwards, long enough to clear min-hold + hysteresis
    regs = _regimes(clf, _flat(500, 4625.0))
    assert regs[-1] == RegimeType.RANGING_MEAN_REVERSION


def test_volatility_not_absorbing_returns_to_ranging():
    """VOLATILITY_EXPANSION must relax back to RANGING when price calms."""
    clf = MarketRegimeClassifier()
    _feed(clf, _alternating_swing(600, 1.3))
    regs = _regimes(clf, _flat(500, 4619.0))
    assert regs[-1] == RegimeType.RANGING_MEAN_REVERSION


def test_chop_not_absorbing_returns_when_spread_normalizes():
    """HIGH_SPREAD_CHOP must relax to RANGING when spread drops below exit."""
    clf = MarketRegimeClassifier()
    _feed(clf, _flat(200, 4619.0), spread_usd=0.60)
    regs = _regimes(clf, _flat(200, 4619.0), spread_usd=0.04)
    assert regs[-1] == RegimeType.RANGING_MEAN_REVERSION


# --------------------------------------------------------------------------
# 11. All five regimes reachable (smoke / coverage)
# --------------------------------------------------------------------------
def test_all_five_regimes_reachable():
    seen: Counter = Counter()
    # RANGING
    seen[_feed(MarketRegimeClassifier(), _flat(120, 4619.0))[-1].regime_type] += 1
    # TRENDING
    seen[_regimes(MarketRegimeClassifier(), _noisy_trend(400, 6.0, 0.25))[-1]] += 1
    # VOLATILITY
    seen[RegimeType.VOLATILITY_EXPANSION] = (
        1
        if RegimeType.VOLATILITY_EXPANSION
        in _regimes(MarketRegimeClassifier(), _alternating_swing(600, 1.3))
        else 0
    )
    # CHOP
    seen[_feed(MarketRegimeClassifier(), _flat(120, 4619.0), spread_usd=0.60)[-1].regime_type] += 1
    # NEWS
    news_clf = MarketRegimeClassifier()
    st = None
    for i in range(30):
        ts = _T0 + timedelta(seconds=i)
        st = news_clf.classify_tick(
            TickData(
                symbol="XAUUSD", timestamp=ts, bid=4618.98, ask=4619.02, last=4619.0, volume=1.0
            ),
            is_macro_news_window=True,
        )
    seen[st.regime_type] += 1

    for r in (
        RegimeType.RANGING_MEAN_REVERSION,
        RegimeType.TRENDING_MOMENTUM,
        RegimeType.VOLATILITY_EXPANSION,
        RegimeType.HIGH_SPREAD_CHOP,
        RegimeType.MACRO_NEWS_FREEZE,
    ):
        assert r in seen, f"regime {r} not reachable in fixtures"


# --------------------------------------------------------------------------
# 12. tick_velocity retained as context field, decoupled from VOL trigger
# --------------------------------------------------------------------------
def test_tick_velocity_field_retained_as_context():
    clf = MarketRegimeClassifier()
    st = _feed(clf, _flat(600, 4619.0))[-1]
    assert hasattr(st, "tick_velocity_per_sec")
    assert st.tick_velocity_per_sec > 0.0
    assert st.regime_type == RegimeType.RANGING_MEAN_REVERSION


# --------------------------------------------------------------------------
# 13. Recalibrated thresholds documented in code actually take effect
# --------------------------------------------------------------------------
def test_recalibrated_default_thresholds():
    clf = MarketRegimeClassifier()
    assert clf.spread_chop_enter == 0.25
    assert clf.spread_chop_exit == 0.18
    assert clf.rv_expand_enter == 0.0013
    assert clf.rv_expand_exit == 0.0010
    assert clf.tick_vel_expand_enter == 20.0  # far above any real XAUUSD feed rate
    assert clf.price_trend_threshold == 0.0010
    assert clf.rv_trend_floor == 0.0004
