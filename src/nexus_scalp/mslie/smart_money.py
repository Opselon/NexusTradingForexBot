"""Smart Money feature extraction for MSLIE.

Converts ICT/SMC concepts into numerical ML features:

- Order Blocks          (last opposite-color impulse candle before a move)
- Fair Value Gaps       (3-candle imbalance windows)
- Displacement          (ATR-normalized impulse strength)
- Inducement            (levels engineered to lure entries before a sweep)
- Premium/Discount      (position within the dealing range)

Every value is finite and bounded. All computations are strictly causal
(INV-008): only bars closed at/before ``decision_at`` are visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from nexus_scalp.mslie.models import SmartMoneyFeatures

# =============================================================================
# CONSTANTS
# =============================================================================

MIN_ATR: float = 0.20
OB_LOOKBACK: int = 60
FVG_MIN_ATR: float = 0.20  # minimum gap size for an FVG to count
DISPLACEMENT_BARS: int = 3
MAX_FVG_COUNT: float = 6.0


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
        "open": np.asarray([b.open for b in bars], dtype=np.float64),
        "high": np.asarray([b.high for b in bars], dtype=np.float64),
        "low": np.asarray([b.low for b in bars], dtype=np.float64),
        "close": np.asarray([b.close for b in bars], dtype=np.float64),
        "volume": np.asarray([b.tick_volume for b in bars], dtype=np.float64),
    }


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================================================================
# EXTRACTION
# =============================================================================


def compute_smart_money_features(
    bars: Sequence[Any],
    *,
    decision_at: datetime | None = None,
    atr: float | None = None,
    mid_price: float | None = None,
) -> SmartMoneyFeatures:
    """Computes the smart-money numerical block from visible bars only."""
    times = _bar_times(bars)
    if decision_at is None:
        decision_at = times[-1] if times else datetime.now(UTC)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=UTC)
    else:
        decision_at = decision_at.astimezone(UTC)

    vis_idx = [i for i, t in enumerate(times) if t <= decision_at]
    if len(vis_idx) < 6:
        return _empty()

    arr = _arrays([bars[i] for i in vis_idx])
    o = arr["open"]
    h = arr["high"]
    l = arr["low"]
    c = arr["close"]
    v = arr["volume"]
    n = len(c)

    if atr is None or atr <= 0:
        tr = np.empty(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        atr = float(np.mean(tr[-14:])) if len(tr) else MIN_ATR
    safe_atr = max(atr, MIN_ATR)
    price = mid_price if mid_price is not None else float(c[-1])

    look_start = max(0, n - OB_LOOKBACK)

    # ---- order block ---------------------------------------------------------
    # last strong impulse candle (body > 0.5*range, volume > baseline) whose
    # opposite direction preceded a move — bullish OB = bearish impulse before
    # an up-move; bearish OB = bullish impulse before a down-move.
    ob_type = 0.0
    ob_strength = 0.0
    ob_level: float | None = None
    base_vol = float(np.mean(v[look_start:])) or 1.0
    for i in range(look_start, n - 1):
        rng = max(h[i] - l[i], 1e-9)
        body = abs(c[i] - o[i]) / rng
        if body < 0.5:
            continue
        impulse_dir = 1.0 if c[i] > o[i] else -1.0
        move_dir = 1.0 if c[i + 1] > o[i + 1] else -1.0
        if move_dir not in (impulse_dir, 0.0):
            # opposite-color impulse before the move = order block
            ob_type = -move_dir
            ob_strength = _clip(
                0.4 + 0.3 * body + 0.3 * _clip(float(v[i]) / base_vol - 1.0, 0.0, 1.0), 0.0, 1.0
            )
            ob_level = float(o[i]) if impulse_dir < 0 else float(c[i])

    # ---- FVGs (3-candle imbalances) ------------------------------------------
    fvg_count = 0
    fvg_strength = 0.0
    for i in range(max(1, look_start), n - 1):
        # bullish FVG: low[i+1] > high[i-1]
        gap_up = l[i + 1] - h[i - 1]
        gap_down = l[i - 1] - h[i + 1]
        if gap_up > FVG_MIN_ATR * safe_atr:
            fvg_count += 1
            fvg_strength = max(fvg_strength, _clip(gap_up / safe_atr, 0.0, 3.0))
        elif gap_down > FVG_MIN_ATR * safe_atr:
            fvg_count += 1
            fvg_strength = max(fvg_strength, _clip(gap_down / safe_atr, 0.0, 3.0))

    # ---- displacement --------------------------------------------------------
    # ATR-normalized net move over the last DISPLACEMENT_BARS
    if n > DISPLACEMENT_BARS:
        disp = abs(c[-1] - c[-1 - DISPLACEMENT_BARS]) / safe_atr
    else:
        disp = abs(c[-1] - c[0]) / safe_atr
    displacement_strength = _clip(disp, 0.0, 3.0)

    # ---- inducement ----------------------------------------------------------
    # count of recent swing extremes within inducement distance of price
    # (levels that would lure entries before a sweep)
    inducement = 0
    inducement_band = 0.8 * safe_atr
    for i in range(look_start, n):
        if abs(float(h[i]) - price) <= inducement_band:
            inducement += 1
        if abs(float(l[i]) - price) <= inducement_band:
            inducement += 1
    inducement_levels = _clip(float(inducement), 0.0, MAX_FVG_COUNT)

    # ---- premium / discount --------------------------------------------------
    # position within the visible dealing range: +1 = deep premium (top of
    # range), -1 = deep discount (bottom), 0 = equilibrium
    range_hi = float(np.max(h[look_start:]))
    range_lo = float(np.min(l[look_start:]))
    rng_span = range_hi - range_lo
    if rng_span > 1e-9:
        premium_discount = _clip(2.0 * (price - range_lo) / rng_span - 1.0, -1.0, 1.0)
    else:
        premium_discount = 0.0

    # ---- last mitigated order block distance ---------------------------------
    last_ob_dist = 0.0
    if ob_level is not None:
        last_ob_dist = _clip(abs(price - ob_level) / safe_atr, 0.0, 3.0)

    return SmartMoneyFeatures(
        order_block_type=round(ob_type, 4),
        order_block_strength=round(ob_strength, 4),
        fvg_count=round(_clip(fvg_count, 0.0, MAX_FVG_COUNT), 4),
        fvg_strength=round(fvg_strength, 4),
        displacement_strength=round(displacement_strength, 4),
        inducement_levels=round(inducement_levels, 4),
        premium_discount_position=round(premium_discount, 4),
        last_mitigated_order_block=round(last_ob_dist, 4),
    )


def _empty() -> SmartMoneyFeatures:
    return SmartMoneyFeatures(
        order_block_type=0.0,
        order_block_strength=0.0,
        fvg_count=0.0,
        fvg_strength=0.0,
        displacement_strength=0.0,
        inducement_levels=0.0,
        premium_discount_position=0.0,
        last_mitigated_order_block=0.0,
    )
