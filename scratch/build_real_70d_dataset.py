"""Build a real 70D dataset (scalp_v3) from broker M5 bars (bounded slice)."""

import datetime as _dt
import json
import sys
import time

sys.path.insert(0, ".")

import polars as pl

from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact

df = pl.read_parquet(r"data/raw/XAUUSD_M5.parquet").sort("time")
# -> naive datetime via epoch
df = df.with_columns(pl.col("time").cast(pl.Datetime("us")).cast(pl.Int64).alias("_us"))
rows = []
for r in df.tail(1200).iter_rows(named=True):
    us = int(r["_us"])
    ts = _dt.datetime.fromtimestamp(us / 1_000_000, tz=_dt.UTC)
    rows.append(
        {
            "time": ts,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r.get("tick_volume", 0) or 0),
        }
    )
frame_in = pl.DataFrame(rows)
tm = time.perf_counter()
try:
    handle = build_70d_dataset(frame_in, timeframe="M5", dataset_id=None)
    print("BUILD OK in %.1fs" % (time.perf_counter() - tm))
    print(
        json.dumps(
            {
                "dataset_id": handle.get("dataset_id"),
                "counts": handle.get("counts"),
                "hash": handle.get("dataset_hash", ""),
            }
        )
    )
    did = handle.get("dataset_id")
    v = verify_70d_artifact(did)
    print(
        json.dumps(
            {
                "verify": {
                    k: v[k]
                    for k in (
                        "ok",
                        "feature_count",
                        "rows",
                        "schema_id_ok",
                        "dimension_ok",
                        "schema_hash_ok",
                        "all_finite",
                        "all_in_range",
                    )
                    if k in v
                }
            },
            indent=1,
        )
    )
except Exception as e:
    print("BUILD FAILED:", type(e).__name__, str(e)[:400])
    import traceback

    traceback.print_exc()
