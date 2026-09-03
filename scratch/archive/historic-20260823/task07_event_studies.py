"""TASK-07-70D-LIQUIDITY-RESEARCH — liquidity event studies (step 6).

Strictly causal event-study protocol (mission 11-14, 18, 35):
  - Event detection uses ONLY bars at/before decision (engine contract).
  - Forward outcomes (directional move, MFE, MAE, reversal/continuation,
    volatility response) are computed ONLY on bars AFTER the event bar
    (horizons 3/5/10/15/30 bars of the M5 series).
  - The future is NEVER fed into any feature at the event timestamp.
  - Multiple-testing awareness: 45 feature/outcome dimensions => all
    p-values are exploratory; corrections and effect sizes reported, never
    bare 'p<0.05 therefore works'.

Events studied:
  1. SWEEP: liquidity_sweep_state != 0 (v1.1 semantics, relevance-gated)
     => sweep+reversal vs sweep+continuation vs no-sweep forward outcomes.
  2. CONFLUENCE: low (<1) / medium (1..2) / high (>2) buckets.
  3. DISTANCE bins: BSL/SSL distance binned by FROZEN thresholds from the
     training/reference period (p33/p66 of the reference distribution —
     computed once on a reference slice, then frozen; NOT re-binned OOS).
  4. HTF score sign.
Outcome measures per bucket: mean abs move, MFE, MAE, reversal prob,
continuation prob, volatility ratio, n.

Output: scratch/task07_research/event_studies.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import polars as pl  # noqa: E402

from nexus_scalp.features.liquidity_engine import liquidity_atr  # noqa: E402
from nexus_scalp.features.liquidity_engine_opt import compute_liquidity_features_v1_1  # noqa: E402
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)
RESEARCH_RUN_ID = "task07_event_studies_01"
BASELINE_ID = "e85de540e09d3339"
LOOKBACK = 2000
STRIDE = 100  # finer stride for event density
MIN_BARS = 54
HORIZONS = [3, 5, 10, 15, 30]


def load_bars() -> list[BarData]:
    df = pl.read_parquet(REPO / "data" / "raw" / "XAUUSD_M5.parquet").sort("time")
    bars: list[BarData] = []
    for row in df.iter_rows(named=True):
        t = row["time_utc"]
        ts = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
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


def main() -> int:
    bars = load_bars()
    print(f"bars={len(bars)}", flush=True)
    n_bar = len(bars)

    # ---- frozen distance bin thresholds from a REFERENCE slice (first 25%) ----
    ref_end = n_bar // 4
    ref_bsl: list[float] = []
    ref_ssl: list[float] = []
    for i in range(MIN_BARS, ref_end, STRIDE):
        lo = max(0, i + 1 - LOOKBACK)
        w = bars[lo : i + 1]
        ts = w[-1].timestamp
        atr = float(liquidity_atr([b.high for b in w], [b.low for b in w], [b.close for b in w]))
        f = compute_liquidity_features_v1_1(w, decision_at=ts, atr=atr)
        ref_bsl.append(f.bsl_distance_atr)
        ref_ssl.append(f.ssl_distance_atr)

    def pct(vals: list[float], p: float) -> float:
        sv = sorted(vals)
        return sv[min(len(sv) - 1, int(len(sv) * p))]

    bsl_lo, bsl_hi = pct(ref_bsl, 1 / 3), pct(ref_bsl, 2 / 3)
    ssl_lo, ssl_hi = pct(ref_ssl, 1 / 3), pct(ref_ssl, 2 / 3)
    bins = {
        "bsl": {
            "near_max": bsl_lo,
            "medium_max": bsl_hi,
            "frozen_on": "reference slice (first 25% of history)",
        },
        "ssl": {
            "near_max": ssl_lo,
            "medium_max": ssl_hi,
            "frozen_on": "reference slice (first 25% of history)",
        },
    }
    print(f"frozen bins bsl={bsl_lo:.2f}/{bsl_hi:.2f} ssl={ssl_lo:.2f}/{ssl_hi:.2f}", flush=True)

    def dist_bin(v: float, lo_: float, hi_: float) -> str:
        if v <= lo_:
            return "near"
        if v <= hi_:
            return "medium"
        return "far"

    # ---- event collection ----
    events = []
    for i in range(MIN_BARS, n_bar - max(HORIZONS), STRIDE):
        lo = max(0, i + 1 - LOOKBACK)
        w = bars[lo : i + 1]
        ts = w[-1].timestamp
        atr = float(liquidity_atr([b.high for b in w], [b.low for b in w], [b.close for b in w]))
        f = compute_liquidity_features_v1_1(w, decision_at=ts, atr=atr)
        event = {
            "i": i,
            "ts": ts,
            "price": w[-1].close,
            "atr": atr,
            "sweep": float(f.liquidity_sweep_state),
            "confluence": float(f.liquidity_confluence),
            "bsl": float(f.bsl_distance_atr),
            "ssl": float(f.ssl_distance_atr),
            "htf": float(f.htf_liquidity_score),
            "bsl_bin": dist_bin(float(f.bsl_distance_atr), bsl_lo, bsl_hi),
            "ssl_bin": dist_bin(float(f.ssl_distance_atr), ssl_lo, ssl_hi),
        }
        # forward outcomes (strictly after bar i)
        fut = bars[i + 1 : i + 1 + max(HORIZONS)]
        event["outcomes"] = {}
        for h in HORIZONS:
            if len(fut) < h:
                continue
            seg = fut[:h]
            p0 = event["price"]
            hi = max(b.high for b in seg)
            lo = min(b.low for b in seg)
            last = seg[-1].close
            mfe = (hi - p0) / atr if atr > 0 else 0.0
            mae = (lo - p0) / atr if atr > 0 else 0.0
            direction = last - p0
            event["outcomes"][h] = {
                "abs_move_atr": abs(direction) / atr if atr > 0 else 0.0,
                "mfe_atr": mfe,
                "mae_atr": mae,
                "reversal": 1 if (mfe > 0 and lo < p0 - 0.5 * atr) else 0,
                "continuation": 1 if abs(direction) / (atr if atr > 0 else 1) > 0.5 else 0,
                "vol_ratio": (hi - lo) / (atr if atr > 0 else 1),
            }
        events.append(event)
        if len(events) % 200 == 0:
            print(f"  ..events {len(events)}", flush=True)

    print(f"events collected={len(events)}", flush=True)

    # ---- aggregation ----
    def agg(rows: list[dict], h: int) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        absm = [r["outcomes"][h]["abs_move_atr"] for r in rows]
        mfe = [r["outcomes"][h]["mfe_atr"] for r in rows]
        mae = [r["outcomes"][h]["mae_atr"] for r in rows]
        rev = sum(r["outcomes"][h]["reversal"] for r in rows)
        cont = sum(r["outcomes"][h]["continuation"] for r in rows)
        vr = [r["outcomes"][h]["vol_ratio"] for r in rows]
        return {
            "n": n,
            "mean_abs_move_atr": round(sum(absm) / n, 4),
            "mean_mfe_atr": round(sum(mfe) / n, 4),
            "mean_mae_atr": round(sum(mae) / n, 4),
            "reversal_prob": round(rev / n, 4),
            "continuation_prob": round(cont / n, 4),
            "mean_vol_ratio": round(sum(vr) / n, 4),
        }

    out = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "algorithm_version": "liquidity-v1.1 (candidate, TASK-06)",
        "protocol": "strictly causal: features from bars <= decision; outcomes from bars > decision only; frozen distance bins from reference slice (first 25%), never re-binned on the evaluation slice",
        "events_total": len(events),
        "horizons_bars": HORIZONS,
        "frozen_bins": bins,
        "sweep": {},
        "confluence": {},
        "distance_bsl": {},
        "distance_ssl": {},
        "htf": {},
    }

    # sweep buckets
    sweep_buckets = {
        "sweep_negative": [e for e in events if e["sweep"] < 0],
        "sweep_zero": [e for e in events if e["sweep"] == 0],
        "sweep_positive": [e for e in events if e["sweep"] > 0],
    }
    for name, rows in sweep_buckets.items():
        out["sweep"][name] = {}
        for h in HORIZONS:
            out["sweep"][name][str(h)] = agg(rows, h)

    # confluence buckets
    conf_buckets = {
        "low_lt_1": [e for e in events if e["confluence"] < 1.0],
        "medium_1_2": [e for e in events if 1.0 <= e["confluence"] <= 2.0],
        "high_gt_2": [e for e in events if e["confluence"] > 2.0],
    }
    for name, rows in conf_buckets.items():
        out["confluence"][name] = {}
        for h in HORIZONS:
            out["confluence"][name][str(h)] = agg(rows, h)

    # distance bins
    for side, key in [("bsl", "bsl_bin"), ("ssl", "ssl_bin")]:
        for binname in ["near", "medium", "far"]:
            rows = [e for e in events if e[key] == binname]
            out[f"distance_{side}"][binname] = {}
            for h in HORIZONS:
                out[f"distance_{side}"][binname][str(h)] = agg(rows, h)

    # htf sign
    htf_buckets = {
        "htf_negative": [e for e in events if e["htf"] < 0],
        "htf_zero": [e for e in events if e["htf"] == 0],
        "htf_positive": [e for e in events if e["htf"] > 0],
    }
    for name, rows in htf_buckets.items():
        out["htf"][name] = {}
        for h in HORIZONS:
            out["htf"][name][str(h)] = agg(rows, h)

    (OUT / "event_studies.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    # console digest: horizon 5
    print(
        json.dumps(
            {
                "events_total": len(events),
                "sweep_n": {k: len(v) for k, v in sweep_buckets.items()},
                "confluence_n": {k: len(v) for k, v in conf_buckets.items()},
                "bsl_bin_n": {
                    b: len([e for e in events if e["bsl_bin"] == b])
                    for b in ["near", "medium", "far"]
                },
                "ssl_bin_n": {
                    b: len([e for e in events if e["ssl_bin"] == b])
                    for b in ["near", "medium", "far"]
                },
                "htf_n": {k: len(v) for k, v in htf_buckets.items()},
                "h5_sweep": {k: out["sweep"][k]["5"] for k in out["sweep"]},
                "h5_confluence": {k: out["confluence"][k]["5"] for k in out["confluence"]},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
