"""Breakout quality engine for MSLIE — REAL BREAKOUT vs LIQUIDITY TRAP.

Distinguishes a genuine breakout from a fake one (liquidity trap) using:

- closing strength   — did price CLOSE beyond the level (not just wick it)?
- volume             — participation above the recent baseline
- momentum           — ATR-normalized speed of the move
- retest             — a successful retest of the broken level confirms
- structure          — aligned higher-timeframe structure / prior swing break

Output: ``(real_breakout_probability, fake_breakout_probability)``.

CAUSALITY: only bars closed at/before ``decision_at`` are visible (INV-008).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from nexus_scalp.mslie.models import BreakoutQuality

# =============================================================================
# CONSTANTS
# =============================================================================

MIN_ATR: float = 0.20
LOOKBACK: int = 40
MOMENTUM_BARS: int = 3
RETEST_BAND_ATR: float = 0.25


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


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================================================================
# ENGINE
# =============================================================================


def assess_breakout_quality(
    bars: Sequence[Any],
    *,
    decision_at: datetime | None = None,
    breakout_level: float | None = None,
    direction: str | None = None,  # "UP" | "DOWN" (None = auto from bars)
) -> BreakoutQuality | None:
    """Assesses the most recent breakout against ``breakout_level``.

    If ``breakout_level`` is None, the engine uses the recent range high/low
    as the level. Returns None when there is no breakout in the window (no
    level violated by a close) or when history is too short.

    The returned object is honest: when a breakout is detected the two
    probabilities sum to 1.0 (real + fake are complements).
    """
    times = _bar_times(bars)
    if decision_at is None:
        decision_at = times[-1] if times else datetime.now(UTC)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=UTC)
    else:
        decision_at = decision_at.astimezone(UTC)

    vis_idx = [i for i, t in enumerate(times) if t <= decision_at]
    if len(vis_idx) < 8:
        return None

    arr = _arrays([bars[i] for i in vis_idx])
    high = arr["high"]
    low = arr["low"]
    close = arr["close"]
    volume = arr["volume"]
    n = len(close)

    tr = np.empty(n - 1)
    for i in range(1, n):
        tr[i - 1] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = float(np.mean(tr[-14:])) if len(tr) else MIN_ATR
    safe_atr = max(atr, MIN_ATR)

    # ---- find the breakout bar ----------------------------------------------
    look = min(n, LOOKBACK)
    if breakout_level is None:
        breakout_level = (
            float(np.max(high[-look:])) if direction != "DOWN" else float(np.min(low[-look:]))
        )
    if direction is None:
        # auto: most recent close beyond the range high => up, below range
        # low => down
        range_hi = float(np.max(high[-look:-1]))
        range_lo = float(np.min(low[-look:-1]))
        if close[-1] > range_hi:
            direction = "UP"
            breakout_level = range_hi
        elif close[-1] < range_lo:
            direction = "DOWN"
            breakout_level = range_lo
        else:
            return None

    breakout_bar: int | None = None
    for i in range(max(1, n - look), n):
        if direction == "UP" and close[i] > breakout_level:
            breakout_bar = i
            break
        if direction == "DOWN" and close[i] < breakout_level:
            breakout_bar = i
            break
    if breakout_bar is None:
        return None

    # ---- closing strength ---------------------------------------------------
    if direction == "UP":
        closing_strength = _clip((close[breakout_bar] - breakout_level) / safe_atr / 0.5, 0.0, 1.0)
    else:
        closing_strength = _clip((breakout_level - close[breakout_bar]) / safe_atr / 0.5, 0.0, 1.0)

    # ---- volume support -----------------------------------------------------
    base_vol = float(np.mean(volume[max(0, breakout_bar - 10) : breakout_bar])) or 1.0
    volume_support = _clip((float(volume[breakout_bar]) / base_vol - 1.0) / 1.5, 0.0, 1.0)

    # ---- momentum support ---------------------------------------------------
    mom_start = max(0, breakout_bar - MOMENTUM_BARS)
    if direction == "UP":
        momentum = (close[breakout_bar] - close[mom_start]) / safe_atr
    else:
        momentum = (close[mom_start] - close[breakout_bar]) / safe_atr
    momentum_support = _clip(momentum / 1.0, 0.0, 1.0)

    # ---- retest confirmation -------------------------------------------------
    retest_confirmation = 0.0
    band = RETEST_BAND_ATR * safe_atr
    after = range(breakout_bar + 1, n)
    for j in after:
        if direction == "UP":
            if low[j] <= breakout_level + band and close[j] > breakout_level:
                retest_confirmation = 1.0
                break
        elif high[j] >= breakout_level - band and close[j] < breakout_level:
            retest_confirmation = 1.0
            break
    # partial credit: no retest yet but price stayed beyond the level
    if retest_confirmation == 0.0 and n - 1 - breakout_bar >= 1:
        stayed = True
        for j in after:
            if direction == "UP" and close[j] <= breakout_level:
                stayed = False
                break
            if direction == "DOWN" and close[j] >= breakout_level:
                stayed = False
                break
        if stayed:
            retest_confirmation = 0.5

    # ---- structure confirmation ---------------------------------------------
    # how many prior bars had extremes beyond the level (resistance/support
    # already broken before) — a level that was tested multiple times and
    # finally broken is a stronger structural event.
    structure_confirmation = 0.0
    prior_extremes = 0
    for j in range(max(0, breakout_bar - look), breakout_bar):
        if direction == "UP" and high[j] > breakout_level:
            prior_extremes += 1
        if direction == "DOWN" and low[j] < breakout_level:
            prior_extremes += 1
    structure_confirmation = _clip(prior_extremes / 3.0, 0.0, 1.0)

    # ---- combined probabilities ---------------------------------------------
    real = (
        0.30 * closing_strength
        + 0.25 * volume_support
        + 0.20 * momentum_support
        + 0.15 * retest_confirmation
        + 0.10 * structure_confirmation
    )
    real = _clip(real, 0.05, 0.95)
    fake = 1.0 - real

    return BreakoutQuality(
        real_breakout_probability=round(real, 4),
        fake_breakout_probability=round(fake, 4),
        closing_strength=round(closing_strength, 4),
        volume_support=round(volume_support, 4),
        momentum_support=round(momentum_support, 4),
        retest_confirmation=round(retest_confirmation, 4),
        structure_confirmation=round(structure_confirmation, 4),
    )
