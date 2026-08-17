"""
Candle Close Classifier
========================
The heart of the candle-intelligence module (BUG-061): converts one completed
candle into a full close-quality classification. The candle close is a GATE —
every downstream decision (entry / hold / fast-exit / no-trade / modify /
cancel) consumes this summary.

All outputs are finite in [0,1]; malformed input (NaN/Inf/non-positive range)
is rejected with `CandleCloseClass.INVALID` rather than crashing.

Deterministic: identical input geometry -> identical summary.
"""

from __future__ import annotations

import math
from datetime import datetime

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.models import (
    CandleCloseClass,
    CandleCloseSummary,
)


class CandleCloseClassifier:
    """Pure, stateless close-geometry classifier."""

    def __init__(self, config: CandleIntelligenceConfig | None = None) -> None:
        self.config = config or CandleIntelligenceConfig()

    def classify(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> CandleCloseSummary:
        """Classify one completed candle. Returns an INVALID summary on bad input."""
        invalid = self._invalid_summary(symbol, timeframe, timestamp, open_, high, low, close)
        if invalid is not None:
            return invalid

        rng = high - low
        body = abs(close - open_)
        upper_wick = max(0.0, high - max(open_, close))
        lower_wick = max(0.0, min(open_, close) - low)

        body_ratio = body / rng if rng > 1e-12 else 0.0
        upper_wick_ratio = upper_wick / rng if rng > 1e-12 else 0.0
        lower_wick_ratio = lower_wick / rng if rng > 1e-12 else 0.0
        # 0.0 = closed at the low, 1.0 = closed at the high
        close_position_in_range = (close - low) / rng if rng > 1e-12 else 0.5

        if close > open_:
            direction = "UP"
        elif close < open_:
            direction = "DOWN"
        else:
            direction = "FLAT"

        cfg = self.config

        # ---- component scores ----
        # Close strength: how decisively price closed toward the candle's own
        # edge (direction-aware). A strong close sits near the extreme in the
        # direction of the body.
        if direction == "UP":
            close_strength = close_position_in_range
        elif direction == "DOWN":
            close_strength = 1.0 - close_position_in_range
        else:  # FLAT
            close_strength = 0.0

        # Rejection: long wick AGAINST the body direction = rejection of that
        # extension. Bullish body + long upper wick = rejection at highs.
        if direction == "UP":
            rejection_score = min(1.0, upper_wick_ratio / (cfg.long_wick_ratio + 1e-12))
        elif direction == "DOWN":
            rejection_score = min(1.0, lower_wick_ratio / (cfg.long_wick_ratio + 1e-12))
        else:
            # Doji: rejection of both directions — score by total wick presence.
            rejection_score = min(1.0, (upper_wick_ratio + lower_wick_ratio))

        # Continuation: body dominant + close at the extreme in body direction.
        continuation_score = min(1.0, body_ratio * close_strength * 2.0)

        # Reversal: close against the PRIOR direction is computed later (needs
        # previous candle); here we score the shape-based reversal potential:
        # long counter-wick + small body near the opposite extreme.
        reversal_shape = min(
            1.0,
            (max(upper_wick_ratio, lower_wick_ratio)) * (1.0 - body_ratio) * 2.0,
        )
        reversal_score = reversal_shape

        # Indecision: doji-like — small body, both wicks present.
        indecision_score = min(
            1.0,
            (1.0 - body_ratio) * (upper_wick_ratio + lower_wick_ratio) * 1.5,
        )

        # Momentum decay: body small relative to range with close retracing
        # from the extreme reached during the candle (upper wick on UP, etc.).
        if direction == "UP":
            momentum_decay_score = min(1.0, upper_wick_ratio / (cfg.long_wick_ratio + 1e-12))
        elif direction == "DOWN":
            momentum_decay_score = min(1.0, lower_wick_ratio / (cfg.long_wick_ratio + 1e-12))
        else:
            momentum_decay_score = min(1.0, (upper_wick_ratio + lower_wick_ratio))

        # ---- classification ----
        close_class = self._classify(
            direction,
            body_ratio,
            upper_wick_ratio,
            lower_wick_ratio,
            close_strength,
            cfg,
        )
        close_quality = self._quality(close_class, body_ratio, close_strength)

        return CandleCloseSummary(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            range=rng,
            body=body,
            upper_wick=upper_wick,
            lower_wick=lower_wick,
            body_ratio=round(body_ratio, 6),
            upper_wick_ratio=round(upper_wick_ratio, 6),
            lower_wick_ratio=round(lower_wick_ratio, 6),
            close_position_in_range=round(close_position_in_range, 6),
            open_to_close_direction=direction,
            close_strength=round(close_strength, 6),
            rejection_score=round(rejection_score, 6),
            continuation_score=round(continuation_score, 6),
            reversal_score=round(reversal_score, 6),
            indecision_score=round(indecision_score, 6),
            momentum_decay_score=round(momentum_decay_score, 6),
            close_class=close_class,
            close_quality=close_quality,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _classify(
        self,
        direction: str,
        body_ratio: float,
        upper_wick_ratio: float,
        lower_wick_ratio: float,
        close_strength: float,
        cfg: CandleIntelligenceConfig,
    ) -> CandleCloseClass:
        # Indecision dominates when body is tiny (doji family).
        if body_ratio <= cfg.weak_body_ratio and (upper_wick_ratio + lower_wick_ratio) > body_ratio:
            return CandleCloseClass.INDECISION

        if direction == "UP":
            # Big upper wick with a bullish body = rejection / trapped longs.
            if upper_wick_ratio >= cfg.long_wick_ratio:
                if body_ratio < cfg.strong_body_ratio:
                    return CandleCloseClass.TRAPPED_BREAKOUT
                return CandleCloseClass.EXHAUSTION
            if close_strength >= cfg.continuation_threshold:
                return CandleCloseClass.BULLISH_CONTINUATION
            if close_strength >= cfg.weak_body_ratio:
                return CandleCloseClass.WEAK_CLOSE
            return CandleCloseClass.INDECISION

        if direction == "DOWN":
            if lower_wick_ratio >= cfg.long_wick_ratio:
                if body_ratio < cfg.strong_body_ratio:
                    return CandleCloseClass.TRAPPED_BREAKOUT
                return CandleCloseClass.EXHAUSTION
            if close_strength >= cfg.continuation_threshold:
                return CandleCloseClass.BEARISH_CONTINUATION
            if close_strength >= cfg.weak_body_ratio:
                return CandleCloseClass.WEAK_CLOSE
            return CandleCloseClass.INDECISION

        # FLAT (doji)
        return CandleCloseClass.INDECISION

    def _quality(
        self,
        close_class: CandleCloseClass,
        body_ratio: float,
        close_strength: float,
    ) -> str:
        if (
            close_class
            in (
                CandleCloseClass.BULLISH_CONTINUATION,
                CandleCloseClass.BEARISH_CONTINUATION,
            )
            and body_ratio >= 0.5
        ):
            return "STRONG"
        if close_class in (
            CandleCloseClass.BULLISH_CONTINUATION,
            CandleCloseClass.BEARISH_CONTINUATION,
        ):
            return "GOOD"
        if close_class in (
            CandleCloseClass.INDECISION,
            CandleCloseClass.WEAK_CLOSE,
        ):
            return "NEUTRAL"
        if close_class == CandleCloseClass.INVALID:
            return "INVALID"
        return "WEAK"

    def _invalid_summary(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> CandleCloseSummary | None:
        vals = [open_, high, low, close]
        if any(v is None or not math.isfinite(v) for v in vals):
            return self._invalid(symbol, timeframe, timestamp, open_, high, low, close)
        if high < low:
            return self._invalid(symbol, timeframe, timestamp, open_, high, low, close)
        if high - low <= self.config.min_candle_range:
            return self._invalid(symbol, timeframe, timestamp, open_, high, low, close)
        return None

    def _invalid(
        self,
        symbol: str,
        timeframe: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> CandleCloseSummary:
        return CandleCloseSummary(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            open=open_ if math.isfinite(open_) else 0.0,
            high=high if math.isfinite(high) else 0.0,
            low=low if math.isfinite(low) else 0.0,
            close=close if math.isfinite(close) else 0.0,
            range=0.0,
            body=0.0,
            upper_wick=0.0,
            lower_wick=0.0,
            body_ratio=0.0,
            upper_wick_ratio=0.0,
            lower_wick_ratio=0.0,
            close_position_in_range=0.0,
            open_to_close_direction="FLAT",
            close_strength=0.0,
            rejection_score=0.0,
            continuation_score=0.0,
            reversal_score=0.0,
            indecision_score=0.0,
            momentum_decay_score=0.0,
            close_class=CandleCloseClass.INVALID,
            close_quality="INVALID",
        )
