"""STEP-02: BUG-106 performance benchmark — compute_70d_frame scaling.

Measures runtime/rows-per-sec for 1K/5K/10K/20K bar windows with the
bounded LIQUIDITY_HISTORY_LIMIT=4000 implementation. Proves the O(n*H)
bound and records memory/CPU context.
"""
import sys
import time
import tracemalloc
from datetime import UTC, datetime

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import polars as pl

from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

SPREAD = 0.30


def make_bars(n: int, start_ts: datetime) -> pl.DataFrame:
    rows = []
    ts = start_ts
    px = 3000.0
    for i in range(n):
        o = px
        c = px + (0.4 if i % 3 else -0.3)
        rows.append(
            {
                "time": ts,
                "open": o,
                "high": max(o, c) + 0.2,
                "low": min(o, c) - 0.2,
                "close": c,
                "tick_volume": 100 + (i % 50),
            }
        )
        px = c
        ts = ts.replace(minute=(ts.minute + 5) % 60, hour=(ts.hour + (1 if ts.minute >= 55 else 0)) % 24)
        if i and i % 288 == 287:  # M5: 288 bars/day
            ts = ts.replace(day=ts.day + 1)
    return pl.DataFrame(rows)


def bench(n: int) -> dict:
    frame = make_bars(n, datetime(2025, 3, 1, 0, 0, tzinfo=UTC))
    tracemalloc.start()
    t0 = time.perf_counter()
    out = compute_70d_frame(frame)
    elapsed = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rows = out.height
    return {
        "bars": n,
        "rows": rows,
        "runtime_s": round(elapsed, 3),
        "rows_per_sec": round(rows / elapsed, 1),
        "peak_mb": round(peak / 1e6, 1),
        "feat_cols": sum(1 for c in out.columns if c.startswith("feat_")),
    }


def main() -> None:
    results = []
    for n in (1000, 5000, 10000, 20000):
        try:
            r = bench(n)
            results.append(r)
            print(f"{n:6d} bars -> {r['rows']:6d} rows  {r['runtime_s']:8.1f}s  "
                  f"{r['rows_per_sec']:8.1f} rows/s  peak {r['peak_mb']:7.1f} MB  feat={r['feat_cols']}")
        except Exception as e:
            print(f"{n:6d} bars -> FAILED {type(e).__name__}: {str(e)[:200]}")
    import json

    path = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/benchmarks/bug106_70d_frame_bench.json"
    import pathlib

    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("written:", path)


if __name__ == "__main__":
    main()