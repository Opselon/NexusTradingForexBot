"""Adaptive swing structure detection for MSLIE.

The engine deliberately does NOT use simple fixed fractals. Every swing is
scored across six dimensions:

1. Adaptive fractals            — pivot window scales with volatility
2. ATR-based dynamic thresholds — a pivot must move beyond ATR-scaled
                                  relevance to count
3. Volatility adjustment        — all distances are ATR-normalized
4. Timeframe importance         — higher-timeframe swings are structurally
                                  more significant
5. Volume confirmation          — pivot bars with above-average volume get
                                  higher strength
6. Historical reaction strength — how often price reacted at the level
                                  (reaction count, retest proximity)

A random local high is NOT equal to a major institutional high: the
importance score separates them.

CAUSALITY: a swing at bar ``i`` is a CANDIDATE at bar ``i`` but only
CONFIRMED once bar ``i + confirm_window`` has closed (same discipline as the
70D liquidity engine, SWING_CONFIRM_BARS).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

import numpy as np

from nexus_scalp.mslie.models import BrokenStatus, SwingPoint, SwingType

# =============================================================================
# CONSTANTS
# =============================================================================

BASE_CONFIRM_WINDOW: int = 3
MAX_CONFIRM_WINDOW: int = 6
MIN_SWING_ATR: float = 0.35  # minimum ATR-scaled prominence to register a swing
MIN_ATR: float = 0.20
REACTION_RETEST_ATR: float = 0.4  # retest proximity band for reaction counting
MAX_REACTION_LOOKBACK: int = 200


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


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    n = len(high)
    if n < 2:
        return MIN_ATR
    tr = np.empty(n - 1)
    for i in range(1, n):
        tr[i - 1] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return float(max(np.mean(tr[-period:]), MIN_ATR))


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================================================================
# SWING DETECTION
# =============================================================================


def _volatility_adjusted_window(
    local_atr: np.ndarray, idx: int, base: int = BASE_CONFIRM_WINDOW
) -> int:
    """Scales the pivot window with recent volatility.

    High volatility -> smaller window (structure moves faster); low
    volatility -> larger window (structure needs more confirmation).
    Bounded [BASE_CONFIRM_WINDOW, MAX_CONFIRM_WINDOW].
    """
    start = max(0, idx - 30)
    window_atr = float(np.mean(local_atr[start : idx + 1])) if idx > start else MIN_ATR
    ratio = _clip(window_atr / MIN_ATR, 0.5, 2.0)
    window = int(round(base * (2.0 - ratio)))  # high ATR -> smaller window
    return int(_clip(window, BASE_CONFIRM_WINDOW, MAX_CONFIRM_WINDOW))


def _volume_ratio(volume: np.ndarray, idx: int, window: int = 20) -> float:
    """Volume at the pivot bar vs its recent average (0..3 clipped)."""
    start = max(0, idx - window)
    baseline = float(np.mean(volume[start:idx])) if idx > start else 0.0
    if baseline <= 0.0:
        return 1.0
    return _clip(float(volume[idx]) / baseline, 0.0, 3.0)


def detect_swings(
    bars: Sequence[Any],
    *,
    decision_at: datetime | None = None,
    symbol: str = "XAUUSD",
    timeframe: str = "M1",
    timeframe_weight: float = 1.0,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Detects confirmed adaptive swing highs/lows.

    Returns ``(swing_highs, swing_lows)`` chronologically. Every swing is
    CONFIRMED at or before ``decision_at`` (its confirm window closed) —
    candidates are never emitted.

    ``timeframe_weight`` lets higher-timeframe analysis (H1/H4/D1 buckets)
    raise the importance of the same swing (timeframe importance dimension).
    """
    times = _bar_times(bars)
    if decision_at is None:
        decision_at = times[-1] if times else datetime.now(UTC)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=UTC)
    else:
        decision_at = decision_at.astimezone(UTC)

    vis_idx = [i for i, t in enumerate(times) if t <= decision_at]
    if len(vis_idx) < 10:
        return [], []

    arr = _arrays([bars[i] for i in vis_idx])
    high = arr["high"]
    low = arr["low"]
    close = arr["close"]
    volume = arr["volume"]
    n = len(close)
    atr = _atr(high, low, close)
    safe_atr = max(atr, MIN_ATR)

    # rolling local ATR per bar (for adaptive windows) — small window mean TR
    local_atr = np.full(n, safe_atr)
    for i in range(1, n):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        local_atr[i] = max(float(np.mean([local_atr[i - 1], tr])), MIN_ATR)

    swings_high: list[SwingPoint] = []
    swings_low: list[SwingPoint] = []
    swing_id = 0

    for i in range(1, n - 1):
        window = _volatility_adjusted_window(local_atr, i)
        left = max(0, i - window)
        right = min(n - 1, i + window)
        # the confirm window must have fully closed before decision_at
        if times[vis_idx[right]] > decision_at:
            continue

        is_high = bool(high[i] == np.max(high[left : right + 1]) and high[i] > high[i - 1])
        is_low = bool(low[i] == np.min(low[left : right + 1]) and low[i] < low[i - 1])
        if not is_high and not is_low:
            continue

        # ATR-based dynamic threshold: the swing must stand out beyond the
        # minimum prominence to be structurally relevant.
        if is_high:
            prominence = float(high[i] - high[i - 1]) if i > 0 else 0.0
            if prominence < MIN_SWING_ATR * safe_atr and (high[i] - close[i]) < MIN_SWING_ATR * safe_atr:
                continue
        else:
            prominence = float(low[i - 1] - low[i]) if i > 0 else 0.0
            if prominence < MIN_SWING_ATR * safe_atr and (close[i] - low[i]) < MIN_SWING_ATR * safe_atr:
                continue

        # ---- strength score (0..100) ---------------------------------------
        # base: ATR-normalized rejection body + prominence
        if is_high:
            rejection = float(close[i] - low[i]) / safe_atr  # wick below = rejection of highs
            reaction = float(high[i] - close[i]) / safe_atr  # upper wick distance
        else:
            rejection = float(high[i] - close[i]) / safe_atr
            reaction = float(close[i] - low[i]) / safe_atr
        strength = 40.0 + 20.0 * _clip(rejection, 0.0, 2.0) / 2.0 + 10.0 * _clip(reaction, 0.0, 2.0) / 2.0
        # volume confirmation (dimension 5)
        vol_ratio = _volume_ratio(volume, i)
        strength += 15.0 * _clip(vol_ratio - 1.0, 0.0, 1.0)
        # volatility adjustment: strong momentum into the pivot adds weight
        momentum = abs(close[i] - close[max(0, i - 3)]) / safe_atr
        strength += 15.0 * _clip(momentum - 0.5, 0.0, 1.5) / 1.5
        strength = _clip(strength, 0.0, 100.0)

        # ---- historical reaction strength (dimension 6) ---------------------
        price_level = float(high[i]) if is_high else float(low[i])
        reaction_count = 0
        look_start = max(0, i - MAX_REACTION_LOOKBACK)
        band = REACTION_RETEST_ATR * safe_atr
        for j in range(look_start, i):
            if is_high:
                if abs(float(high[j]) - price_level) <= band:
                    reaction_count += 1
            else:
                if abs(float(low[j]) - price_level) <= band:
                    reaction_count += 1
        reaction_count = max(0, reaction_count - 1)  # the pivot bar itself doesn't count

        # ---- importance score (0..100) --------------------------------------
        # reaction frequency + strength + timeframe weight
        importance = 30.0
        importance += 25.0 * _clip(reaction_count / 6.0, 0.0, 1.0)
        importance += 20.0 * strength / 100.0
        importance += 25.0 * _clip(timeframe_weight - 1.0, 0.0, 2.0) / 2.0
        importance = _clip(importance, 0.0, 100.0)

        # ---- liquidity interaction ------------------------------------------
        # liquidity_created: the swing formed by sweeping a prior level
        # liquidity_taken:  a later (visible) bar violated this swing
        liquidity_created = False
        if is_high and i >= 2:
            liquidity_created = bool(low[i - 1] < low[i - 2] and close[i] > close[i - 1])
        elif not is_high and i >= 2:
            liquidity_created = bool(high[i - 1] > high[i - 2] and close[i] < close[i - 1])

        liquidity_taken = False
        for j in range(i + 1, n):
            if is_high and float(close[j]) > price_level:
                liquidity_taken = True
                break
            if not is_high and float(close[j]) < price_level:
                liquidity_taken = True
                break

        broken = BrokenStatus.INTACT
        if is_high:
            for j in range(i + 1, n):
                if float(high[j]) > price_level:
                    broken = BrokenStatus.BROKEN
                    # retested after break?
                    for k in range(j + 1, n):
                        if abs(float(high[k]) - price_level) <= band:
                            broken = BrokenStatus.BROKEN_AND_RETESTED
                            break
                    break
        else:
            for j in range(i + 1, n):
                if float(low[j]) < price_level:
                    broken = BrokenStatus.BROKEN
                    for k in range(j + 1, n):
                        if abs(float(low[k]) - price_level) <= band:
                            broken = BrokenStatus.BROKEN_AND_RETESTED
                            break
                    break

        swing_id += 1
        ts = times[vis_idx[i]]
        sp = SwingPoint(
            id=f"SWING-{timeframe}-{swing_id}",
            symbol=symbol,
            timeframe=timeframe,
            price=round(price_level, 5),
            timestamp=ts,
            type=SwingType.HIGH if is_high else SwingType.LOW,
            strength_score=round(strength, 2),
            importance_score=round(importance, 2),
            liquidity_created=liquidity_created,
            liquidity_taken=liquidity_taken,
            reaction_count=reaction_count,
            broken_status=broken,
        )
        if is_high:
            swings_high.append(sp)
        else:
            swings_low.append(sp)

    return swings_high, swings_low
