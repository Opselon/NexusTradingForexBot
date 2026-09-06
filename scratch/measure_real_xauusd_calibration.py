"""Priority 3: measure REAL XAUUSD historical data found in the repo and
compare it against the synthetic PAPER generator's statistical fingerprint.

Real data located (measured, not claimed):
  - scratch/data/forensic_50d_live_T1_bars.json           900 M1 bars (2026-08-18, broker XAUUSD, 50d-forensic capture)
  - scratch/archive/historic-20260823/forensic_50d_live_T{1,2,3}_bars.json (same capture, archived copies)

Metrics (same family as the synthetic 5000-tick profile):
  return distribution (mean, sd, skew, kurtosis), |ret| volatility
  clustering (ACF lag 1..10), return autocorrelation, spread (points and
  cents), session mix, trend/range persistence (up/down run lengths +
  sign autocorr), shock frequency (>3sd and >1% moves), gaps.

Outputs JSON to scratch/calibration/xauusd_real_calibration.json and
prints a real-vs-synthetic comparison table.

Run:  python scratch/measure_real_xauusd_calibration.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REAL_SOURCES = [
    ROOT / "scratch/data/forensic_50d_live_T1_bars.json",
    ROOT / "scratch/archive/historic-20260823/forensic_50d_live_T1_bars.json",
    ROOT / "scratch/archive/historic-20260823/forensic_50d_live_T2_bars.json",
    ROOT / "scratch/archive/historic-20260823/forensic_50d_live_T3_bars.json",
]
# XAUUSD quoted with 2 digits; broker "spread" column is in POINTS (0.01).
POINT = 0.01


def load_bars(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    bars_any: list[dict] = d.get("bars", d) if isinstance(d, dict) else d  # type: ignore[assignment]
    return sorted(bars_any, key=lambda b: b["time"])


def acf(xs: list[float], lag: int) -> float:
    """Autocorrelation of a series at a given lag (population)."""
    n = len(xs)
    if n <= lag + 1:
        return 0.0
    m = statistics.fmean(xs)
    num = sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, n))
    den = sum((x - m) ** 2 for x in xs)
    return num / den if den else 0.0


def bar_metrics(bars: list[dict]) -> dict:
    closes = [float(b["close"]) for b in bars]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets)
    # skew / excess kurtosis
    skew = (sum((r - mean) ** 3 for r in rets) / n) / (sd**3) if sd > 0 else 0.0
    kurt = (sum((r - mean) ** 4 for r in rets) / n) / (sd**4) - 3.0 if sd > 0 else 0.0
    abs_rets = [abs(r) for r in rets]

    spreads_pts = [float(b["spread"]) for b in bars]
    tvols = [float(b.get("tick_volume", 0)) for b in bars]

    # session mix (UTC hours; London 07-16, NY 12-21 overlap 12-16)
    hours = [datetime.fromisoformat(b["time"]).hour for b in bars]
    hour_counts = Counter(hours)
    asia = sum(v for h, v in hour_counts.items() if 0 <= h < 7)
    london = sum(v for h, v in hour_counts.items() if 7 <= h < 12)
    overlap = sum(v for h, v in hour_counts.items() if 12 <= h < 17)
    ny = sum(v for h, v in hour_counts.items() if 17 <= h < 21)
    late = sum(v for h, v in hour_counts.items() if h >= 21)

    # trend/range persistence: sign autocorrelation + mean up/down run length
    signs = [1 if r > 0 else (-1 if r < 0 else 0) for r in rets]
    runs: list[int] = []
    cur = 1
    for i in range(1, len(signs)):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    pos_share = sum(1 for s in signs if s > 0) / max(n, 1)

    # shock frequency
    shocks_3sd = sum(1 for r in rets if sd > 0 and abs(r) > 3 * sd)
    shocks_1pct = sum(1 for r in rets if abs(r) > 0.01)

    # gaps: expected 60s between M1 bars
    times = [datetime.fromisoformat(b["time"]) for b in bars]
    gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
    gap_counts = Counter(int(g) for g in gaps)
    gaps_gt_60 = sum(v for g, v in gap_counts.items() if g > 60)

    ranges = [float(b["high"]) - float(b["low"]) for b in bars]

    return {
        "n_bars": len(bars),
        "time_from": bars[0]["time"],
        "time_to": bars[-1]["time"],
        "price_range": [min(closes), max(closes)],
        "ret_log": {
            "mean_per_bar": mean,
            "sd_per_bar": sd,
            "ann_vol_proxy_pct": sd * math.sqrt(252 * 1440) * 100,  # M1 -> annualized
            "skew": skew,
            "excess_kurtosis": kurt,
        },
        "acf_ret_lag1": acf(rets, 1),
        "acf_ret_lag5": acf(rets, 5),
        "acf_absret": {f"lag{k}": round(acf(abs_rets, k), 4) for k in (1, 2, 3, 5, 10)},
        "spread_points": {
            "min": min(spreads_pts),
            "p50": statistics.median(spreads_pts),
            "mean": statistics.fmean(spreads_pts),
            "p95": sorted(spreads_pts)[int(0.95 * len(spreads_pts))],
            "max": max(spreads_pts),
            "mean_cents": statistics.fmean(spreads_pts) * POINT,
        },
        "session_bars_utc": {
            "asia_00_07": asia,
            "london_07_12": london,
            "ldn_ny_overlap_12_17": overlap,
            "ny_17_21": ny,
            "late_21_24": late,
        },
        "trend_persistence": {
            "sign_autocorr_lag1": acf([float(s) for s in signs], 1),
            "pct_up_bars": pos_share,
            "mean_run_len": statistics.fmean(runs),
            "max_run_len": max(runs),
        },
        "shocks": {
            "gt_3sd_count": shocks_3sd,
            "gt_3sd_pct": 100.0 * shocks_3sd / max(n, 1),
            "gt_1pct_move_count": shocks_1pct,
            "bar_range_mean": statistics.fmean(ranges),
            "bar_range_p95": sorted(ranges)[int(0.95 * len(ranges))],
        },
        "gaps": {
            "non_60s_bars": gaps_gt_60,
            "gap_distribution_top": dict(
                sorted(gap_counts.items(), key=lambda kv: -kv[1])[:4]
            ),
        },
        "tick_volume": {"p50": statistics.median(tvols), "mean": statistics.fmean(tvols)},
    }


def synthetic_metrics_5000_ticks() -> dict:
    """Same-metric fingerprint of the seeded synthetic generator (5000 ticks),
    computed from paper_stress.generate_ticks(seed=42) + the adapter AR(1)."""
    from nexus_scalp.market_data.paper_stress import Regime, generate_ticks

    ticks = generate_ticks(
        5000,
        [Regime.RANGE, Regime.TREND_UP, Regime.VOLATILE, Regime.TREND_DOWN],
        seed=42,
        symbol="XAUUSD",
    )
    mids = [(float(t.bid) + float(t.ask)) / 2.0 for t in ticks]
    rets = [math.log(mids[i] / mids[i - 1]) for i in range(1, len(mids))]
    n = len(rets)
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets)
    skew = (sum((r - mean) ** 3 for r in rets) / n) / (sd**3) if sd > 0 else 0.0
    kurt = (sum((r - mean) ** 4 for r in rets) / n) / (sd**4) - 3.0 if sd > 0 else 0.0
    abs_rets = [abs(r) for r in rets]
    spreads = [float(t.ask) - float(t.bid) for t in ticks]
    # ticks are 250ms apart in the generator -> gaps from timestamps
    return {
        "n_ticks": len(ticks),
        "ret_log": {
            "mean_per_tick": mean,
            "sd_per_tick": sd,
            "skew": skew,
            "excess_kurtosis": kurt,
        },
        "acf_ret_lag1": acf(rets, 1),
        "acf_absret_lag1": acf(abs_rets, 1),
        "spread_cents": {"min": min(spreads), "mean": statistics.fmean(spreads), "max": max(spreads)},
    }


def main() -> None:
    found = {p: p.exists() for p in REAL_SOURCES}
    real_bars: list[dict] = []
    used: list[str] = []
    for p, ok in found.items():
        if ok:
            bars = load_bars(p)
            # T1 == scratch/data copy; skip exact duplicates so bars are not double-counted
            if real_bars and bars[0]["time"] == real_bars[0]["time"]:
                continue
            real_bars.extend(bars)
            used.append(str(p.relative_to(ROOT)))
    real_bars.sort(key=lambda b: b["time"])

    result: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "calibration_status": "MEASURED_FROM_REAL_DATA" if real_bars else "UNKNOWN_NO_REAL_DATA",
        "sources": used,
        "dedupe_note": "T1 duplicate (scratch/data vs scratch/archive) counted once",
    }
    if real_bars:
        # drop duplicate overlapping windows by (time) key
        seen: dict[str, dict] = {}
        for b in real_bars:
            seen[b["time"]] = b
        uniq = sorted(seen.values(), key=lambda b: b["time"])
        result["real"] = bar_metrics(uniq)
        result["n_bars_total_unique"] = len(uniq)
    else:
        result["real"] = None

    try:
        result["synthetic_5000_ticks_seed42"] = synthetic_metrics_5000_ticks()
    except Exception as exc:  # pragma: no cover
        result["synthetic_5000_ticks_seed42"] = {"error": repr(exc)}

    out = ROOT / "scratch/calibration"
    out.mkdir(parents=True, exist_ok=True)
    (out / "xauusd_real_calibration.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
