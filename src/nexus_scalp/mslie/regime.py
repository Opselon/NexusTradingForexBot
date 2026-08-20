"""Market Regime feature computation for MSLIE.

Produces the regime block of the feature vector contract:

- trend_direction        (-1 .. +1)  EMA-based directional bias
- trend_strength         (0..100)    ADX-like directional strength
- volatility_state       (0..1)      ATR-normalized volatility level
- ranging_probability    (0..1)
- expansion_probability  (0..1)
- compression_probability(0..1)

CAUSALITY: only bars with ``timestamp <= decision_at`` are ever read. The
last visible bar is the most recent COMPLETED bar (never the forming bar).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from nexus_scalp.mslie.models import MarketRegimeFeatures

# =============================================================================
# CONSTANTS
# =============================================================================

EMA_FAST: int = 10
EMA_SLOW: int = 30
ADX_PERIOD: int = 14
RANGE_WINDOW: int = 40
ATR_WINDOW: int = 14

MIN_ATR: float = 0.20


# =============================================================================
# HELPERS
# =============================================================================


def _bar_times(bars: Sequence[Any]) -> list[datetime]:
    out: list[datetime] = []
    for b in bars:
        t = b.timestamp
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        else:
            t = t.astimezone(UTC)
        out.append(t)
    return out


def _arrays(bars: Sequence[Any]) -> dict[str, np.ndarray]:
    return {
        "high": np.asarray([b.high for b in bars], dtype=np.float64),
        "low": np.asarray([b.low for b in bars], dtype=np.float64),
        "close": np.asarray([b.close for b in bars], dtype=np.float64),
        "volume": np.asarray([b.tick_volume for b in bars], dtype=np.float64),
    }


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average (seed = first value)."""
    if len(values) == 0:
        return values
    out = np.empty_like(values)
    out[0] = values[0]
    alpha = 2.0 / (period + 1.0)
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = ATR_WINDOW) -> float:
    """Wilder-style ATR over the visible series (fallback to simple mean)."""
    n = len(high)
    if n < 2:
        return MIN_ATR
    tr = np.empty(n - 1)
    for i in range(1, n):
        tr[i - 1] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    if n - 1 < period:
        return float(np.mean(tr)) if len(tr) else MIN_ATR
    # simple mean of the last `period` true ranges (deterministic, matches
    # the canonical mean-TR-14 semantics of the feature engine)
    return float(np.mean(tr[-period:]))


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================================================================
# REGIME COMPUTATION
# =============================================================================


def compute_regime_features(
    bars: Sequence[Any],
    *,
    decision_at: datetime | None = None,
) -> MarketRegimeFeatures:
    """Computes the regime block from visible (closed) bars only.

    ``bars`` must be chronological completed bars. Any bar with timestamp
    after ``decision_at`` is invisible (no future leakage, INV-008).

    Returns honest defaults when history is too short (never NaN/Inf).
    """
    times = _bar_times(bars)
    if decision_at is None:
        decision_at = times[-1] if times else datetime.now(UTC)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=UTC)
    else:
        decision_at = decision_at.astimezone(UTC)

    vis_idx = [i for i, t in enumerate(times) if t <= decision_at]
    if len(vis_idx) < 6:
        return MarketRegimeFeatures(
            trend_direction=0.0,
            trend_strength=0.0,
            volatility_state=0.0,
            ranging_probability=0.5,
            expansion_probability=0.25,
            compression_probability=0.25,
            regime_label="INSUFFICIENT_HISTORY",
        )
    arr = _arrays([bars[i] for i in vis_idx])
    close = arr["close"]
    high = arr["high"]
    low = arr["low"]
    n = len(close)
    atr = _atr(high, low, close)

    # ---- trend direction (EMA cross, normalized by ATR) ---------------------
    ema_fast = _ema(close, EMA_FAST)
    ema_slow = _ema(close, EMA_SLOW)
    trend_direction = _clip(float((ema_fast[-1] - ema_slow[-1]) / max(atr, MIN_ATR)), -1.0, 1.0)

    # ---- trend strength (ADX-like, deterministic) ---------------------------
    trend_strength = 0.0
    if n >= ADX_PERIOD + 2:
        # directional movement index over the visible window
        up_move = np.diff(close)
        dm_plus: list[float] = []
        dm_minus: list[float] = []
        tr_list: list[float] = []
        for i in range(1, n):
            up = up_move[i - 1]
            down = -up
            tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
            tr_list.append(tr)
            dm_plus.append(up if (up > down and up > 0.0) else 0.0)
            dm_minus.append(down if (down > up and down > 0.0) else 0.0)
        # smoothed over the last ADX_PERIOD values
        tr_sum = sum(tr_list[-ADX_PERIOD:]) or MIN_ATR
        plus_di = 100.0 * (sum(dm_plus[-ADX_PERIOD:]) / tr_sum)
        minus_di = 100.0 * (sum(dm_minus[-ADX_PERIOD:]) / tr_sum)
        di_sum = plus_di + minus_di
        if di_sum > 0.0:
            dx = 100.0 * abs(plus_di - minus_di) / di_sum
            trend_strength = _clip(dx, 0.0, 100.0)

    # ---- volatility state (ATR vs recent range, normalized 0..1) ------------
    look = min(n, RANGE_WINDOW)
    recent_range = float(np.mean(high[-look:] - low[-look:])) or MIN_ATR
    vol_ratio = _clip(atr / recent_range, 0.0, 1.5) / 1.5  # 0..1
    volatility_state = _clip(vol_ratio, 0.0, 1.0)

    # ---- ranging / expansion / compression probabilities --------------------
    # compression: recent range shrinking vs the longer lookback
    half = max(5, look // 2)
    range_recent = float(np.mean(high[-half:] - low[-half:])) or MIN_ATR
    range_prior = (
        float(np.mean(high[-look:-half] - low[-look:-half])) if n >= look + half else range_recent
    )
    range_prior = range_prior or range_recent
    compression = _clip(1.0 - range_recent / max(range_prior, MIN_ATR), 0.0, 1.0)

    # expansion: recent range expanding AND range wider than the ATR baseline
    expansion = _clip(range_recent / max(atr * 1.2, MIN_ATR) - 0.4, 0.0, 1.0)

    # ranging: weak trend strength + stable range (not compressing/expanding)
    ranging = _clip(1.0 - trend_strength / 100.0, 0.0, 1.0) * _clip(
        1.0 - max(compression, expansion), 0.0, 1.0
    )

    # normalize the three probabilities to sum ~1 (softmax-like)
    total = compression + expansion + ranging
    if total <= 0.0:
        compression_p = expansion_p = ranging_p = 1.0 / 3.0
    else:
        compression_p = compression / total
        expansion_p = expansion / total
        ranging_p = ranging / total

    # ---- regime label -------------------------------------------------------
    if trend_strength >= 45.0:
        label = "TRENDING"
    elif expansion_p >= 0.55:
        label = "EXPANSION"
    elif compression_p >= 0.55:
        label = "COMPRESSION"
    elif ranging_p >= 0.55:
        label = "RANGING"
    else:
        label = "MIXED"

    return MarketRegimeFeatures(
        trend_direction=float(trend_direction),
        trend_strength=float(round(trend_strength, 2)),
        volatility_state=float(round(volatility_state, 4)),
        ranging_probability=float(round(ranging_p, 4)),
        expansion_probability=float(round(expansion_p, 4)),
        compression_probability=float(round(compression_p, 4)),
        regime_label=label,
    )
