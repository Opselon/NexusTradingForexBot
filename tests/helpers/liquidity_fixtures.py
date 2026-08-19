"""Deterministic bar fixtures for the liquidity feature suites.

TASK-01-60D-LIQUIDITY. Engineered scenarios produce KNOWN structural
features (a swing high at a specific index, a later touch, a sweep) so the
causality tests can assert exact timestamps.

IMPORTANT: flat/symmetric bars become fractal pivots everywhere (every bar is
a local max/min) which pollutes swing detection. The pivot-free building
block here is a MONOTONIC RAMP — a ramp produces NO interior fractals, so the
only swings in a scenario are the explicit spikes we inject.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.market_data.bar_aggregator import BarData


def bar(
    i: int,
    t0: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    vol: int = 100,
) -> BarData:
    return BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=t0 + timedelta(minutes=i),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=vol,
        is_complete=True,
    )


def ramp_bars(
    n: int,
    start_price: float,
    step: float,
    t0: datetime,
    atr_units: float = 1.0,
) -> list[BarData]:
    """Monotonic ramp: NO interior fractal pivots (each bar's high/low is
    strictly dominated by the next bar's), so swings appear only where the
    caller injects spikes."""
    out: list[BarData] = []
    for i in range(n):
        c = start_price + step * i
        out.append(
            bar(
                i,
                t0,
                c - 0.2 * atr_units,
                c + 0.5 * atr_units,
                c - 0.5 * atr_units,
                c,
            )
        )
    return out


def steady_bars(
    n: int,
    price: float = 3300.0,
    step: float = 0.0,
    t0: datetime | None = None,
    atr_units: float = 1.0,
) -> list[BarData]:
    """n bars drifting by ``step`` per bar around ``price``. NOTE: when
    flat (step=0) every bar is a fractal pivot — only use for volume/ATR
    sanity tests, never for swing scenarios."""
    t0 = t0 or datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    return ramp_bars(n, price, step, t0, atr_units)


def swing_high_bars(
    n_before: int,
    high_price: float,
    base: float,
    atr_units: float = 1.0,
    t0: datetime | None = None,
    step: float = 0.05,
) -> list[BarData]:
    """Ramp, ONE sharp high bar (the only swing), then 6 ramp bars so the
    swing at the peak is confirmed by bar i+5."""
    t0 = t0 or datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    out = ramp_bars(n_before, base - step * n_before, step, t0, atr_units)
    i = n_before
    # spike: high above the ramp; low NOT below neighbors (avoids a spurious
    # swing low on the same bar)
    out.append(bar(i, t0, base, high_price, base - 0.35 * atr_units, base, vol=200))
    for j in range(1, 7):
        c = base + step * j
        out.append(bar(i + j, t0, c - 0.2 * atr_units, c + 0.5 * atr_units, c - 0.5 * atr_units, c))
    return out


def swing_low_bars(
    n_before: int,
    low_price: float,
    base: float,
    atr_units: float = 1.0,
    t0: datetime | None = None,
    step: float = 0.05,
) -> list[BarData]:
    """Mirror of swing_high_bars for a confirmed swing LOW."""
    t0 = t0 or datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    out = ramp_bars(n_before, base - step * n_before, step, t0, atr_units)
    i = n_before
    # spike low; high NOT above neighbors (avoids a spurious swing high)
    out.append(bar(i, t0, base, base + 0.35 * atr_units, low_price, base, vol=200))
    for j in range(1, 7):
        c = base + step * j
        out.append(bar(i + j, t0, c - 0.2 * atr_units, c + 0.5 * atr_units, c - 0.5 * atr_units, c))
    return out


def sweep_pool_bars(
    pool_price: float,
    base: float,
    pool_index: int,
    sweep_index: int,
    *,
    n_total: int,
    side: str = "bsl",
    atr_units: float = 1.0,
    t0: datetime | None = None,
) -> list[BarData]:
    """Ramp with a pool spike at ``pool_index``, a penetration at
    ``sweep_index`` and a rejecting close at ``sweep_index+1`` (strict
    causal sweep). side='bsl' penetrates above; 'ssl' below."""
    t0 = t0 or datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    out = ramp_bars(n_total, base - 0.05 * n_total, 0.05, t0, atr_units)
    # pool spike
    if side == "bsl":
        out[pool_index] = bar(
            pool_index, t0, base, pool_price, base - 0.35 * atr_units, base, vol=200
        )
    else:
        out[pool_index] = bar(
            pool_index, t0, base, base + 0.35 * atr_units, pool_price, base, vol=200
        )
    # penetration bar
    if side == "bsl":
        out[sweep_index] = bar(
            sweep_index, t0, base, pool_price + 0.5 * atr_units, base - 0.5 * atr_units, base
        )
    else:
        out[sweep_index] = bar(
            sweep_index, t0, base, base + 0.5 * atr_units, pool_price - 0.5 * atr_units, base
        )
    # rejecting close: back beyond the pool by > RECLAIM_FRACTION_ATR
    if side == "bsl":
        out[sweep_index + 1] = bar(
            sweep_index + 1,
            t0,
            base,
            base + 0.3 * atr_units,
            base - atr_units,
            base - 0.3 * atr_units,
        )
    else:
        out[sweep_index + 1] = bar(
            sweep_index + 1,
            t0,
            base,
            base + atr_units,
            base - 0.3 * atr_units,
            base + 0.3 * atr_units,
        )
    return out


def bars_to_frame(rows: list[dict[str, Any]]):
    """Converts bare dict rows into a Polars bars frame (schema_v2 input)."""
    import polars as pl

    return pl.DataFrame(rows)
