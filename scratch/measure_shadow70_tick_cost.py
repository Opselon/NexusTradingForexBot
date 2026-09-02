"""STEP-07/08a: Measure the REAL 70D shadow per-tick cost.

build_liquidity_10(engine, tick) calls compute_liquidity_features on ALL
aggregator bars. Measure that cost with a realistic aggregator bar count
(200/900/2000/4000) to prove whether the 70D shadow hook is hot-path-safe.

The governor (live_engine liquidity_governor) already caches a liquidity
snapshot — if the shadow hook recomputes instead of reusing, that's the bug.
"""
import sys
import time

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import polars as pl

from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.shadow.shadow70.liq_provider import build_liquidity_10

RAW = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/data/raw/XAUUSD_M5.parquet"


class FakeAggregator:
    def __init__(self, bars: list[BarData]) -> None:
        self._bars = bars

    def get_completed_bars(self) -> list[BarData]:
        return self._bars


class FakeEngine:
    def __init__(self, bars: list[BarData], governor=None) -> None:
        self.aggregator = FakeAggregator(bars)
        self.liquidity_governor = governor


class FakeGovernor:
    """Mimics LiquidityGovernor.last_snapshot + _last_success_at."""

    def __init__(self, features: list[float], fresh: bool = True) -> None:
        import types

        snap = types.SimpleNamespace(features=tuple(features))
        self.last_snapshot = snap
        import time

        self._last_success_at = time.monotonic() if fresh else time.monotonic() - 9999.0


class FakeTick:
    symbol = "XAUUSD"


def main() -> None:
    df = pl.read_parquet(RAW).sort("time")
    df = df.with_columns(pl.from_epoch(pl.col("time"), time_unit="s").alias("ts")).sort("ts")
    bars_all: list[BarData] = []
    for r in df.iter_rows(named=True):
        bars_all.append(
            BarData(
                symbol="XAUUSD", timeframe="M5", timestamp=r["ts"],
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                tick_volume=int(r.get("tick_volume", 0) or 0), is_complete=True,
            )
        )
    for n in (200, 900, 2000, 4000):
        engine = FakeEngine(bars_all[-n:])
        t0 = time.perf_counter()
        liq10, version = build_liquidity_10(engine, FakeTick())
        dt = (time.perf_counter() - t0) * 1000
        print(f"bars={n:5d}: fallback rebuild = {dt:8.1f} ms  version={version}  len={len(liq10)}")

    # governor-cached path (BUG-112 fix): must be ~microseconds
    sample = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.0]
    for fresh in (True, False):
        gov = FakeGovernor(sample, fresh=fresh)
        engine = FakeEngine(bars_all[-2000:], governor=gov)
        t0 = time.perf_counter()
        liq10, version = build_liquidity_10(engine, FakeTick())
        dt = (time.perf_counter() - t0) * 1000
        print(f"governor fresh={fresh}: {dt:8.3f} ms  version={version}  len={len(liq10)}")


if __name__ == "__main__":
    main()