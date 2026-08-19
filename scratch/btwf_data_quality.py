"""Data quality forensics probe for raw XAUUSD M5 bars (TASK-BT-WF-OOS)."""
import hashlib
import json

import polars as pl

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet")
n = df.height
t = df["time"]
ts = t.to_list()

report: dict = {
    "source": "data/raw/XAUUSD_M5.parquet",
    "provider": "MT5 (MetaQuotes-Demo capture, 2026-08-17)",
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "rows": n,
    "columns": df.columns,
    "range": {"start": str(t.min()), "end": str(t.max())},
}

with open("data/raw/XAUUSD_M5.parquet", "rb") as f:
    report["file_sha256_16"] = hashlib.sha256(f.read()).hexdigest()[:16]

report["monotonic"] = bool(t.is_sorted())
report["duplicate_timestamps"] = int(t.is_duplicated().sum())

o, h, l, c = (df[col].cast(pl.Float64) for col in ["open", "high", "low", "close"])
finite = o.is_finite() & h.is_finite() & l.is_finite() & c.is_finite()
report["finite_rows"] = int(finite.sum())
report["nonfinite_rows"] = int(n - finite.sum())
report["high_lt_max_open_close"] = int(df.select((h < pl.max_horizontal(o, c)).sum()).item())
report["low_gt_min_open_close"] = int(df.select((l > pl.min_horizontal(o, c)).sum()).item())
report["high_lt_low"] = int((h < l).sum())

if "tick_volume" in df.columns:
    tv = df["tick_volume"]
    report["tick_volume"] = {
        "min": int(tv.min()),
        "max": int(tv.max()),
        "zero_pct": float((tv == 0).mean()),
    }

import datetime as dt
import itertools

# time column is epoch seconds (int) — convert
ts_dt = [dt.datetime.fromtimestamp(x, tz=dt.UTC) for x in ts]
report["range"] = {"start": str(t.min()), "end": str(t.max())}

gaps = []
prev = ts_dt[0]
for cur in ts_dt[1:]:
    d = (cur - prev).total_seconds()
    if d > 300:
        gaps.append({"from": str(prev), "to": str(cur), "gap_s": d})
    prev = cur
report["n_gaps_gt_5min"] = len(gaps)
report["max_gap_s"] = max((g["gap_s"] for g in gaps), default=0)
report["p50_interbar_s"] = sorted(
    (cur - prev).total_seconds() for prev, cur in itertools.pairwise(ts_dt)
)[n // 2]
report["unique_days"] = len({x.date().isoformat() for x in ts_dt})
report["sample_gaps"] = gaps[:8]

with open("scratch/btwf_data_quality.json", "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)
print(json.dumps(report, indent=2, default=str)[:4000])
