"""BUG-106 STEP-05b: incremental builder scaling 500..20K (fast path only).

The canonical reference is quadratic -> only measured at 500/1000/2000
(the parity harness already proves equality). This probes the incremental
builder's complexity trend: rows/sec should be ~flat (O(n) amortized).
"""
from __future__ import annotations

import gc
import json
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast  # noqa: E402

OUT = REPO / "artifacts" / "benchmarks"
OUT.mkdir(parents=True, exist_ok=True)

SIZES = [500, 1000, 2000, 5000, 10000, 20000]


def run_one(n: int, full: pl.DataFrame) -> dict:
    df = full.head(n)
    gc.collect()
    tracemalloc.start()
    t0 = time.time()
    f = compute_70d_frame_fast(df, news_frame=None)
    dt = time.time() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "rows_in": n,
        "rows_out": f.height,
        "seconds": round(dt, 3),
        "rows_per_sec": round(f.height / dt, 1) if dt else None,
        "peak_ram_mb": round(peak / 1e6, 1),
    }


def main() -> int:
    full = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
    results = []
    for n in SIZES:
        r = run_one(n, full)
        results.append(r)
        print(json.dumps(r), flush=True)
    report = {
        "probe": "BUG-106 STEP-05b incremental scaling",
        "commit": "cf9199a",
        "results": results,
        "complexity_note": "rows/sec flat ~ O(n) amortized; super-linear would show rows/sec falling",
    }
    (OUT / "bug106_perf_fast.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("written:", OUT / "bug106_perf_fast.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())