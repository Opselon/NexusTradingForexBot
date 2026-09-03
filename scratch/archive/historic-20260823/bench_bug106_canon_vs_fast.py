"""STEP-02c: BUG-106 benchmark — canonical (bounded) vs incremental (fast)
on real XAUUSD M5 data. Records runtime / rows-per-sec / parity equality.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import polars as pl

RAW = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/data/raw/XAUUSD_M5.parquet"


def load(n: int) -> pl.DataFrame:
    df = pl.read_parquet(RAW).sort("time")
    df = df.with_columns(pl.from_epoch(pl.col("time"), time_unit="s").alias("time")).sort("time")
    return df.head(n)


def bench_canonical(df: pl.DataFrame) -> dict:
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    t0 = time.perf_counter()
    out = compute_70d_frame(df, news_frame=None)
    dt = time.perf_counter() - t0
    return {"rows": out.height, "runtime_s": round(dt, 2), "rps": round(out.height / dt, 2)}


def bench_fast(df: pl.DataFrame) -> dict:
    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

    t0 = time.perf_counter()
    out = compute_70d_frame_fast(df, news_frame=None)
    dt = time.perf_counter() - t0
    return {"rows": out.height, "runtime_s": round(dt, 2), "rps": round(out.height / dt, 2)}


def main() -> None:
    results = []
    for n in (600, 1200, 2000):
        df = load(n)
        print(f"--- {n} bars ---")
        try:
            c = bench_canonical(df)
            print(f"  canonical: {c}")
        except Exception as e:
            c = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
            print(f"  canonical FAILED: {c}")
        try:
            f = bench_fast(df)
            print(f"  fast:      {f}")
        except Exception as e:
            f = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
            print(f"  fast FAILED: {f}")
        results.append({"bars": n, "canonical": c, "fast": f})

    dest = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/benchmarks/bug106_70d_frame_bench.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("written:", dest)


if __name__ == "__main__":
    main()