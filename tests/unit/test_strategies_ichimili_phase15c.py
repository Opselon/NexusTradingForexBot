"""
Unit Tests - Seedable Ichimoku (Ichimili) Strategies (PHASE 15C)
===============================================================
Verifies:

1. Ichimoku line math matches the Pine reference (donchian/tenkan/kijun/
   span A / span B, displacement handling).
2. `IchimiliFinalStrategy`: displaced-visible-kumo break + rising/falling
   future cloud + ONE-IN-A-ROW alternation.
3. `IchimiliSpacedStrategy`: current-kumo close break + span momentum +
   min-candles-between-signals gap rule.
4. Registration + deterministic content-addressed candidates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.strategies import (
    BUILTIN_STRATEGIES,
    STRATEGY_ID_FINAL,
    STRATEGY_ID_SPACED,
    IchimiliFinalStrategy,
    IchimiliSpacedStrategy,
    builtin_candidates,
    make_candidate,
)
from nexus_scalp.strategies.base import donchian_mid
from nexus_scalp.strategies.ichimoku import _ichimoku_lines


class _Bar:
    def __init__(self, o, h, l, c, i):
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.tick_volume = 100
        self.timestamp = datetime(2026, 8, 17, 0, 0, tzinfo=UTC) + timedelta(minutes=i)


def _mk_bars(o, h, l, c):
    return [_Bar(o[i], h[i], l[i], c[i], i) for i in range(len(o))]


def _slow_bull_series(n=120):
    """A clear uptrend: higher highs/lows, closes near highs."""
    opens, highs, lows, closes = [], [], [], []
    price = 2000.0
    for i in range(n):
        opens.append(price + i * 0.1)
        highs.append(opens[-1] + 0.8)
        lows.append(opens[-1] - 0.3)
        closes.append(opens[-1] + 0.5)
        price = closes[-1]
    return _mk_bars(opens, highs, lows, closes)


def _slow_bear_series(n=120):
    """A clear downtrend: lower lows, closes near lows."""
    opens, highs, lows, closes = [], [], [], []
    price = 2100.0
    for i in range(n):
        opens.append(price - i * 0.1)
        highs.append(opens[-1] + 0.3)
        lows.append(opens[-1] - 0.8)
        closes.append(opens[-1] - 0.5)
        price = closes[-1]
    return _mk_bars(opens, highs, lows, closes)


# ---------------------------------------------------------------------------
# 1. Indicator math
# ---------------------------------------------------------------------------


def test_donchian_mid_basic() -> None:
    highs = [10.0, 11.0, 12.0, 13.0]
    lows = [9.0, 9.5, 10.0, 10.5]
    # window [9, 12] -> mid of (12, 9) = 10.5
    assert donchian_mid(highs, lows, 3, 2) == (12.0 + 9.0) / 2.0
    # window [12, 13] highs + [10, 10.5] lows (last 2 bars ending at index 3)
    # -> mid of (13.0, 10.0) = 11.5
    assert donchian_mid(highs, lows, 2, 3) == (13.0 + 10.0) / 2.0


def test_ichimoku_lines_match_pine() -> None:
    bars = _slow_bull_series(80)
    _opens, highs, lows, _closes = (
        [b.open for b in bars],
        [b.high for b in bars],
        [b.low for b in bars],
        [b.close for b in bars],
    )
    conv, base, lead1, lead2 = _ichimoku_lines(highs, lows)
    _n = len(bars)
    # conversion(9) at i=50: donchian over bars 42..50
    expected_conv = (max(highs[42:51]) + min(lows[42:51])) / 2.0
    assert conv[50] == pytest.approx(expected_conv)
    # base(26)
    expected_base = (max(highs[25:51]) + min(lows[25:51])) / 2.0
    assert base[50] == pytest.approx(expected_base)
    # span A = (conv + base) / 2
    assert lead1[50] == pytest.approx((conv[50] + base[50]) / 2.0)
    # span B(52)
    expected_lead2 = (max(highs[0:52]) + min(lows[0:52])) / 2.0
    assert lead2[51] == pytest.approx(expected_lead2)


# ---------------------------------------------------------------------------
# 2. Final variant (displaced kumo + alternation)
# ---------------------------------------------------------------------------


def test_final_variant_emits_buy_in_uptrend() -> None:
    strat = IchimiliFinalStrategy()
    bars = _slow_bull_series(160)
    signals = strat.evaluate(bars)
    buys = [s for s in signals if s.direction == "BUY"]
    assert buys, "uptrend should produce at least one BUY"
    # Alternation: never two buys in a row, never two sells in a row.
    from itertools import pairwise

    for a, b in pairwise(signals):
        assert a.direction != b.direction, "signals must alternate"


def test_final_variant_emits_sell_in_downtrend() -> None:
    strat = IchimiliFinalStrategy()
    bars = _slow_bear_series(160)
    signals = strat.evaluate(bars)
    sells = [s for s in signals if s.direction == "SELL"]
    assert sells, "downtrend should produce at least one SELL"


def test_final_variant_respects_displacement_lookback() -> None:
    """Signals cannot fire before `displacement - 1` bars of history exist."""
    strat = IchimiliFinalStrategy()
    bars = _slow_bull_series(30)  # fewer than displacement + warmup
    signals = strat.evaluate(bars)
    for s in signals:
        assert s.bar_index >= strat.displacement - 1


# ---------------------------------------------------------------------------
# 3. Spaced variant (min gap between signals)
# ---------------------------------------------------------------------------


def test_spaced_variant_enforces_min_gap() -> None:
    strat = IchimiliSpacedStrategy(min_candles_between_signals=6)
    bars = _slow_bull_series(120)
    signals = strat.evaluate(bars)
    assert signals
    last_idx: int | None = None
    for s in signals:
        if last_idx is not None:
            assert s.bar_index - last_idx >= 6, "signals must respect the minimum gap"
        last_idx = s.bar_index


def test_spaced_variant_gap_is_parameterizable() -> None:
    tight = IchimiliSpacedStrategy(min_candles_between_signals=1)
    loose = IchimiliSpacedStrategy(min_candles_between_signals=20)
    bars = _slow_bull_series(120)
    tight_signals = tight.evaluate(bars)
    loose_signals = loose.evaluate(bars)
    assert len(tight_signals) >= len(loose_signals)
    assert loose_signals, "loose gap should still produce signals in a strong trend"


def test_spaced_variant_emits_sell_in_downtrend() -> None:
    strat = IchimiliSpacedStrategy()
    bars = _slow_bear_series(120)
    signals = strat.evaluate(bars)
    assert any(s.direction == "SELL" for s in signals)


# ---------------------------------------------------------------------------
# 4. Registration & candidates
# ---------------------------------------------------------------------------


def test_builtin_registration() -> None:
    assert STRATEGY_ID_FINAL in BUILTIN_STRATEGIES
    assert STRATEGY_ID_SPACED in BUILTIN_STRATEGIES


def test_candidates_deterministic_and_content_addressed() -> None:
    cands = builtin_candidates()
    ids = {c.strategy_id for c in cands}
    assert STRATEGY_ID_FINAL in ids
    assert STRATEGY_ID_SPACED in ids
    for c in cands:
        assert c.strategy_version == c.canonical_version()
        assert c.is_version_consistent()
        assert c.lifecycle.value == "DISCOVERED"


def test_candidate_version_changes_with_definition() -> None:
    a = IchimiliFinalStrategy()
    b = IchimiliFinalStrategy(conversion_periods=12)  # different definition
    ca = make_candidate(a)
    cb = make_candidate(b)
    assert ca.strategy_version != cb.strategy_version
    # Same definition -> same version (determinism).
    ca2 = make_candidate(IchimiliFinalStrategy())
    assert ca.strategy_version == ca2.strategy_version


def test_candidate_entry_logic_describes_conditions() -> None:
    cand = make_candidate(IchimiliFinalStrategy())
    assert cand.entry_logic["bull_condition"].startswith("candle_body_above")
    assert cand.entry_logic["variant"] == "final"
    spaced = make_candidate(IchimiliSpacedStrategy())
    assert spaced.entry_logic["variant"] == "spaced"
    assert "min_candles_between_signals" in spaced.entry_logic
