"""TASK-07-70D-LIQUIDITY-RESEARCH — algorithm version isolation v1.0 vs v1.1 (step 5).

Frozen contract (mission 43): results from liquidity-v1.0 must NEVER be mixed
silently with liquidity-v1.1. This analysis computes BOTH versions on the SAME
causal inputs (identical bars, decision_at, ATR) and attributes the differences
feature by feature — quantifying what TASK-06's optimization actually changed
in distributional terms, and whether the degeneracies I independently found in
v1.0 (confluence saturation, sweep flood, eqh step) are resolved in v1.1.

Inputs: data/raw/XAUUSD_M5.parquet, causal stratified sample (200-bar stride,
LOOKBACK=2000 bars per decision, decision_at gating — same protocol as the
v1.0-only consolidation run, so results are directly comparable).

Outputs (tracked): scratch/task07_research/version_isolation_v1_vs_v1_1.json
"""
from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.features.liquidity_engine import (  # noqa: E402
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
    liquidity_atr,
)
from nexus_scalp.features.liquidity_engine_opt import (  # noqa: E402
    LIQUIDITY_ALGORITHM_VERSION,
    compute_liquidity_features_v1_1,
)
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)

RESEARCH_RUN_ID = "task07_version_isolation_01"
BASELINE_ID = "e85de540e09d3339"
STRIDE = 200
LOOKBACK = 2000
MIN_BARS = 54
N_POINTS = 400  # bounded for runtime

SESSIONS = {
    "ASIAN_TOKYO": (0, 8),
    "LONDON": (8, 13),
    "LONDON_NY_OVERLAP": (13, 16),
    "NEW_YORK": (16, 21),
}


def session_of(ts) -> str:
    h = ts.hour
    for name, (a, b) in SESSIONS.items():
        if a <= h < b:
            return name
    return "OFF_HOURS"


def load_bars() -> list[BarData]:
    df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
    bars: list[BarData] = []
    for row in df.iter_rows(named=True):
        t = row["time_utc"]
        ts = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        bars.append(
            BarData(symbol="XAUUSD", timeframe="M5", timestamp=ts,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    tick_volume=int(row["tick_volume"] or 0), is_complete=True)
        )
    return bars


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    sv = sorted(vals)
    uniq = len(set(round(v, 4) for v in vals))
    return {
        "n": n, "mean": round(mean, 4), "median": round(sv[n // 2], 4),
        "std": round(std, 4), "min": round(min(vals), 4), "max": round(max(vals), 4),
        "unique_at_4dp": uniq,
        "saturation_frac_abs_ge_3": round(sum(1 for v in vals if abs(v) >= 2.999) / n, 4),
        "neutral_sentinel_frac_3": round(sum(1 for v in vals if v == 3.0) / n, 4),
        "zero_frac": round(sum(1 for v in vals if v == 0.0) / n, 4),
    }


def main() -> int:
    from datetime import UTC  # noqa: PLC0415

    bars = load_bars()
    print(f"bars={len(bars)}", flush=True)
    idxs = list(range(MIN_BARS, len(bars), STRIDE))[:N_POINTS]
    print(f"sample points={len(idxs)}", flush=True)

    rows = []
    for k, i in enumerate(idxs):
        lo = max(0, i + 1 - LOOKBACK)
        window = bars[lo : i + 1]
        ts = window[-1].timestamp
        atr = float(liquidity_atr([b.high for b in window], [b.low for b in window], [b.close for b in window]))
        v1 = compute_liquidity_features(window, decision_at=ts, atr=atr)
        v11 = compute_liquidity_features_v1_1(window, decision_at=ts, atr=atr)
        rec = {"timestamp": ts.isoformat(), "session": session_of(ts)}
        for name, a, b in zip(LIQUIDITY_FEATURE_NAMES, v1.as_vector(), v11.as_vector(), strict=True):
            rec[f"v1_{name}"] = float(a)
            rec[f"v11_{name}"] = float(b)
            rec[f"d_{name}"] = float(b) - float(a)
        rec["v1_pools"] = len(v1.pools)
        rec["v11_pools"] = len(v11.pools)
        rows.append(rec)
        if k % 100 == 0:
            print(f"  ..{k}/{len(idxs)}", flush=True)

    n = len(rows)
    names = list(LIQUIDITY_FEATURE_NAMES)
    out = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "versions": {"v1": "liquidity-v1.0 (committed liquidity_engine.py, golden baseline 4455874)",
                     "v1_1": LIQUIDITY_ALGORITHM_VERSION + " (candidate liquidity_engine_opt.py, TASK-06)"},
        "protocol": "identical causal inputs (bars, decision_at, ATR), 200-bar stride, LOOKBACK=2000, decision_at gating",
        "samples": n,
        "time_range": [rows[0]["timestamp"], rows[-1]["timestamp"]],
        "per_feature": {},
        "bivariate": {},
    }
    for name in names:
        a = [r[f"v1_{name}"] for r in rows]
        b = [r[f"v11_{name}"] for r in rows]
        d = [r[f"d_{name}"] for r in rows]
        out["per_feature"][name] = {
            "v1": stats(a),
            "v1_1": stats(b),
            "delta_mean": round(sum(d) / n, 4),
            "delta_std": round(math.sqrt(sum((x - sum(d) / n) ** 2 for x in d) / n), 4),
            "frac_changed": round(sum(1 for x in d if abs(x) > 1e-9) / n, 4),
        }

    # bivariate: confluence vs sweep state under both versions + session x version
    from collections import defaultdict  # noqa: PLC0415

    by_sess: dict[str, dict] = defaultdict(dict)
    for r in rows:
        for name in names:
            by_sess[r["session"]].setdefault(f"v1_{name}", []).append(r[f"v1_{name}"])
            by_sess[r["session"]].setdefault(f"v11_{name}", []).append(r[f"v11_{name}"])
    out["session_delta"] = {}
    for sess, cols in sorted(by_sess.items()):
        out["session_delta"][sess] = {
            "n": len(cols[f"v1_{names[0]}"]),
            "confluence_delta_mean": round(
                sum(c - a for a, c in zip(cols["v1_liquidity_confluence"], cols["v11_liquidity_confluence"], strict=True)) / len(cols["v1_liquidity_confluence"]), 4),
            "sweep_delta_mean": round(
                sum(c - a for a, c in zip(cols["v1_liquidity_sweep_state"], cols["v11_liquidity_sweep_state"], strict=True)) / len(cols["v1_liquidity_sweep_state"]), 4),
        }

    (OUT / "version_isolation_v1_vs_v1_1.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "samples": n,
        "per_feature_summary": {
            name: {"delta_mean": out["per_feature"][name]["delta_mean"],
                   "v1_std": out["per_feature"][name]["v1"]["std"],
                   "v11_std": out["per_feature"][name]["v1_1"]["std"],
                   "v1_sat": out["per_feature"][name]["v1"]["saturation_frac_abs_ge_3"],
                   "v11_sat": out["per_feature"][name]["v1_1"]["saturation_frac_abs_ge_3"],
                   "v1_unique": out["per_feature"][name]["v1"]["unique_at_4dp"],
                   "v11_unique": out["per_feature"][name]["v1_1"]["unique_at_4dp"],
                   "frac_changed": out["per_feature"][name]["frac_changed"]}
            for name in names},
        "session_delta": out["session_delta"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())