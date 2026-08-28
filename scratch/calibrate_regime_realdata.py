"""Calibration probe for MarketRegimeClassifier on REAL XAUUSD M1 data.

Loads data/raw/XAUUSD_M1.parquet (canonical project market-data source),
reconstructs a deterministic tick stream per M1 bar via a Brownian-bridge
random walk scaled to the bar's (high-low) range (preserves intrabar noise
that plain OHLC interpolation would erase), feeds the REAL classifier, and
emits auditable distribution statistics + before/after regime coverage.

Outputs JSON to scratch/calibration/regime_calibration_<ts>.json
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pyarrow.parquet as pq  # type: ignore

from nexus_scalp.domain.models import TickData  # type: ignore
from nexus_scalp.features.regime_classifier import (  # type: ignore
    MarketRegimeClassifier,
    RegimeType,
)

DATA = ROOT / "data" / "raw" / "XAUUSD_M1.parquet"
OUTDIR = ROOT / "scratch" / "calibration"
OUTDIR.mkdir(parents=True, exist_ok=True)

# ---- reconstruction params ----
BAR_SECONDS = 60
# Cap ticks/bar for runtime, but keep it HIGH enough that (a) realized vol is
# sampled finely (coarse sampling underestimates sum-of-squared-returns) and
# (b) tick_velocity reflects the REAL feed density from tick_volume.
# Real XAUUSD: ~316 ticks/min (p50) -> ~5/s. We space n ticks across 60s so
# tick_velocity ~= tv/60 (the genuine feed rate -> proves/disproves the
# tick_velocity-as-volatility thesis).
MAX_TICKS_PER_BAR = 400
RANDOM_SEED = 20260821


def reconstruct_bar_ticks(open_p, high, low, close, tv, spread_pts, start_dt, rng):
    """Return list of (dt, bid, ask) for one M1 bar using a Brownian bridge
    from open->close whose excursion reaches ~ (high-low).

    n ticks are spaced across the 60s bar at the REAL feed density
    (n ~= tv, capped), so tick arrival rate reflects genuine XAUUSD feed
    activity rather than a fixed cadence.
    """
    n = max(2, min(MAX_TICKS_PER_BAR, int(tv)))
    half = max(spread_pts, 1) * 0.01 / 2.0
    rng_range = max(high - low, 1e-6)
    # step volatility chosen so a random walk of n steps spans ~rng_range
    step_sigma = rng_range / math.sqrt(max(n, 1)) / 2.0
    prices = [open_p]
    cur = open_p
    drift = (close - open_p) / n
    for i in range(1, n):
        target = open_p + drift * i
        noise = rng.gauss(0.0, step_sigma)
        cur = target + (cur - target) * 0.5 + noise
        if cur > high:
            cur = high - (cur - high) * 0.3
        if cur < low:
            cur = low + (low - cur) * 0.3
        prices.append(cur)
    prices[-1] = close
    ticks = []
    step_sec = BAR_SECONDS / max(n - 1, 1)
    for i in range(n):
        mid = prices[i]
        dt = start_dt + timedelta(seconds=(i * step_sec))
        ticks.append((dt, mid - half, mid + half))
    return ticks


def load_bars(limit=None):
    t = pq.read_table(str(DATA))
    d = t.to_pydict()
    cols = {k: d[k] for k in ["time", "open", "high", "low", "close", "tick_volume", "spread"]}
    n = len(cols["time"])
    if limit:
        n = min(n, limit)
    return {
        "open": cols["open"][:n],
        "high": cols["high"][:n],
        "low": cols["low"][:n],
        "close": cols["close"][:n],
        "tv": cols["tick_volume"][:n],
        "spread": [max(s, 1) for s in cols["spread"][:n]],
        "time": cols["time"][:n],
    }


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def run(limit, cfg_overrides=None):
    bars = load_bars(limit)
    rng = random.Random(RANDOM_SEED)
    clf = MarketRegimeClassifier(**(cfg_overrides or {}))
    regime_counts = Counter()
    candidate_counts = Counter()
    rv_list, tv_list, sp_list, ofi_list = [], [], [], []
    transitions = 0
    prev_regime = None
    dwell = defaultdict(list)
    cur_start = None
    t0 = time.time()
    base_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    for i in range(len(bars["open"])):
        start_dt = base_dt + timedelta(seconds=bars["time"][i])
        ticks = reconstruct_bar_ticks(
            bars["open"][i], bars["high"][i], bars["low"][i], bars["close"][i],
            bars["tv"][i], bars["spread"][i], start_dt, rng,
        )
        for dt, bid, ask in ticks:
            st = clf.classify_tick(TickData(
                symbol="XAUUSD", timestamp=dt, bid=bid, ask=ask,
                last=(bid + ask) / 2, volume=1.0,
            ))
        # sample metrics at bar end (after warmup)
        if i > 30:
            regime_counts[st.regime_type] += 1
            rv_list.append(st.realized_volatility_5m)
            tv_list.append(st.tick_velocity_per_sec)
            sp_list.append(st.current_spread_usd)
            ofi_list.append(st.order_flow_imbalance)
            # candidate (pre-hysteresis) regime for stickiness diagnostics
            cand, _, _, _ = clf._candidate_regime(
                is_macro_news=False,
                spread=st.current_spread_usd,
                rv_5m=st.realized_volatility_5m,
                tick_velocity=st.tick_velocity_per_sec,
                norm_ofi=st.order_flow_imbalance,
            )
            candidate_counts[cand] += 1
            if prev_regime is not None and st.regime_type != prev_regime:
                transitions += 1
                if cur_start is not None:
                    dwell[prev_regime].append(i - cur_start)
                cur_start = i
            if prev_regime != st.regime_type:
                cur_start = i
            prev_regime = st.regime_type
    elapsed = time.time() - t0
    total = sum(regime_counts.values()) or 1

    def summ(name, xs):
        if not xs:
            return {"min": 0, "p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0, "n": 0}
        xs = sorted(xs)
        return {
            "min": round(xs[0], 6), "p50": round(pct(xs, .5), 6),
            "p75": round(pct(xs, .75), 6), "p90": round(pct(xs, .9), 6),
            "p95": round(pct(xs, .95), 6), "p99": round(pct(xs, .99), 6),
            "max": round(xs[-1], 6), "n": len(xs),
        }

    return {
        "bars_processed": len(bars["open"]),
        "classify_seconds": round(elapsed, 2),
        "regime_distribution": {k.value: v for k, v in regime_counts.items()},
        "candidate_distribution": {k.value: v for k, v in candidate_counts.items()},
        "candidate_pct": {k.value: round(100.0 * v / total, 2) for k, v in candidate_counts.items()},
        "regime_pct": {k.value: round(100.0 * v / total, 2) for k, v in regime_counts.items()},
        "transitions": transitions,
        "metrics": {
            "rv_5m": summ("rv", rv_list),
            "tick_velocity": summ("tv", tv_list),
            "spread_usd": summ("sp", sp_list),
            "norm_ofi": summ("ofi", ofi_list),
        },
    }


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 12000
    print(f"Running calibration on {limit} bars of REAL XAUUSD M1...", flush=True)
    res = run(limit)
    print(json.dumps(res, indent=2))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTDIR / f"regime_calibration_before_{ts}.json"
    out.write_text(json.dumps(res, indent=2))
    print(f"\nWROTE {out}")
