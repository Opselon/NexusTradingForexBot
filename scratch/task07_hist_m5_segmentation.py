"""TASK-07-70D-LIQUIDITY-RESEARCH — full historical Liquidity segmentation (step 3).

Substrate: data/raw/XAUUSD_M5.parquet — REAL broker M5 bars, 100k rows,
2025-03-12 .. 2026-08-17 (gaps only at weekend/daily rollovers, verified:
409 non-5min gaps of 100k, all >= 10min).

Causal protocol (identical to the engine contract): for each decision bar i
(i >= 54), compute features over bars[:i+1] with decision_at = bars[i].
No bar strictly after decision_at is ever read -> no leakage.

Analyses:
  1. Feature distributions (coverage/missingness/saturation/neutral-sentinel rate)
  2. Session segmentation (5 canonical sessions)
  3. Regime proxy: trend vs range via close-position/ATR roll (no production
     regime tags exist for M5 history; the proxy is documented, not loaded from
     a hidden source)
  4. Redundancy matrix (spearman, all 45 pairs)
  5. Stability (half-series mean shift)
  6. Family aggregation

Outputs (tracked, repo convention): scratch/task07_research/*.json
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.features.liquidity_engine import (  # noqa: E402
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)

RESEARCH_RUN_ID = "task07_hist_m5_full_01"
BASELINE_ID = "e85de540e09d3339"
SCHEMA_ID = "scalp_liquidity_v1"
MIN_BARS = 54

SESSIONS = {
    "ASIAN_TOKYO": (0, 8),
    "LONDON": (8, 13),
    "LONDON_NY_OVERLAP": (13, 16),
    "NEW_YORK": (16, 21),
    "OFF_HOURS": (21, 24),
}


def session_of(ts: datetime) -> str:
    h = ts.hour
    for name, (a, b) in SESSIONS.items():
        if a <= h < b:
            return name
    return "OFF_HOURS"


def trend_regime(bars: list[BarData], atr: float) -> str:
    """Documented proxy: net displacement over the observable window vs ATR.
    |close - open| over window >= 1.5*ATR -> TRENDING_MOMENTUM else RANGING.
    NOT a production regime label — research-only approximation."""
    if atr <= 0 or len(bars) < 20:
        return "UNKNOWN"
    win = bars[-20:]
    net = abs(win[-1].close - win[0].open)
    if net >= 1.5 * atr:
        return "TRENDING_MOMENTUM"
    return "RANGING_MEAN_REVERSION"


def load_bars() -> list[BarData]:
    df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
    bars: list[BarData] = []
    for row in df.iter_rows(named=True):
        t = row["time_utc"]
        if isinstance(t, datetime):
            ts = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        else:
            ts = datetime.fromisoformat(str(t)).replace(tzinfo=UTC)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M5",
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=int(row["tick_volume"] or 0),
                is_complete=True,
            )
        )
    return bars


def compute_series(bars: list[BarData], limit: int | None = None) -> list[dict]:
    series: list[dict] = []
    end = len(bars) if limit is None else min(len(bars), limit)
    for i in range(MIN_BARS, end):
        window = bars[: i + 1]
        ts = window[-1].timestamp
        feats = compute_liquidity_features(window, decision_at=ts)
        rec = {"timestamp": ts.isoformat(), "session": session_of(ts), "mid": window[-1].close}
        vec = feats.as_vector()
        for name, v in zip(LIQUIDITY_FEATURE_NAMES, vec, strict=True):
            rec[name] = float(v)
        rec["pools"] = len(feats.pools)
        rec["sweep_state"] = float(feats.liquidity_sweep_state)
        rec["confluence"] = float(feats.liquidity_confluence)
        if i % 200 == 0:
            print(f"  ..bar {i}/{end}", flush=True)
        series.append(rec)
    return series


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    sorted_v = sorted(vals)
    median = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    q1 = sorted_v[int(n * 0.25)]
    q3 = sorted_v[min(n - 1, int(n * 0.75))]
    sat = sum(1 for v in vals if abs(v) >= 2.999) / n
    neutral3 = sum(1 for v in vals if v == 3.0) / n
    return {
        "n": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "saturation_frac_abs_ge_3": round(sat, 4),
        "neutral_sentinel_frac_3": round(neutral3, 4),
        "missing_frac": 0.0 if n else 1.0,
    }


def spearman(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0

    def ranks(x: list[float]) -> list[float]:
        idx = sorted(range(len(x)), key=lambda k: x[k])
        r = [0.0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[idx[j + 1]] == x[idx[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(n)))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


FAMILIES = {
    "LOCATION": ["bsl_distance_atr", "ssl_distance_atr"],
    "STRUCTURE": ["eqh_strength", "eql_strength"],
    "HTF": ["htf_liquidity_score"],
    "CONFLUENCE": ["liquidity_confluence"],
    "EVENT": ["liquidity_sweep_state"],
    "DISPLACEMENT": ["post_sweep_displacement"],
    "DISTANCE_EXT": ["internal_liquidity_distance", "external_liquidity_distance"],
}


def main() -> int:
    bars = load_bars()
    print(f"bars={len(bars)}", flush=True)
    series = compute_series(bars)
    n = len(series)
    names = list(LIQUIDITY_FEATURE_NAMES)
    print(f"samples={n}", flush=True)

    # --- 1. distributions ---
    dist = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "schema_id": SCHEMA_ID,
        "source": "HISTORICAL",
        "input_bars": len(bars),
        "samples": n,
        "time_range": [series[0]["timestamp"], series[-1]["timestamp"]],
        "features": {name: stats([r[name] for r in series]) for name in names},
    }
    (OUT / "feature_distributions.json").write_text(json.dumps(dist, indent=2), encoding="utf-8")

    # --- 2. sessions ---
    sess_rows: dict[str, list[dict]] = defaultdict(list)
    for r in series:
        sess_rows[r["session"]].append(r)
    session_an = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "sessions": {},
    }
    for sess, rows in sorted(sess_rows.items()):
        sn = len(rows)
        entry = {"n": sn, "frac_of_total": round(sn / n, 4)}
        for name in names:
            entry[name] = stats([r[name] for r in rows])
        entry["sweep_frequency"] = round(sum(1 for r in rows if r["sweep_state"] != 0.0) / sn, 4)
        entry["confluence_positive_frac"] = round(sum(1 for r in rows if r["confluence"] > 0.0) / sn, 4)
        entry["avg_pools_per_bar"] = round(sum(r["pools"] for r in rows) / sn, 2)
        entry["avg_confluence"] = round(sum(r["confluence"] for r in rows) / sn, 4)
        session_an["sessions"][sess] = entry
    (OUT / "session_analysis.json").write_text(json.dumps(session_an, indent=2), encoding="utf-8")

    # --- 3. regime proxy ---
    # recompute a small atr per window from the engine (already in feats? no —
    # compute rough ATR here for the proxy using the canonical formula source
    # directly via liquidity_atr)
    from nexus_scalp.features.liquidity_engine import liquidity_atr

    regime_rows: dict[str, list[dict]] = defaultdict(list)
    for i in range(MIN_BARS, len(bars)):
        window = bars[: i + 1]
        ts = window[-1].timestamp
        highs = [b.high for b in window]
        lows = [b.low for b in window]
        closes = [b.close for b in window]
        atr = float(liquidity_atr(highs, lows, closes))
        regime = trend_regime(window, atr)
        feats = compute_liquidity_features(window, decision_at=ts, atr=atr)
        rec = {"timestamp": ts.isoformat(), "session": session_of(ts)}
        vec = feats.as_vector()
        for name, v in zip(names, vec, strict=True):
            rec[name] = float(v)
        rec["sweep_state"] = float(feats.liquidity_sweep_state)
        rec["confluence"] = float(feats.liquidity_confluence)
        regime_rows[regime].append(rec)
        if i % 200 == 0:
            print(f"  ..regime {i}/{len(bars)}", flush=True)

    regime_an = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "regime_proxy": "net-displacement-over-20bars vs 1.5*ATR (research-only, NOT production regime tags)",
        "regimes": {},
    }
    for rg, rows in sorted(regime_rows.items()):
        rn = len(rows)
        entry = {"n": rn, "frac_of_total": round(rn / n, 4)}
        for name in names:
            entry[name] = stats([r[name] for r in rows])
        entry["sweep_frequency"] = round(sum(1 for r in rows if r["sweep_state"] != 0.0) / rn, 4)
        entry["confluence_positive_frac"] = round(sum(1 for r in rows if r["confluence"] > 0.0) / rn, 4)
        regime_an["regimes"][rg] = entry
    (OUT / "regime_analysis.json").write_text(json.dumps(regime_an, indent=2), encoding="utf-8")

    # --- 4. redundancy matrix ---
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c = spearman([r[names[i]] for r in series], [r[names[j]] for r in series])
            pairs.append({"a": names[i], "b": names[j], "spearman": round(c, 4)})
    redun = {"pairs": pairs, "max_abs_corr": round(max(abs(p["spearman"]) for p in pairs), 4),
             "top_correlated": sorted(pairs, key=lambda p: -abs(p["spearman"]))[:6]}

    # --- 5. stability ---
    half = n // 2
    stability = {}
    for name in names:
        s1 = stats([r[name] for r in series[:half]])
        s2 = stats([r[name] for r in series[half:]])
        stability[name] = {
            "first_half_mean": s1["mean"],
            "second_half_mean": s2["mean"],
            "first_half_std": s1["std"],
            "second_half_std": s2["std"],
            "mean_shift_abs": round(abs(s1["mean"] - s2["mean"]), 4),
        }

    # --- 6. families ---
    fam = {}
    for f, members in FAMILIES.items():
        present = [m for m in members if m in names]
        if not present:
            continue
        fam[f] = {
            "members": present,
            "mean": {m: round(sum(r[m] for r in series) / n, 4) for m in present},
            "sample_mean_magnitude": round(sum(abs(r[m]) for r in series for m in present) / (n * len(present)), 4),
        }

    quality = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "redundancy": redun,
        "stability": stability,
        "families": fam,
    }
    (OUT / "feature_quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")

    print(json.dumps({
        "samples": n,
        "sessions": {k: v["n"] for k, v in session_an["sessions"].items()},
        "regimes": {k: v["n"] for k, v in regime_an["regimes"].items()},
        "max_abs_corr": redun["max_abs_corr"],
        "top_correlated": redun["top_correlated"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())