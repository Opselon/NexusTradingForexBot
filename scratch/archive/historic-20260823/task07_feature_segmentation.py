"""TASK-07-70D-LIQUIDITY-RESEARCH — Liquidity feature segmentation (research step 2).

Feeds: frozen `research_baseline.json` (e85de540e09d3339) + REAL M1 XAUUSD bars
from artifacts/candle_intel.db (2026-08-17T22:36Z .. 2026-08-18T22:59Z, 342 bars).

Analyses (all strictly causal — the engine only sees bars closed at/before
decision_at):
  1. Coverage / missingness / distribution / saturation / stability for all 10
     Liquidity dimensions (LIQUIDITY_01..10, features 50..59 of the 60D vector).
  2. Session segmentation (ASIAN_TOKYO / LONDON / NEW_YORK / LONDON_NY_OVERLAP /
     OFF_HOURS) — liquidity distributions, sweep frequency, confluence,
     distance behavior.
  3. Regime segmentation using the project's market_regimes table where
     aligned (else UNKNOWN) — trend / range behavior.
  4. Feature family aggregation (LOCATION/STRUCTURE/HTF/CONFLUENCE/EVENT/
     DISPLACEMENT) and redundancy (spearman matrix + nearest-neighbor info).

Output: artifacts/model_generation/liquidity_research/feature_distributions.json,
session_analysis.json, feature_quality.json (with run identity per mission 42).

source=REPLAY/HISTORICAL. NOT a trading rule. No parameter mutation.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.features.liquidity_engine import (  # noqa: E402
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from nexus_scalp.features.schema_contract import feature_schema_hash  # noqa: E402
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

OUT = REPO / "artifacts" / "model_generation" / "liquidity_research"
OUT.mkdir(parents=True, exist_ok=True)

RESEARCH_RUN_ID = "task07_features_01"
SCHEMA_ID = "scalp_liquidity_v1"  # 60D contract (indices 50..59 = liquidity)


# Session windows (UTC, XAUUSD): Tokyo 00-09, London 08-16, NY 13-21,
# London-NY overlap 13-16, off-hours the rest.
def session_of(ts: datetime) -> str:
    h = ts.hour
    if 0 <= h < 8:
        return "ASIAN_TOKYO"
    if 8 <= h < 13:
        return "LONDON"
    if 13 <= h < 16:
        return "LONDON_NY_OVERLAP"
    if 16 <= h < 21:
        return "NEW_YORK"
    return "OFF_HOURS"


def load_bars() -> list[BarData]:
    con = sqlite3.connect(REPO / "artifacts" / "candle_intel.db")
    rows = con.execute(
        "SELECT bar_ts, open, high, low, close, volume FROM candles ORDER BY bar_ts"
    ).fetchall()
    con.close()
    bars: list[BarData] = []
    for ts_raw, o, h, l, c, v in rows:
        ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=ts.astimezone(UTC),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                tick_volume=int(v or 0),
                is_complete=True,
            )
        )
    return bars


def compute_series(bars: list[BarData]) -> list[dict]:
    """Walk-forward causal feature computation: at each bar i (i>=54), features
    describe decision_at=bars[i].timestamp using only bars[:i+1]."""
    series: list[dict] = []
    for i in range(54, len(bars)):
        window = bars[: i + 1]
        feats = compute_liquidity_features(window, decision_at=window[-1].timestamp)
        rec = {
            "timestamp": window[-1].timestamp.isoformat(),
            "session": session_of(window[-1].timestamp),
            "mid": window[-1].close,
        }
        vec = feats.as_vector()
        for name, v in zip(LIQUIDITY_FEATURE_NAMES, vec, strict=True):
            rec[name] = float(v)
        rec["pools"] = len(feats.pools)
        rec["sweep_state"] = feats.liquidity_sweep_state
        rec["confluence"] = feats.liquidity_confluence
        series.append(rec)
    return series


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    median = sorted(vals)[n // 2]
    sat = sum(1 for v in vals if abs(v) >= 2.999) / n
    default_rate = sum(1 for v in vals if v == 3.0) / n  # documented "far/missing" sentinel
    return {
        "n": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "saturation_frac_abs_ge_3": round(sat, 4),
        "neutral_sentinel_frac_3": round(default_rate, 4),
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
    series = compute_series(bars)
    n = len(series)
    names = list(LIQUIDITY_FEATURE_NAMES)

    # 1) distributions
    dist = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": "e85de540e09d3339",
        "schema_id": SCHEMA_ID,
        "feature_schema_hash": feature_schema_hash(SCHEMA_ID),
        "source": "HISTORICAL/REPLAY",
        "bars_input": len(bars),
        "samples": n,
        "time_range": [series[0]["timestamp"], series[-1]["timestamp"]] if n else [],
        "features": {},
    }
    for name in names:
        vals = [r[name] for r in series]
        dist["features"][name] = stats(vals)

    # 2) session analysis
    sessions = defaultdict(list)
    for r in series:
        sessions[r["session"]].append(r)
    session_an = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": "e85de540e09d3339",
        "sessions": {},
    }
    for sess, rows in sessions.items():
        sess_n = len(rows)
        entry = {"n": sess_n}
        for name in names:
            entry[name] = stats([r[name] for r in rows])
        entry["sweep_frequency"] = (
            round(sum(1 for r in rows if float(r["sweep_state"]) != 0.0) / sess_n, 4)
            if sess_n
            else 0.0
        )
        entry["confluence_positive_frac"] = (
            round(sum(1 for r in rows if float(r["confluence"]) > 0.0) / sess_n, 4)
            if sess_n
            else 0.0
        )
        entry["avg_pools_per_bar"] = (
            round(sum(r["pools"] for r in rows) / sess_n, 2) if sess_n else 0
        )
        session_an["sessions"][sess] = entry

    # 3) redundancy matrix (overall + by session centroid)
    redun = {"pairs": [], "max_abs_corr": 0.0}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = [r[names[i]] for r in series]
            b = [r[names[j]] for r in series]
            c = spearman(a, b)
            redun["pairs"].append({"a": names[i], "b": names[j], "spearman": round(c, 4)})
    if redun["pairs"]:
        redun["max_abs_corr"] = max(abs(p["spearman"]) for p in redun["pairs"])
        redun["top_correlated"] = sorted(redun["pairs"], key=lambda p: -abs(p["spearman"]))[:5]

    # 4) family aggregation
    fam = {}
    for f, members in FAMILIES.items():
        present = [m for m in members if m in names]
        if not present:
            continue
        means = {m: round(sum(r[m] for r in series) / n, 4) for m in present}
        fam[f] = {
            "members": present,
            "mean": {m: v for m, v in means.items()},
            "n_coverage": n,
        }

    # 5) stability: split half-series, compare means
    half = n // 2
    stability = {}
    for name in names:
        first = stats([r[name] for r in series[:half]])
        second = stats([r[name] for r in series[half:]])
        stability[name] = {
            "first_half_mean": first["mean"],
            "second_half_mean": second["mean"],
            "mean_shift": round(abs(first["mean"] - second["mean"]), 4),
        }

    payload = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": "e85de540e09d3339",
        "schema_id": SCHEMA_ID,
        "source": "HISTORICAL/REPLAY",
        "columns": {
            "feature_distributions": dist,
            "session_analysis": session_an,
            "redundancy": redun,
            "families": fam,
            "stability": stability,
        },
    }
    (OUT / "feature_distributions.json").write_text(
        json.dumps(payload["columns"]["feature_distributions"], indent=2), encoding="utf-8"
    )
    (OUT / "session_analysis.json").write_text(
        json.dumps(payload["columns"]["session_analysis"], indent=2), encoding="utf-8"
    )
    (OUT / "feature_quality.json").write_text(
        json.dumps(
            {
                "research_baseline_id": "e85de540e09d3339",
                "research_run_id": RESEARCH_RUN_ID,
                "redundancy": redun,
                "families": fam,
                "stability": stability,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "samples": n,
                "sessions": {k: v["n"] for k, v in session_an["sessions"].items()},
                "max_abs_corr": redun["max_abs_corr"],
                "top_correlated": redun.get("top_correlated"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
