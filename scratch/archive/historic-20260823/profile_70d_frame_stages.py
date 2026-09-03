"""STEP-02b: Profile WHERE compute_70d_frame time goes (BUG-106 incomplete fix).

Hypothesis: LIQUIDITY_HISTORY_LIMIT=4000 bounded the slice, but the per-row
liquidity engine cost over 4000 M5 bars is still huge (swing detection is
O(H^2) or worse). Profile per-stage for a small slice.
"""
import sys
import time

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")


from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData


def make_bars(n: int) -> list[BarData]:
    from datetime import UTC, datetime

    out = []
    ts = datetime(2025, 3, 1, tzinfo=UTC)
    px = 3000.0
    for i in range(n):
        o = px
        c = px + (0.4 if i % 3 else -0.3)
        out.append(
            BarData(
                symbol="XAUUSD", timeframe="M5", timestamp=ts,
                open=o, high=max(o, c) + 0.2, low=min(o, c) - 0.2,
                close=c, tick_volume=100 + (i % 50), is_complete=True,
            )
        )
        px = c
        ts = ts.replace(minute=(ts.minute + 5) % 60, hour=(ts.hour + (1 if ts.minute >= 55 else 0)) % 24)
        if i and i % 288 == 287:
            ts = ts.replace(day=ts.day + 1)
    return out


def main() -> None:
    bars = make_bars(1000)
    engine = ScalpFeatureEngine()

    # stage 1: 50D engine alone (window 55)
    t0 = time.perf_counter()
    for i in range(500, 1000):
        window = bars[max(0, i - 54) : i + 1]
        engine.compute_from_bars(window, None)  # type: ignore[arg-type]
    t50 = time.perf_counter() - t0
    print(f"50D only (500 rows): {t50:.2f}s -> {500 / t50:.1f} rows/s")

    # stage 2: liquidity alone (bounded 4000, but only 1000 bars here)
    from nexus_scalp.domain.models import TickData

    t0 = time.perf_counter()
    for i in range(500, 1000):
        b = bars[i]
        tick = TickData(symbol="XAUUSD", timestamp=b.timestamp, bid=b.close, ask=b.close + 0.3, volume=1.0)
        fv = engine.compute_from_bars(bars[max(0, i - 54) : i + 1], tick)
        compute_liquidity_features(bars[max(0, i + 1 - 4000) : i + 1], decision_at=b.timestamp, mid_price=b.close, atr=fv.atr_m1)
    tliq = time.perf_counter() - t0
    print(f"50D+liquidity (500 rows, H<=1000): {tliq:.2f}s -> {500 / tliq:.1f} rows/s")
    print(f"  implied liquidity cost per row: {(tliq - t50) / 500 * 1000:.1f} ms")

    # stage 3: liquidity with the 4000 limit simulated on 1000 bars is same as
    # stage 2; now test H=4000 real bars
    bars4k = make_bars(4000)
    t0 = time.perf_counter()
    for i in range(3500, 4000):
        b = bars4k[i]
        compute_liquidity_features(bars4k[max(0, i + 1 - 4000) : i + 1], decision_at=b.timestamp, mid_price=b.close, atr=1.5)
    t4k = time.perf_counter() - t0
    print(f"liquidity-only H=4000 (500 rows): {t4k:.2f}s -> {500 / t4k:.1f} rows/s ({t4k / 500 * 1000:.1f} ms/row)")


if __name__ == "__main__":
    main()