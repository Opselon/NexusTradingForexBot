"""
Candlestick & Chart Pattern Engine
==================================
Local-only pattern detection over completed candles (BUG-061). Detects the
29 required patterns, scores each with a raw shape fidelity AND a context
weight (trend, volatility, structure, sweep proximity, spread/ATR context),
and returns `PatternDetection` records with confidence in [0,1].

A pattern alone is never sufficient: the decision engine requires multi-factor
confirmation and a non-contradictory candle close.

Deterministic: same candle window -> same detections.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from nexus_scalp.candle_intelligence.classifier import CandleCloseClassifier
from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.models import (
    PatternDetection,
    RegimeState,
)


class Candle:
    """Lightweight, validated candle view for pattern math."""

    __slots__ = ("close", "high", "low", "open", "symbol", "timeframe", "timestamp", "volume")

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def rng(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def upper_wick(self) -> float:
        return max(0.0, self.high - max(self.open, self.close))

    @property
    def lower_wick(self) -> float:
        return max(0.0, min(self.open, self.close) - self.low)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_ratio(self) -> float:
        return self.body / self.rng

    def is_doji(self, tol: float = 0.12) -> bool:
        return self.body_ratio <= tol


class PatternContext:
    """Contextual factors used to weight every pattern's confidence."""

    __slots__ = ("spread_atr", "structure", "sweep_proximity", "trend", "volatility")

    def __init__(
        self,
        trend: float = 0.0,  # -1 bearish .. +1 bullish (from candle window)
        volatility: float = 0.5,  # 0 low .. 1 high
        structure: float = 0.5,  # 0 weak .. 1 strong
        sweep_proximity: float = 0.0,  # 0 none .. 1 sweep present
        spread_atr: float = 0.5,  # 0 tight .. 1 wide
    ) -> None:
        self.trend = trend
        self.volatility = volatility
        self.structure = structure
        self.sweep_proximity = sweep_proximity
        self.spread_atr = spread_atr


class PatternEngine:
    """Detects the required 29 patterns over a sliding window of candles."""

    #: Pattern set required by spec — (name, direction, min_candles)
    PATTERNS: ClassVar[dict[str, tuple[str, int]]] = {
        "HAMMER": ("BULLISH", 1),
        "INVERTED_HAMMER": ("BULLISH", 1),
        "HANGING_MAN": ("BEARISH", 1),
        "SHOOTING_STAR": ("BEARISH", 1),
        "MARUBOZU": ("NEUTRAL", 1),
        "BULLISH_ENGULFING": ("BULLISH", 2),
        "BEARISH_ENGULFING": ("BEARISH", 2),
        "MORNING_STAR": ("BULLISH", 3),
        "EVENING_STAR": ("BEARISH", 3),
        "GRAVESTONE_DOJI": ("BEARISH", 1),
        "DRAGONFLY_DOJI": ("BULLISH", 1),
        "STANDARD_DOJI": ("NEUTRAL", 1),
        "LONG_LEGGED_DOJI": ("NEUTRAL", 1),
        "THREE_WHITE_SOLDIERS": ("BULLISH", 3),
        "THREE_BLACK_CROWS": ("BEARISH", 3),
        "HARAMI": ("NEUTRAL", 2),
        "DARK_CLOUD_COVER": ("BEARISH", 2),
        "PIERCING_LINE": ("BULLISH", 2),
        "RISING_THREE_METHODS": ("BULLISH", 5),
        "FALLING_THREE_METHODS": ("BEARISH", 5),
        "DOUBLE_TOP": ("BEARISH", 5),
        "DOUBLE_BOTTOM": ("BULLISH", 5),
        "HEAD_AND_SHOULDERS": ("BEARISH", 5),
        "INVERSE_HEAD_AND_SHOULDERS": ("BULLISH", 5),
        "FLAG": ("NEUTRAL", 5),
        "PENNANT": ("NEUTRAL", 5),
        "WEDGE": ("NEUTRAL", 5),
        "TRIANGLE": ("NEUTRAL", 5),
        "GAP_WINDOW": ("NEUTRAL", 2),
    }

    def __init__(
        self,
        config: CandleIntelligenceConfig | None = None,
    ) -> None:
        self.config = config or CandleIntelligenceConfig()
        self._classifier = CandleCloseClassifier(self.config)

    def detect(
        self,
        candles: list[Candle],
        regime: RegimeState | None = None,
        context: PatternContext | None = None,
    ) -> list[PatternDetection]:
        """Detect all patterns present in the window (last candle = current).

        Returns detections sorted by confidence desc; empty list on malformed
        input (fewer than 2 candles, non-finite values).
        """
        if not candles or len(candles) < 2:
            return []
        if any(not _finite(c) for c in candles[-5:]):
            return []

        ctx = context or self._derive_context(candles, regime)
        out: list[PatternDetection] = []
        for name, (_dir, need) in self.PATTERNS.items():
            if len(candles) < need:
                continue
            raw = self._detect_one(name, candles, need)
            if raw <= 0.0:
                continue
            confidence = self._weight(name, raw, ctx)
            if confidence < self.config.pattern_min_confidence:
                continue
            out.append(
                PatternDetection(
                    pattern_name=name,
                    direction=_dir,
                    raw_score=round(raw, 6),
                    context_weight=round(confidence / max(raw, 1e-9), 6),
                    confidence_score=round(min(confidence, 1.0), 6),
                    requires_confirmation=_dir != "NEUTRAL",
                    reason_codes=[f"{name}_SHAPE", f"CTX_TREND_{_trend_label(ctx.trend)}"],
                )
            )
        out.sort(key=lambda p: p.confidence_score, reverse=True)
        return out

    # ------------------------------------------------------------------
    # single-pattern detectors (return raw fidelity 0..1)
    # ------------------------------------------------------------------

    def _detect_one(self, name: str, candles: list[Candle], need: int) -> float:
        cur = candles[-1]
        prev = candles[-2] if len(candles) >= 2 else None

        if name == "HAMMER":
            return _hammer(cur, bullish=True)
        if name == "INVERTED_HAMMER":
            return _hammer(cur, bullish=True, inverted=True)
        if name == "HANGING_MAN":
            return _hammer(cur, bullish=False)
        if name == "SHOOTING_STAR":
            return _hammer(cur, bullish=False, inverted=True)
        if name == "MARUBOZU":
            return _marubozu(cur)
        if name in ("BULLISH_ENGULFING", "BEARISH_ENGULFING"):
            if prev is None:
                return 0.0
            return _engulfing(cur, prev, bearish=(name == "BEARISH_ENGULFING"))
        if name in ("MORNING_STAR", "EVENING_STAR"):
            if len(candles) < 3:
                return 0.0
            return _star(candles[-3], candles[-2], cur, bearish=(name == "EVENING_STAR"))
        if name == "GRAVESTONE_DOJI":
            return _gravestone_doji(cur)
        if name == "DRAGONFLY_DOJI":
            return _dragonfly_doji(cur)
        if name == "STANDARD_DOJI":
            return _standard_doji(cur)
        if name == "LONG_LEGGED_DOJI":
            return _long_legged_doji(cur)
        if name in ("THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"):
            if len(candles) < 3:
                return 0.0
            return _three_soldiers(
                candles[-3], candles[-2], cur, bearish=(name == "THREE_BLACK_CROWS")
            )
        if name == "HARAMI":
            if prev is None:
                return 0.0
            return _harami(prev, cur)
        if name in ("DARK_CLOUD_COVER", "PIERCING_LINE"):
            if prev is None:
                return 0.0
            return _cloud_cover(prev, cur, bearish=(name == "DARK_CLOUD_COVER"))
        if name in ("RISING_THREE_METHODS", "FALLING_THREE_METHODS"):
            if len(candles) < 5:
                return 0.0
            return _three_methods(candles[-5:], bearish=(name == "FALLING_THREE_METHODS"))
        if name in ("DOUBLE_TOP", "DOUBLE_BOTTOM"):
            if len(candles) < 5:
                return 0.0
            return _double_top_bottom(candles[-5:], bearish=(name == "DOUBLE_TOP"))
        if name in ("HEAD_AND_SHOULDERS", "INVERSE_HEAD_AND_SHOULDERS"):
            if len(candles) < 5:
                return 0.0
            return _head_shoulders(candles[-5:], bearish=(name == "HEAD_AND_SHOULDERS"))
        if name in ("FLAG", "PENNANT", "WEDGE", "TRIANGLE"):
            if len(candles) < 5:
                return 0.0
            return _chart_pattern(candles[-5:], kind=name)
        if name == "GAP_WINDOW":
            if prev is None:
                return 0.0
            return _gap(prev, cur)
        return 0.0

    # ------------------------------------------------------------------
    # context weighting
    # ------------------------------------------------------------------

    def _weight(self, name: str, raw: float, ctx: PatternContext) -> float:
        """Multi-factor contextual weight: trend, volatility, structure,
        sweep proximity, spread/ATR. Favours confluence, damps noise."""
        direction = self.PATTERNS[name][0]
        w = raw

        # Trend alignment: bullish patterns score higher in uptrends, etc.
        if direction == "BULLISH":
            w *= 0.7 + 0.6 * max(0.0, ctx.trend)
        elif direction == "BEARISH":
            w *= 0.7 + 0.6 * max(0.0, -ctx.trend)
        else:  # NEUTRAL — volatility-aware
            w *= 0.8 + 0.4 * ctx.volatility

        # Volatility: too low = noise, too high = unreliable shapes.
        w *= 1.0 - 0.4 * abs(ctx.volatility - 0.5)

        # Structure strength amplifies.
        w *= 0.8 + 0.4 * ctx.structure

        # Sweep proximity: reversal patterns near a sweep are stronger.
        if direction in ("BULLISH", "BEARISH"):
            w *= 0.9 + 0.3 * ctx.sweep_proximity

        # Spread/ATR: wide spreads degrade every pattern's reliability.
        w *= 1.0 - 0.5 * ctx.spread_atr

        return max(0.0, min(1.0, w))

    def _derive_context(
        self,
        candles: list[Candle],
        regime: RegimeState | None,
    ) -> PatternContext:
        """Derives trend/vol/structure from the window (deterministic)."""
        closes = [c.close for c in candles[-10:]]
        if len(closes) >= 5:
            first, last = closes[0], closes[-1]
            rng_span = max(closes) - min(closes)
            trend = 0.0
            if rng_span > 1e-12:
                trend = max(-1.0, min(1.0, (last - first) / rng_span))
        else:
            trend = 0.0

        atr_ctx = 0.5
        vol = 0.5
        if regime is not None and regime.atr > 0:
            atr_ctx = min(1.0, regime.atr / 3.0)  # heuristic normalization
            vol = atr_ctx
        spread_atr = min(1.0, (regime.spread / max(regime.atr, 1e-9)) * 5.0) if regime else 0.5

        structure = 0.6  # deterministic base; could be refined by S/R later
        sweep = 0.0
        return PatternContext(
            trend=trend,
            volatility=vol,
            structure=structure,
            sweep_proximity=sweep,
            spread_atr=spread_atr,
        )


# ==========================================================================
# shape math (pure functions, all return 0..1 fidelity)
# ==========================================================================


def _finite(c: Candle) -> bool:
    import math

    return all(math.isfinite(v) for v in (c.open, c.high, c.low, c.close))


def _hammer(c: Candle, bullish: bool, inverted: bool = False) -> float:
    """Hammer/Hanging Man: small body at one end, long wick at the other.
    inverted=True -> shooting star / inverted hammer (long UPPER wick)."""
    if c.body_ratio > 0.35:
        return 0.0
    body_mid = (c.open + c.close) / 2.0
    if not inverted:
        # Long lower wick, small body near the top.
        if c.lower_wick <= 0:
            return 0.0
        wick_body_ratio = c.lower_wick / max(c.body, 1e-12)
        top_offset = (c.high - body_mid) / c.rng
        fidelity = min(1.0, wick_body_ratio / 2.0) * (1.0 - max(0.0, top_offset - 0.5))
    else:
        if c.upper_wick <= 0:
            return 0.0
        wick_body_ratio = c.upper_wick / max(c.body, 1e-12)
        bottom_offset = (body_mid - c.low) / c.rng
        fidelity = min(1.0, wick_body_ratio / 2.0) * (1.0 - max(0.0, bottom_offset - 0.5))
    # Bullish hammers close green; bearish hanging men close red (soft).
    if bullish and not c.bullish:
        fidelity *= 0.85
    if not bullish and not c.bearish:
        fidelity *= 0.85
    return max(0.0, min(1.0, fidelity))


def _marubozu(c: Candle) -> float:
    """No (or tiny) wicks — full-body candle."""
    wick_total = c.upper_wick + c.lower_wick
    return max(0.0, min(1.0, 1.0 - wick_total / c.rng * 4.0))


def _engulfing(cur: Candle, prev: Candle, bearish: bool) -> float:
    if bearish:
        if not (prev.bullish and cur.bearish):
            return 0.0
    elif not (prev.bearish and cur.bullish):
        return 0.0
    if cur.body <= prev.body:
        return 0.0
    # Full engulf = current body covers previous body entirely.
    if bearish:
        covers_top = cur.open >= prev.close  # opens above prev close
        covers_bottom = cur.close <= prev.open
    else:
        covers_top = cur.close >= prev.open
        covers_bottom = cur.open <= prev.close
    raw = (cur.body / prev.body - 1.0) * 2.0
    if not (covers_top and covers_bottom):
        raw *= 0.6
    return max(0.0, min(1.0, raw))


def _star(c1: Candle, c2: Candle, c3: Candle, bearish: bool) -> float:
    """Morning/Evening star: big move, small middle, confirm close."""
    if bearish:
        ok = c1.bullish and c2.body_ratio <= 0.3 and c3.bearish
        big_first = c1.body > c2.body * 2.0
        gap = c2.high < c1.close or c2.body_ratio <= 0.25
        confirm = c3.close < c1.open or c3.close < (c1.open + c1.close) / 2.0
    else:
        ok = c1.bearish and c2.body_ratio <= 0.3 and c3.bullish
        big_first = c1.body > c2.body * 2.0
        gap = c2.low > c1.close or c2.body_ratio <= 0.25
        confirm = c3.close > c1.open or c3.close > (c1.open + c1.close) / 2.0
    if not ok:
        return 0.0
    score = 0.6 + (0.2 if big_first else 0.0) + (0.2 if gap else 0.0) + (0.2 if confirm else 0.0)
    return max(0.0, min(1.0, score))


def _gravestone_doji(c: Candle) -> float:
    if not c.is_doji(0.15):
        return 0.0
    if c.upper_wick <= 0:
        return 0.0
    return min(1.0, c.upper_wick / c.rng * 2.0) * (0.5 + 0.5 * (c.close <= c.open))


def _dragonfly_doji(c: Candle) -> float:
    if not c.is_doji(0.15):
        return 0.0
    if c.lower_wick <= 0:
        return 0.0
    return min(1.0, c.lower_wick / c.rng * 2.0) * (0.5 + 0.5 * (c.close >= c.open))


def _standard_doji(c: Candle) -> float:
    if not c.is_doji(0.10):
        return 0.0
    return 1.0 if c.close == c.open else 0.7


def _long_legged_doji(c: Candle) -> float:
    if not c.is_doji(0.12):
        return 0.0
    if c.upper_wick <= 0 or c.lower_wick <= 0:
        return 0.0
    return min(1.0, (c.upper_wick + c.lower_wick) / c.rng)


def _three_soldiers(c1: Candle, c2: Candle, c3: Candle, bearish: bool) -> float:
    candles = [c1, c2, c3]
    if bearish:
        if not all(not c.bullish for c in candles):
            return 0.0
        rising_ok = c1.close > c2.close > c3.close
    else:
        if not all(c.bullish for c in candles):
            return 0.0
        rising_ok = c1.close < c2.close < c3.close
    if not rising_ok:
        return 0.0
    body_growth = c3.body >= c2.body >= c1.body * 0.7
    return 0.7 + (0.3 if body_growth else 0.0)


def _harami(prev: Candle, cur: Candle) -> float:
    # Current body fully inside previous body (opposite or neutral).
    if prev.body <= cur.body:
        return 0.0
    inside = cur.high <= prev.high and cur.low >= prev.low
    if not inside:
        return 0.0
    return min(1.0, (prev.body / cur.body - 1.0) * 0.8)


def _cloud_cover(prev: Candle, cur: Candle, bearish: bool) -> float:
    if bearish:
        if not (prev.bullish and cur.bearish):
            return 0.0
        mid = (prev.open + prev.close) / 2.0
        if cur.close >= mid:
            return 0.0
        penetration = (mid - cur.close) / max(prev.body, 1e-12)
        open_above = cur.open >= prev.close
    else:
        if not (prev.bearish and cur.bullish):
            return 0.0
        mid = (prev.open + prev.close) / 2.0
        if cur.close <= mid:
            return 0.0
        penetration = (cur.close - mid) / max(prev.body, 1e-12)
        open_above = cur.open <= prev.close
    score = min(1.0, penetration) * (1.1 if open_above else 0.8)
    return max(0.0, min(1.0, score))


def _three_methods(window: list[Candle], bearish: bool) -> float:
    """Rising/Falling Three Methods: strong candle, shallow pullbacks,
    final strong candle through the first close."""
    if len(window) < 5:
        return 0.0
    first, *mid, last = window
    if bearish:
        if first.bullish or last.bullish:
            return 0.0
        if not all(m.high <= first.high and m.low >= first.low for m in mid):
            return 0.0
        if last.close >= first.close:
            return 0.0
    else:
        if first.bearish or last.bearish:
            return 0.0
        if not all(m.high <= first.high and m.low >= first.low for m in mid):
            return 0.0
        if last.close <= first.close:
            return 0.0
    return 0.8


def _double_top_bottom(window: list[Candle], bearish: bool) -> float:
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    if bearish:
        # Two comparable highs with a dip between -> double top.
        top1, top2 = max(highs[:3]), max(highs[2:])
        if abs(top1 - top2) / max(top1, 1e-12) > 0.01:
            return 0.0
        return 0.7 if window[-1].bearish else 0.5
    else:
        bot1, bot2 = min(lows[:3]), min(lows[2:])
        if abs(bot1 - bot2) / max(bot1, 1e-12) > 0.01:
            return 0.0
        return 0.7 if window[-1].bullish else 0.5


def _head_shoulders(window: list[Candle], bearish: bool) -> float:
    if len(window) < 5:
        return 0.0
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    if bearish:
        # Left shoulder < head > right shoulder, then neckline break.
        left, head, right = highs[0], max(highs[1:4]), highs[-1]
        if not (head > left and head > right):
            return 0.0
        return 0.65 if window[-1].bearish else 0.45
    else:
        left, head, right = lows[0], min(lows[1:4]), lows[-1]
        if not (head < left and head < right):
            return 0.0
        return 0.65 if window[-1].bullish else 0.45


def _chart_pattern(window: list[Candle], kind: str) -> float:
    """Flag/Pennant/Wedge/Triangle: convergence of highs/lows (deterministic
    simplification). Returns higher fidelity for tighter convergence."""
    if len(window) < 5:
        return 0.0
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    hi_range = max(highs) - min(highs)
    lo_range = max(lows) - min(lows)
    if hi_range <= 1e-12 or lo_range <= 1e-12:
        return 0.45
    convergence = (max(highs) - min(lows)) / (hi_range + lo_range + 1e-12)
    # convergence near 1 = tight consolidation.
    fidelity = max(0.0, min(1.0, (convergence - 0.5) * 2.0)) if convergence > 0.5 else 0.0
    if fidelity <= 0.0:
        return 0.0
    # Wedge: highs and lows both slope; triangle: one side flat.
    if kind == "WEDGE":
        hi_slope = highs[-1] - highs[0]
        lo_slope = lows[-1] - lows[0]
        if hi_slope * lo_slope < 0:  # converging
            fidelity *= 1.1
    elif kind in ("TRIANGLE",):
        flat_side = min(hi_range, lo_range) / max(hi_range, lo_range, 1e-12)
        fidelity *= 0.8 + 0.4 * flat_side
    elif kind == "FLAG":
        # Sharp prior move + tight, shallow counter-move.
        prior_move = abs(window[-2].close - window[0].open)
        pullback = max(abs(c.close - window[0].close) for c in window[-3:])
        if prior_move > 0 and pullback < prior_move * 0.5:
            fidelity *= 1.2
    elif kind == "PENNANT":
        # Symmetric small convergence after a move.
        if hi_range > 0 and lo_range > 0:
            if abs(hi_range - lo_range) / (hi_range + lo_range) < 0.3:
                fidelity *= 1.15
    return max(0.0, min(1.0, fidelity))


def _gap(prev: Candle, cur: Candle) -> float:
    if cur.low > prev.high:
        return min(1.0, (cur.low - prev.high) / max(prev.rng, 1e-12))
    if cur.high < prev.low:
        return min(1.0, (prev.low - cur.high) / max(prev.rng, 1e-12))
    return 0.0


def _trend_label(trend: float) -> str:
    if trend > 0.2:
        return "UP"
    if trend < -0.2:
        return "DOWN"
    return "FLAT"
