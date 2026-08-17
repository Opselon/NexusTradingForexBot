"""Candle Intelligence tests — classifier + patterns (BUG-061)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.candle_intelligence.classifier import CandleCloseClassifier
from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.models import CandleCloseClass
from nexus_scalp.candle_intelligence.patterns import Candle, PatternContext, PatternEngine


def t(i: int = 0) -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC) + timedelta(minutes=i)


@pytest.fixture()
def classifier() -> CandleCloseClassifier:
    return CandleCloseClassifier(CandleIntelligenceConfig())


# ---------------------------------------------------------------------------
# classifier
# ---------------------------------------------------------------------------


def test_strong_bullish_continuation(classifier: CandleCloseClassifier) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4410.0, 4399.5, 4409.0)
    assert s.close_class == CandleCloseClass.BULLISH_CONTINUATION
    assert s.close_quality in ("STRONG", "GOOD")
    assert s.body_ratio > 0.8
    assert s.close_position_in_range > 0.9
    assert s.continuation_score > s.rejection_score


def test_bearish_close_low_range(classifier: CandleCloseClassifier) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4410.0, 4410.5, 4400.0, 4400.5)
    assert s.close_class == CandleCloseClass.BEARISH_CONTINUATION
    assert s.open_to_close_direction == "DOWN"


def test_indecision_doji(classifier: CandleCloseClassifier) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4405.0, 4408.0, 4402.0, 4405.1)
    assert s.close_class == CandleCloseClass.INDECISION
    assert s.indecision_score > 0.5


def test_exhaustion_long_upper_wick(classifier: CandleCloseClassifier) -> None:
    # Big bull body but a very long upper wick = exhaustion at highs.
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4412.0, 4399.0, 4405.0)
    assert s.close_class in (CandleCloseClass.EXHAUSTION, CandleCloseClass.TRAPPED_BREAKOUT)
    assert s.upper_wick_ratio > 0.4
    assert s.rejection_score > 0.5


def test_invalid_nan_rejected(classifier: CandleCloseClassifier) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), float("nan"), 4410.0, 4400.0, 4405.0)
    assert s.close_class == CandleCloseClass.INVALID
    assert s.close_quality == "INVALID"


def test_invalid_high_below_low(classifier: CandleCloseClassifier) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4405.0, 4400.0, 4410.0, 4405.0)
    assert s.close_class == CandleCloseClass.INVALID


def test_all_ratios_bounded(classifier: CandleCloseClassifier) -> None:
    for _ in range(20):
        o = 4400.0 + _ * 0.1
        h = o + 5.0
        l = o - 5.0
        c = o + (1 if _ % 2 else -1)
        s = classifier.classify("XAUUSD", "M1", t(_), o, h, l, c)
        for v in (
            s.body_ratio,
            s.upper_wick_ratio,
            s.lower_wick_ratio,
            s.close_position_in_range,
            s.close_strength,
            s.rejection_score,
            s.continuation_score,
            s.reversal_score,
            s.indecision_score,
            s.momentum_decay_score,
        ):
            assert 0.0 <= v <= 1.0
            assert math.isfinite(v)


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------


def _c(o: float, h: float, l: float, c: float, i: int = 0) -> Candle:
    return Candle("XAUUSD", "M1", t(i), o, h, l, c, volume=100)


@pytest.fixture()
def engine() -> PatternEngine:
    return PatternEngine(CandleIntelligenceConfig())


def test_hammer_detected(engine: PatternEngine) -> None:
    # Long lower wick + small body near the top = hammer.
    candles = [_c(4400.0, 4402.0, 4390.0, 4401.0, 0), _c(4401.0, 4403.0, 4391.0, 4402.0, 1)]
    det = engine.detect(candles)
    names = [p.pattern_name for p in det]
    assert "HAMMER" in names or "DRAGONFLY_DOJI" in names


def test_engulfing_detected(engine: PatternEngine) -> None:
    candles = [
        _c(4405.0, 4408.0, 4402.0, 4403.0, 0),  # bearish
        _c(4402.0, 4410.0, 4401.0, 4409.0, 1),  # bullish engulfs
    ]
    det = engine.detect(candles)
    names = [p.pattern_name for p in det]
    assert "BULLISH_ENGULFING" in names


def test_doji_family(engine: PatternEngine) -> None:
    candles = [_c(4400.0, 4405.0, 4395.0, 4400.0, 0), _c(4400.0, 4404.0, 4396.0, 4400.0, 1)]
    det = engine.detect(candles)
    names = [p.pattern_name for p in det]
    assert any("DOJI" in n for n in names)


def test_all_29_pattern_keys(engine: PatternEngine) -> None:
    assert len(engine.PATTERNS) == 29
    required = {
        "HAMMER",
        "INVERTED_HAMMER",
        "HANGING_MAN",
        "SHOOTING_STAR",
        "MARUBOZU",
        "BULLISH_ENGULFING",
        "BEARISH_ENGULFING",
        "MORNING_STAR",
        "EVENING_STAR",
        "GRAVESTONE_DOJI",
        "DRAGONFLY_DOJI",
        "STANDARD_DOJI",
        "LONG_LEGGED_DOJI",
        "THREE_WHITE_SOLDIERS",
        "THREE_BLACK_CROWS",
        "HARAMI",
        "DARK_CLOUD_COVER",
        "PIERCING_LINE",
        "RISING_THREE_METHODS",
        "FALLING_THREE_METHODS",
        "DOUBLE_TOP",
        "DOUBLE_BOTTOM",
        "HEAD_AND_SHOULDERS",
        "INVERSE_HEAD_AND_SHOULDERS",
        "FLAG",
        "PENNANT",
        "WEDGE",
        "TRIANGLE",
        "GAP_WINDOW",
    }
    assert set(engine.PATTERNS.keys()) == required


def test_context_weight_boost(engine: PatternEngine) -> None:
    candles = [
        _c(4405.0, 4408.0, 4402.0, 4403.0, 0),
        _c(4402.0, 4410.0, 4401.0, 4409.0, 1),
    ]
    strong = PatternContext(
        trend=0.8, volatility=0.4, structure=0.9, sweep_proximity=0.8, spread_atr=0.1
    )
    weak = PatternContext(
        trend=-0.8, volatility=0.9, structure=0.2, sweep_proximity=0.0, spread_atr=0.9
    )
    det_strong = engine.detect(candles, context=strong)
    det_weak = engine.detect(candles, context=weak)
    eng = [p for p in det_strong if p.pattern_name == "BULLISH_ENGULFING"]
    if eng:
        sc = eng[0].confidence_score
        wk = [p for p in det_weak if p.pattern_name == "BULLISH_ENGULFING"]
        if wk:
            assert sc >= wk[0].confidence_score
