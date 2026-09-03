"""STEP-03: Rebuild the real 70D dataset with CORRECT timestamps.

Fixes the AGENT-03 timestamp bug: raw `time` is epoch SECONDS (Int64), the
old scratch script cast it to Datetime("us") — reinterpreting seconds as
microseconds → all timestamps collapsed to 1970-01-01 00:29. The correct
conversion is `pl.from_epoch(time, time_unit="s")` (or the parquet's own
`time_utc` column). This builds the dataset with real 2025-03 UTC times.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

import polars as pl

from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact

RAW = r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/data/raw/XAUUSD_M5.parquet"


def main() -> None:
    df = pl.read_parquet(RAW).sort("time")
    print("raw rows:", df.height, "time dtype:", df["time"].dtype)

    # CORRECT conversion: epoch seconds -> naive-UTC datetime (repo convention)
    df = df.with_columns(
        pl.from_epoch(pl.col("time"), time_unit="s").alias("time")
    ).sort("time")
    print("converted sample:", df["time"].head(3).to_list())
    print("converted range:", df["time"].min(), "->", df["time"].max())

    # bounded slice like AGENT-03 (1200 rows) for a first real build;
    # full history build comes after the A/B/C protocol decision.
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    frame_in = df.tail(n).select(
        ["time", "open", "high", "low", "close", "tick_volume"]
    )

    t0 = time.perf_counter()
    handle = build_70d_dataset(
        frame_in,
        timeframe="M5",
        dataset_id=None,
        incremental=True,      # BUG-106 fast builder (byte-identical)
        verify_parity=True,    # canonical-vs-fast equivalence self-check
    )
    print("BUILD OK in %.1fs" % (time.perf_counter() - t0))
    did = handle.get("dataset_id")
    print("dataset_id:", did)
    print("counts:", handle.get("counts"))
    v = verify_70d_artifact(did)
    print(
        "verify:",
        json.dumps(
            {k: v[k] for k in ("ok", "feature_count", "rows", "schema_id_ok",
                               "dimension_ok", "schema_hash_ok", "all_finite",
                               "all_in_range", "duplicate_timestamps",
                               "duplicate_sample_ids") if k in v},
            indent=1,
        ),
    )

    # TIMESTAMP SANITY: real dataset must be >= 2020 (not 1970)
    man_path = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/model_generation/datasets") / did / "dataset_manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    tr = man.get("temporal_range", {})
    print("temporal_range:", tr)
    start_s = str(tr.get("start", ""))[:10]
    sane = start_s.startswith(("202", "201"))
    print("TIMESTAMP_SANE:", sane)
    if not sane:
        print("ERROR: dataset timestamps still invalid (expected 202x)")
        sys.exit(2)

    # stamp the benchmark/validation artifact record
    out = {
        "dataset_id": did,
        "dataset_hash": handle.get("dataset_hash", ""),
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "row_counts": handle.get("counts", {}),
        "schema_id": "scalp_v3",
        "schema_hash": man.get("feature_schema_hash", ""),
        "temporal_range": tr,
        "verify": {k: v[k] for k in ("ok", "feature_count", "rows") if k in v},
    }
    dest = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/validation/70d_real_dataset.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("written:", dest)


if __name__ == "__main__":
    main()