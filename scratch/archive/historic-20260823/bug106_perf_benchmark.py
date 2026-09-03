"""BUG-106 STEP-05: performance benchmark — compute_70d_frame_fast scaling 500..20K.

Records total runtime, rows/sec, peak RAM per size (mission 20/19).
Reference (canonical) only on small sizes (it's O(n^2)-or-worse).
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

from nexus_scalp.model_generation.schema_v2 import compute_70d_frame  # noqa: E402
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast  # noqa: E402

OUT = REPO / "artifacts" / "benchmarks"
OUT.mkdir(parents=True, exist_ok=True)

SIZES = [500, 1000, 2000, 5000, 10000, 20000]


def run_one(fn, df, label):
    gc.collect()
    tracemalloc.start()
    t0 = time.time()
    f = fn(df, news_frame=None)
    dt = time.time() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "label": label,
        "rows_in": df.height,
        "rows_out": f.height,
        "seconds": round(dt, 3),
        "rows_per_sec": round(f.height / dt, 1) if dt else None,
        "peak_ram_mb": round(peak / 1e6, 1),
    }


def main() -> int:
    full = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
    results = []
    for n in SIZES:
        df = full.head(n)
        fast = run_one(compute_70d_frame_fast, df, f"fast_{n}")
        results.append(fast)
        print(json.dumps(fast))
        # reference only up to 2000 (quadratic)
        if n <= 2000:
            ref = run_one(compute_70d_frame, df, f"ref_{n}")
            results.append(ref)
            print(json.dumps(ref))
    report = {
        "probe": "BUG-106 STEP-05 performance scaling",
        "commit": "ff1d00a",
        "hardware": "AMD64 CPU",
        "results": results,
        "note": "fast = compute_70d_frame_fast (incremental); ref = compute_70d_frame (canonical, quadratic)",
    }
    (OUT / "bug106_perf.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("written:", OUT / "bug106_perf.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())