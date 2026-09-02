"""BUG-106 parity verification — canonical compute_70d_frame vs incremental builder.

Mission 9/10/15: exact (or declared-tolerance) parity on all 70 values at many
timestamps; future-bar injection cannot alter T; cache/full-rebuild parity.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.model_generation.schema_v2 import compute_70d_frame  # noqa: E402
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast  # noqa: E402

OUT = REPO / "artifacts" / "benchmarks"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time").head(800)

    t0 = time.time()
    ref = compute_70d_frame(df, news_frame=None)
    t_ref = time.time() - t0
    t0 = time.time()
    fast = compute_70d_frame_fast(df, news_frame=None)
    t_fast = time.time() - t0

    print(f"rows={ref.height} ref={t_ref:.2f}s fast={t_fast:.2f}s speedup={t_ref / max(t_fast, 1e-9):.1f}x")

    ref_cols = [c for c in ref.columns if c.startswith("feat_")]
    fast_cols = [c for c in fast.columns if c.startswith("feat_")]
    assert len(ref_cols) == 70 and len(fast_cols) == 70, (len(ref_cols), len(fast_cols))
    assert ref_cols == fast_cols

    max_delta = 0.0
    mismatched = []
    for c in ref_cols:
        r = ref[c].to_list()
        f = fast[c].to_list()
        for i, (a, b) in enumerate(zip(r, f, strict=True)):
            d = abs(float(a) - float(b))
            max_delta = max(max_delta, d)
            if d > 1e-9:
                mismatched.append({"col": c, "row": i, "ref": float(a), "fast": float(b), "delta": d})
                if len(mismatched) >= 20:
                    break

    # also compare non-feature columns
    meta_delta = 0.0
    for c in ["timestamp", "open", "high", "low", "close", "atr_m1"]:
        r = ref[c].to_list()
        f = fast[c].to_list()
        for a, b in zip(r, f, strict=True):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                meta_delta = max(meta_delta, abs(float(a) - float(b)))

    # --- future-bar injection (temporal anti-leakage, mission 10) ---
    base = df.head(400)
    future = df.slice(400, 60)  # bars that come AFTER the 400-bar prefix
    no_future = compute_70d_frame_fast(base, news_frame=None)
    with_future = compute_70d_frame_fast(
        pl.concat([base, pl.DataFrame(future)]),
        news_frame=None,
    ).head(no_future.height)
    leak = 0.0
    for c in ref_cols:
        a = no_future[c].to_list()
        b = with_future[c].to_list()
        for x, y in zip(a, b, strict=True):
            leak = max(leak, abs(float(x) - float(y)))

    report = {
        "commit": "WIP-bug106",
        "rows_compared": ref.height,
        "features": len(ref_cols),
        "ref_seconds": round(t_ref, 3),
        "fast_seconds": round(t_fast, 3),
        "speedup_x": round(t_ref / max(t_fast, 1e-9), 2),
        "max_delta_feature": float(max_delta),
        "max_delta_meta": float(meta_delta),
        "mismatched_cells_gt_1e9": len([m for m in mismatched]),
        "sample_mismatches": mismatched[:10],
        "future_injection_max_delta": float(leak),
        "parity": "EXACT" if max_delta <= 1e-9 and meta_delta <= 1e-9 and leak <= 1e-9 else "MISMATCH",
    }
    (OUT / "bug106_parity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["parity"] == "EXACT" else 1


if __name__ == "__main__":
    sys.exit(main())