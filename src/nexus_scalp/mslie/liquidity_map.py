"""Liquidity Map for MSLIE — buy-side / sell-side zone detection + ranking.

Detects and ranks liquidity zones:

BUY SIDE (BSL)   — equal highs, previous highs, double tops, range highs,
                   session highs
SELL SIDE (SSL)  — equal lows, previous lows, double bottoms, range lows,
                   session lows

Every zone carries a strength score, timeframe, age, test count, distance
from price and a probability-as-target rank (LOW / MEDIUM / HIGH / EXTREME).

CAUSALITY: only bars closed at/before the decision timestamp are visible
(INV-008). Zones are derived from swing points + session/range geometry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

import numpy as np

from nexus_scalp.mslie.models import LiquidityRank, LiquidityZone, ZoneSide

# =============================================================================
# CONSTANTS
# =============================================================================

EQUAL_TOLERANCE_ATR: float = 0.15  # two highs within this ATR band = equal highs
DOUBLE_TOP_GAP_BARS: int = 8  # min bars between two equal highs to count as double top
RANGE_LOOKBACK: int = 60
MIN_ATR: float = 0.20
MAX_ZONES: int = 12
TEST_PROXIMITY_ATR: float = 0.3


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
# ZONE BUILDERS
# =============================================================================


def _session_high_low(
    high: np.ndarray, low: np.ndarray, times: list[datetime], n: int
) -> tuple[float | None, float | None]:
    """Current completed session's high/low (same UTC session semantics as
    the 50D features: tokyo [0,8), london [7,15), ny [13,21))."""
    if n == 0:
        return None, None
    hour = times[n - 1].hour
    if hour < 8:
        start_h = 0
    elif hour < 15:
        start_h = 7
    elif hour < 21:
        start_h = 13
    else:
        start_h = 21
    start_idx = 0
    for i in range(n - 1, -1, -1):
        if times[i].hour < start_h or (times[i].day != times[n - 1].day):
            start_idx = i + 1
            break
    if start_idx >= n:
        return None, None
    return float(np.max(high[start_idx:])), float(np.min(low[start_idx:]))


def _equal_levels(
    high: np.ndarray,
    low: np.ndarray,
    side: str,
    atr: float,
    min_gap_bars: int = DOUBLE_TOP_GAP_BARS,
) -> list[tuple[float, str]]:
    """Finds equal-high / equal-low clusters (double tops/bottoms).

    Returns ``(price, source_label)`` pairs — e.g. ("EQH", "DOUBLE_TOP").
    """
    tol = atr * EQUAL_TOLERANCE_ATR
    levels: list[tuple[float, str]] = []
    if side == "high":
        candidates = [(float(high[i]), i) for i in range(len(high))]
        candidates.sort(reverse=True)
        used: list[int] = []
        for price, idx in candidates:
            # cluster any other extreme within tolerance
            cluster = [j for j, _ in enumerate(high) if abs(float(high[j]) - price) <= tol]
            cluster = [j for j in cluster if j != idx]
            if not cluster:
                continue
            # double top requires bars reasonably separated
            far = max(abs(j - idx) for j in cluster)
            if far >= min_gap_bars:
                if idx not in used:
                    levels.append((price, "DOUBLE_TOP" if far >= min_gap_bars else "EQH"))
                    used.append(idx)
                    used.extend(cluster)
        return levels
    candidates = [(float(low[i]), i) for i in range(len(low))]
    candidates.sort()
    used = []
    for price, idx in candidates:
        cluster = [j for j, _ in enumerate(low) if abs(float(low[j]) - price) <= tol]
        cluster = [j for j in cluster if j != idx]
        if not cluster:
            continue
        far = max(abs(j - idx) for j in cluster)
        if far >= min_gap_bars and idx not in used:
            levels.append((price, "DOUBLE_BOTTOM" if far >= min_gap_bars else "EQL"))
            used.append(idx)
            used.extend(cluster)
    return levels


def _tests_at_level(
    high: np.ndarray, low: np.ndarray, price: float, side: str, atr: float
) -> int:
    band = atr * TEST_PROXIMITY_ATR
    count = 0
    for i in range(len(high)):
        if side == "BSL" and abs(float(high[i]) - price) <= band:
            count += 1
        if side == "SSL" and abs(float(low[i]) - price) <= band:
            count += 1
    return count


# =============================================================================
# RANKING
# =============================================================================


def _rank_zone(
    strength: float,
    tests: int,
    age_bars: int,
    distance_atr: float,
    confluence_sources: int,
) -> LiquidityRank:
    """Deterministic ranking: LOW / MEDIUM / HIGH / EXTREME.

    Extreme = strong + many tests + fresh + near price + multi-source.
    """
    score = 0.0
    score += _clip(strength / 100.0, 0.0, 1.0) * 4.0
    score += _clip(tests / 4.0, 0.0, 1.0) * 3.0
    score += _clip(1.0 - age_bars / 300.0, 0.0, 1.0) * 1.5
    score += _clip(1.0 - distance_atr / 8.0, 0.0, 1.0) * 2.0
    score += _clip(confluence_sources / 3.0, 0.0, 1.0) * 2.0
    if score >= 10.5:
        return LiquidityRank.EXTREME
    if score >= 8.0:
        return LiquidityRank.HIGH
    if score >= 5.5:
        return LiquidityRank.MEDIUM
    return LiquidityRank.LOW


# =============================================================================
# TOP-LEVEL BUILDER
# =============================================================================


def build_liquidity_map(
    bars: Sequence[Any],
    swings_high: Sequence[Any],
    swings_low: Sequence[Any],
    *,
    decision_at: datetime | None = None,
    mid_price: float | None = None,
    timeframe: str = "M1",
) -> list[LiquidityZone]:
    """Builds the ranked liquidity map (BSL + SSL zones).

    ``swings_high`` / ``swings_low`` come from the adaptive swing detector
    (already causal). Only confirmed swings at/before ``decision_at`` are
    consumed.
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
        return []
    arr = _arrays([bars[i] for i in vis_idx])
    high = arr["high"]
    low = arr["low"]
    close = arr["close"]
    n = len(close)
    atr = _atr(high, low, close)
    safe_atr = max(atr, MIN_ATR)
    price = mid_price if mid_price is not None else float(close[-1])

    # ---- collect candidate levels ------------------------------------------
    raw: list[tuple[float, str, float, float, int]] = []  # (price, source, strength, age, tests)

    # swing highs/lows (institutional levels from the adaptive detector)
    for s in swings_high:
        if s.timestamp <= decision_at:
            raw.append((s.price, "SWING_HIGH", s.importance_score, _age_bars(s.timestamp, times), 0))
    for s in swings_low:
        if s.timestamp <= decision_at:
            raw.append((s.price, "SWING_LOW", s.importance_score, _age_bars(s.timestamp, times), 0))

    # session high/low
    sess_hi, sess_lo = _session_high_low(high, low, times, n)
    if sess_hi is not None:
        raw.append((sess_hi, "SESSION_HIGH", 80.0, 0, 0))
    if sess_lo is not None:
        raw.append((sess_lo, "SESSION_LOW", 80.0, 0, 0))

    # equal highs / equal lows (double tops / bottoms)
    for level_price, source in _equal_levels(high, low, "high", safe_atr):
        raw.append((level_price, source, 90.0, 0, 0))
    for level_price, source in _equal_levels(high, low, "low", safe_atr):
        raw.append((level_price, source, 90.0, 0, 0))

    # range high/low over the visible lookback
    look = min(n, RANGE_LOOKBACK)
    range_hi = float(np.max(high[-look:]))
    range_lo = float(np.min(low[-look:]))
    raw.append((range_hi, "RANGE_HIGH", 70.0, 0, 0))
    raw.append((range_lo, "RANGE_LOW", 70.0, 0, 0))

    # ---- dedupe + aggregate into zones --------------------------------------
    tol = safe_atr * EQUAL_TOLERANCE_ATR
    # keyed by price bucket only (side is assigned at finalization against
    # the CURRENT price — BSL above, SSL below). A level's side must reflect
    # its position relative to today's price, not the price when it formed.
    bucket_key = lambda price: round(price / max(tol, 1e-9))
    merged: dict[int, dict[str, Any]] = {}
    for raw_price, source, strength, age, _ in raw:
        key = bucket_key(raw_price)
        entry = merged.get(key)
        if entry is None:
            merged[key] = {
                "price": raw_price,
                "strength": float(strength),
                "age": int(age),
                "sources": {source},
            }
        else:
            entry["strength"] = max(entry["strength"], float(strength))
            entry["age"] = min(entry["age"], int(age))
            entry["sources"].add(source)

    # ---- finalize each zone (tests, distance, rank) -------------------------
    out: list[LiquidityZone] = []
    for _bk, entry in merged.items():
        zprice = entry["price"]
        # side is decided by position vs the CURRENT price
        side = ZoneSide.BUY_SIDE if zprice >= price else ZoneSide.SELL_SIDE
        tests = _tests_at_level(high, low, zprice, side.name, safe_atr)
        distance = abs(zprice - price) / safe_atr
        strength = float(entry["strength"])
        sources = tuple(sorted(entry["sources"]))
        if strength <= 0.0:
            strength = 55.0 + 15.0 * _clip(tests / 3.0, 0.0, 1.0)
        if len(sources) >= 2:
            strength += 10.0
        age = int(entry["age"])
        rank = _rank_zone(strength, tests, age, distance, len(sources))
        out.append(
            LiquidityZone(
                price=zprice,
                side=side,
                strength_score=round(_clip(strength, 0.0, 100.0), 2),
                timeframe=timeframe,
                age_bars=age,
                number_of_tests=tests,
                distance_from_price=round(distance, 4),
                probability_as_target=round(_clip(rank.value / 4.0, 0.0, 1.0), 4),
                rank=rank,
                sources=sources,
            )
        )

    # sort by distance from price (nearest first), bounded
    out.sort(key=lambda z: z.distance_from_price)
    return out[:MAX_ZONES]


def _age_bars(ts: datetime, times: list[datetime]) -> int:
    """Age of a level in visible bars (0 = formed at the decision bar)."""
    for i, t in enumerate(times):
        if t >= ts:
            return len(times) - i
    return len(times)
