"""H-sensitivity on REAL XAUUSD M5 data: does a bounded history reproduce
H=4000 features exactly on the real series? If yes, the builder can use a
smaller H with ZERO semantic change (train==live parity preserved)."""
import sys
import time

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import polars as pl

from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

RAW = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/data/raw/XAUUSD_M5.parquet"


def main() -> None:
    df = pl.read_parquet(RAW).sort("time")
    df = df.with_columns(pl.from_epoch(pl.col("time"), time_unit="s").alias("ts")).sort("ts")
    print("rows:", df.height, "range:", df["ts"].min(), "->", df["ts"].max())

    # build BarData for a tail slice (e.g. last 5000 bars)
    tail = df.tail(5000)
    bars: list[BarData] = []
    for r in tail.iter_rows(named=True):
        bars.append(
            BarData(
                symbol="XAUUSD", timeframe="M5", timestamp=r["ts"],
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                tick_volume=int(r.get("tick_volume", 0) or 0), is_complete=True,
            )
        )
    # 5 probe decision rows spread across the tail
    probes = [2000, 3000, 4000, 4500, 4999]
    ScalpFeatureEngine()
    n_matched = 0
    for pi in probes:
        decision = bars[pi].timestamp
        mid = bars[pi].close
        # canonical H=4000
        t0 = time.perf_counter()
        f4k = compute_liquidity_features(
            bars[max(0, pi + 1 - 4000) : pi + 1], decision_at=decision, mid_price=mid, atr=1.5
        )
        t4k = (time.perf_counter() - t0) * 1000
        vec4k = f4k.as_vector()
        for h in (2000, 1000, 500):
            t0 = time.perf_counter()
            fh = compute_liquidity_features(
                bars[max(0, pi + 1 - h) : pi + 1], decision_at=decision, mid_price=mid, atr=1.5
            )
            th = (time.perf_counter() - t0) * 1000
            vech = fh.as_vector()
            deltas = [abs(a - b) for a, b in zip(vec4k, vech, strict=False)]
            maxd = max(deltas)
            nz = sum(1 for d in deltas if d > 1e-9)
            if maxd < 1e-9:
                n_matched += 1
            print(
                f"probe@{pi} H=4000 {t4k:6.1f}ms vs H={h:4d} {th:6.1f}ms  "
                f"max_delta={maxd:.6f} nz={nz}/10"
            )
    print(f"EXACT MATCHES (H<4000 == H=4000): {n_matched}/15")


if __name__ == "__main__":
    main()