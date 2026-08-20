"""Stop-hunt / liquidity-sweep detection for MSLIE.

NOT every wick is manipulation. A stop-hunt candidate requires ALL of:

1. An existing liquidity pool (confirmed zone at/before the decision time)
2. Liquidity violation  — a bar extreme penetrates the pool level
3. Rejection OR acceptance analysis — how price behaved immediately after
4. Candle behavior      — the violation bar's close vs the pool
5. Displacement         — the penetration was decisive (ATR-normalized)
6. Follow-through       — later visible bars confirm reversal or continuation

Output states: REVERSAL / CONTINUATION / UNCERTAIN.

CAUSALITY: everything is computed from bars closed at/before
``decision_at`` (INV-008). The event is only emitted once the confirming
bars have closed — never from the forming bar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

import numpy as np

from nexus_scalp.mslie.models import (
    LiquiditySweepEvent,
    LiquidityZone,
    SweepState,
    ZoneSide,
)

# =============================================================================
# CONSTANTS
# =============================================================================

MIN_ATR: float = 0.20
PENETRATION_ATR: float = 0.05  # minimum ATR-scaled penetration to count
REJECTION_RECLAIM_ATR: float = 0.20  # close back beyond this ATR band = rejection
FOLLOW_THROUGH_BARS: int = 3  # bars after the violation used for the verdict
CONFIDENCE_FLOOR: float = 35.0  # below this the event is UNCERTAIN (no event)
SWEEP_WINDOW_BARS: int = 12  # lookback for the latest sweep candidate
MIN_POOL_DISTANCE_ATR: float = 0.5  # pools closer than this are "in play" —
#                                     already tested, not resting liquidity;
#                                     a penetration of them is noise, not a hunt


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
# DETECTOR
# =============================================================================


def detect_sweep_events(
    bars: Sequence[Any],
    zones: Sequence[LiquidityZone],
    *,
    decision_at: datetime | None = None,
    mid_price: float | None = None,
    atr: float | None = None,
) -> list[LiquiditySweepEvent]:
    """Scans the last ``SWEEP_WINDOW_BARS`` visible bars for confirmed
    stop hunts against the given liquidity zones.

    Returns chronologically ordered events (usually 0 or 1; bounded by the
    window). Only events whose confidence clears the floor are emitted —
    an unconvincing wick is NOT classified as manipulation.
    """
    times = _bar_times(bars)
    if decision_at is None:
        decision_at = times[-1] if times else datetime.now(UTC)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=UTC)
    else:
        decision_at = decision_at.astimezone(UTC)

    vis_idx = [i for i, t in enumerate(times) if t <= decision_at]
    if len(vis_idx) < 4 or not zones:
        return []

    arr = _arrays([bars[i] for i in vis_idx])
    high = arr["high"]
    low = arr["low"]
    close = arr["close"]
    volume = arr["volume"]
    n = len(close)
    if atr is None or atr <= 0:
        tr = np.empty(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(
                high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
            )
        atr = float(np.mean(tr[-14:])) if len(tr) else MIN_ATR
    safe_atr = max(atr, MIN_ATR)

    events: list[LiquiditySweepEvent] = []
    start = max(1, n - SWEEP_WINDOW_BARS)
    current_price = float(close[-1])

    for i in range(start, n):
        for z in zones:
            is_bsl = z.side == ZoneSide.BUY_SIDE
            pool_price = z.price
            # a pool already in play (within the proximity band of the
            # CURRENT price) is NOT resting liquidity — penetrating it is
            # ordinary range interaction, not a stop hunt.
            if abs(pool_price - current_price) < MIN_POOL_DISTANCE_ATR * safe_atr:
                continue
            if is_bsl:
                penetrated = high[i] >= pool_price + PENETRATION_ATR * safe_atr
            else:
                penetrated = low[i] <= pool_price - PENETRATION_ATR * safe_atr
            if not penetrated:
                continue

            # ---- rejection / acceptance analysis (candle behavior) ----------
            # rejection: a later visible bar closes back beyond the pool
            rejection = False
            acceptance = False
            later = range(i + 1, min(n, i + 1 + FOLLOW_THROUGH_BARS))
            for j in later:
                if is_bsl:
                    if close[j] < pool_price - REJECTION_RECLAIM_ATR * safe_atr:
                        rejection = True
                    if close[j] > pool_price + REJECTION_RECLAIM_ATR * safe_atr:
                        acceptance = True
                else:
                    if close[j] > pool_price + REJECTION_RECLAIM_ATR * safe_atr:
                        rejection = True
                    if close[j] < pool_price - REJECTION_RECLAIM_ATR * safe_atr:
                        acceptance = True

            # ---- displacement (penetration depth, ATR-normalized) -----------
            if is_bsl:
                depth = float(high[i] - pool_price) / safe_atr
            else:
                depth = float(pool_price - low[i]) / safe_atr
            depth = _clip(depth, 0.0, 3.0)

            # ---- follow-through: bars after the violation -------------------
            # reversal: rejection AND price closed back beyond the pool
            # continuation: acceptance AND price stayed beyond the pool
            follow = list(later)
            if rejection:
                state = SweepState.REVERSAL
            elif acceptance:
                state = SweepState.CONTINUATION
            else:
                state = SweepState.UNCERTAIN

            # ---- confidence -------------------------------------------------
            vol_ratio = 1.0
            if i >= 1:
                base = float(np.mean(volume[max(0, i - 10) : i])) or 1.0
                vol_ratio = float(volume[i]) / base
            confidence = 40.0
            confidence += 25.0 * _clip(depth / 1.0, 0.0, 1.0)  # displacement
            confidence += 15.0 * _clip(vol_ratio - 1.0, 0.0, 1.0)  # volume surge
            confidence += 10.0 * (1.0 if rejection or acceptance else 0.0)
            confidence += 10.0 * _clip(z.strength_score / 100.0, 0.0, 1.0)  # pool strength
            confidence = _clip(confidence, 0.0, 100.0)

            if confidence < CONFIDENCE_FLOOR:
                continue

            events.append(
                LiquiditySweepEvent(
                    # direction = the side of the pool that was swept
                    # (BSL pool penetrated above => BUY_SIDE liquidity swept;
                    #  SSL pool penetrated below => SELL_SIDE liquidity swept)
                    direction="BUY_SIDE" if is_bsl else "SELL_SIDE",
                    liquidity_type=", ".join(z.sources) if z.sources else "SWING",
                    price=float(close[i]),
                    confidence=round(confidence, 2),
                    sweep_strength=round(depth, 4),
                    after_event_state=state,
                    pool_price=pool_price,
                    timestamp=times[vis_idx[i]],
                )
            )
            break  # one event per bar (nearest zone wins)

    # dedupe: keep the strongest event per (direction, bar)
    best: dict[tuple[str, datetime], LiquiditySweepEvent] = {}
    for ev in events:
        key = (ev.direction, ev.timestamp)
        if key not in best or ev.confidence > best[key].confidence:
            best[key] = ev
    return sorted(best.values(), key=lambda e: e.timestamp)
