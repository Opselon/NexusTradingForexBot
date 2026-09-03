# -*- coding: utf-8 -*-
"""MLFIX-T7 PART 1 — Data quality / gap / OHLC / timezone audit probe.

Audits data/raw/XAUUSD_M1.csv (the ONLY raw history behind the 70D datasets):
  * row count, duplicate timestamps, strict chronological ordering
  * gap census: count / size distribution / largest gaps (with timestamps)
  * timezone semantics (epoch column vs time_utc column consistency)
  * session boundaries (Asia / London / NY per UTC hour, weekend closure)
  * OHLC validity: high >= low, high >= max(open,close), low <= min(open,close),
    non-negative prices, sane ranges, spread sanity

Writes scratch/ns_audit_data_quality_out.json (committed evidence).
Read-only over the CSV. Never mutates anything.
"""
import json
import os
import sys
from bisect import bisect_right
from collections import Counter
from datetime import UTC, datetime, timedelta

import polars as pl

sys.path.insert(0, "src")

CSV = "data/raw/XAUUSD_M1.csv"
OUT = "scratch/ns_audit_data_quality_out.json"
MINUTE_US = 60 * 1_000_000

report: dict = {"probe": "ns_audit_data_quality", "csv": CSV}

# ---------------------------------------------------------------- load
lf = pl.scan_csv(CSV)
rows = lf.select(
    [
        pl.col("time").alias("epoch"),
        "open",
        "high",
        "low",
        "close",
        "spread",
        "tick_volume",
        pl.col("time_utc").str.to_datetime("%Y-%m-%dT%H:%M:%S%.f"),
    ]
).collect()

n = rows.height
report["row_count"] = n

epochs = rows["epoch"].to_numpy().astype("int64")
times = rows["time_utc"].to_list()

# ------------------------------------------------------------ ordering
ordered = bool((epochs[1:] > epochs[:-1]).all())
report["strictly_increasing"] = ordered

# ------------------------------------------------------------- dupes
dup_count = int(n - len(set(epochs.tolist())))
report["duplicate_timestamps"] = dup_count

# ---------------------------------------------------------- tz semantics
tz_mismatch = 0
tz_examples = []
for ep, ts in zip(epochs.tolist(), times):
    dt = datetime.fromtimestamp(ep / 1_000_000, tz=UTC) if ep > 1e14 else datetime.fromtimestamp(
        ep, tz=UTC
    )
    # epoch column is SECONDS (1e9 range) per header inspection; verify
    if abs((dt.replace(tzinfo=None) - ts.replace(tzinfo=None)).total_seconds()) > 1:
        tz_mismatch += 1
        if len(tz_examples) < 3:
            tz_examples.append({"epoch": ep, "time_utc": str(ts)})
report["epoch_unit"] = "seconds" if epochs.max() < 4_102_444_800 else "microseconds"
report["epoch_vs_time_utc_mismatches"] = tz_mismatch
report["tz_examples"] = tz_examples

# ----------------------------------------------------------- gap census
# epoch column is SECONDS; nominal inter-bar delta = 60 s
gaps_all = []
gaps_over_1m = []
for i in range(1, n):
    d = int(epochs[i] - epochs[i - 1])  # seconds
    gap_mins = d // 60
    if d != 60:
        gaps_all.append(
            {
                "after_epoch": int(epochs[i - 1]),
                "after_utc": str(times[i - 1]),
                "resumes_utc": str(times[i]),
                "gap_minutes": int(gap_mins),
                "gap_hours": round(d / 3600.0, 3),
            }
        )
        if d > 60:
            gaps_over_1m.append(d // 60)
gap_sizes = gaps_over_1m
# distribution over REAL gaps only
gap_counter = Counter()
for m in gap_sizes:
    if m <= 5:
        gap_counter["2-5m"] += 1
    elif m <= 30:
        gap_counter["6-30m"] += 1
    elif m <= 120:
        gap_counter["31m-2h"] += 1
    elif m <= 1440:
        gap_counter["2h-24h"] += 1
    elif m <= 3600:
        gap_counter["1d-2.5d"] += 1
    else:
        gap_counter[">2.5d"] += 1

weekend_gaps = 0
weekday_gaps = 0
for g in gaps_all:
    if g["gap_minutes"] <= 1:
        continue
    resume_dt = datetime.fromisoformat(g["resumes_utc"])
    if resume_dt.weekday() in (5, 6) or (resume_dt.weekday() == 1 and resume_dt.hour < 4):
        weekend_gaps += 1
    else:
        weekday_gaps += 1

report["gap_census"] = {
    "total_non_60s_deltas": len(gaps_all),
    "total_gaps_gt_1m": len(gap_sizes),
    "largest_gap_minutes": max(gap_sizes) if gap_sizes else 0,
    "largest_gap_hours": round((max(gap_sizes) / 60.0), 2) if gap_sizes else 0,
    "size_distribution": dict(gap_counter),
    "largest_10": sorted(gaps_all, key=lambda g: -g["gap_minutes"])[:10],
    "weekend_aligned": weekend_gaps,
    "weekday_gaps": weekday_gaps,
    "total_missing_minutes": sum(gap_sizes),
}

# ------------------------------------------------------------- sessions
hour_hist = Counter(t.hour for t in times)
weekday_hist = Counter(t.weekday() for t in times)
report["sessions"] = {
    "hours_utc_covered": sorted(hour_hist.keys()),
    "bars_by_utc_hour": {str(h): hour_hist[h] for h in sorted(hour_hist)},
    "bars_by_weekday": {str(d): weekday_hist[d] for d in sorted(weekday_hist)},
    "session_asis_london_ny": {
        "asia_00_07": sum(hour_hist.get(h, 0) for h in range(0, 7)),
        "london_07_12": sum(hour_hist.get(h, 0) for h in range(7, 12)),
        "ny_12_17": sum(hour_hist.get(h, 0) for h in range(12, 17)),
        "ny_late_17_21": sum(hour_hist.get(h, 0) for h in range(17, 21)),
        "closed_21_24": sum(hour_hist.get(h, 0) for h in range(21, 24)),
    },
}
# weekend-closure verification: any bar on Sat/Sun?
weekend_bars = weekday_hist.get(5, 0) + weekday_hist.get(6, 0)
report["sessions"]["weekend_bars"] = weekend_bars

# --------------------------------------------------------- OHLC validity
opens = rows["open"].to_numpy()
highs = rows["high"].to_numpy()
lows = rows["low"].to_numpy()
closes = rows["close"].to_numpy()
spreads = rows["spread"].to_numpy()

bad_high_lt_low = int((highs < lows).sum())
bad_high_inRange = int(((highs < opens) | (highs < closes)).sum())
bad_low_inRange = int(((lows > opens) | (lows > closes)).sum())
bad_negative = int(((opens < 0) | (highs < 0) | (lows < 0) | (closes < 0)).sum())
nonfinite = int(
    (~__import__("numpy").isfinite(opens)).sum()
    + (~__import__("numpy").isfinite(highs)).sum()
    + (~__import__("numpy").isfinite(lows)).sum()
    + (~__import__("numpy").isfinite(closes)).sum()
)
rng_lo = float(min(lows.min(), opens.min()))
rng_hi = float(max(highs.max(), closes.max()))
# sane XAUUSD range guard (2026 window): 1000..6000 USD/oz
sane = bool(1000.0 <= rng_lo and rng_hi <= 6000.0)

spread_stats = {
    "min": float(spreads.min()),
    "avg": round(float(spreads.mean()), 2),
    "p95": float(__import__("numpy").percentile(spreads, 95)),
    "max": float(spreads.max()),
    "negative": int((spreads < 0).sum()),
}

report["ohlc_validity"] = {
    "high_lt_low": bad_high_lt_low,
    "high_outside_open_close": bad_high_inRange,
    "low_outside_open_close": bad_low_inRange,
    "negative_prices": bad_negative,
    "non_finite": nonfinite,
    "price_range": {"min": rng_lo, "max": rng_hi},
    "sane_xauusd_range": sane,
    "spread_points": spread_stats,
    "all_valid": bool(
        bad_high_lt_low == 0
        and bad_high_inRange == 0
        and bad_low_inRange == 0
        and bad_negative == 0
        and nonfinite == 0
        and sane
    ),
}

report["date_range"] = {"first_utc": str(times[0]), "last_utc": str(times[-1])}

# ------------------------------------------------- gap-effect estimates
# quantify downstream exposure of gaps for consumers (documented in report):
seq32_windows = max(0, n - 31)
report["gap_effects_estimate"] = {
    "candidate_seq_windows_len32": seq32_windows,
    "minutes_missing": sum(gap_sizes),
    "pct_time_missing": round(100.0 * sum(gap_sizes) / (seq32_windows + sum(gap_sizes)), 3),
    "tb_tail_rows_unlabeled_by_horizon": 15,
}

os.makedirs("scratch", exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)
print(json.dumps(report, indent=2, default=str)[:2000])
print(f"...full JSON -> {OUT}")
