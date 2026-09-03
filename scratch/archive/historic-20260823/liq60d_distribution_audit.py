"""TASK-04-70D-MODEL-VALIDATION — Liquidity feature distribution audit (brief 8).

Executable TODAY (no 70D dataset required): computes the 10 Liquidity
features across deterministic market regimes (trending, ranging, volatility
bursts, sweep events) using the TASK-01 engine + fixtures, then reports the
full distribution audit: min/max/mean/median/std/p01..p99, zero_rate,
missing_rate, unique_count, constant/near-constant/saturated_at_+/-3.

Purpose: determine whether the features are technically correct AND
scientifically usable (a feature that is 95% zeros or pinned at +3 is
technically correct but useless).

Regimes (deterministic, from liquidity_fixtures):
  - TRENDING_UP:     ramp up + swing high
  - TRENDING_DOWN:   ramp down + swing low
  - RANGING:         steady flat
  - VOLATILE:        swing high + swing low sequence (wide ATR)
  - SWEEP:           stop-hunt (spike through a level then reverse)
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import numpy as np

from nexus_scalp.features.liquidity_engine import (
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from tests.helpers.liquidity_fixtures import (
    bar,
    steady_bars,
    swing_high_bars,
    swing_low_bars,
)


def _sweep_bars() -> list:
    """Stop-hunt: ramp up to a level, spike THROUGH it (sweep), reverse down."""
    rng = np.random.default_rng(42)
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = []
    price = 3300.0
    for i in range(70):
        drift = 0.08 if i < 45 else -0.12
        o = price
        c = o + drift + float(rng.normal(0, 0.02))
        h = max(o, c) + 0.15
        l = min(o, c) - 0.15
        if i == 55:  # the sweep: spike 2.5x ATR above the recent high
            h = max(h, 3350.0)
            c = 3348.0
        bars.append(
            bar(i, t0, o, h, l, c, vol=150 if i != 55 else 600)
        )
        price = c
    return bars


def _volatile_bars() -> list:
    """Wide-swing alternating highs/lows (wide ATR regime)."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = []
    base = 3300.0
    for i in range(90):
        o = base
        up = i % 2 == 0
        c = base + (0.4 if up else -0.4)
        h = max(o, c) + 0.6
        l = min(o, c) - 0.6
        bars.append(bar(i, t0, o, h, l, c, vol=300))
        base = c
    return bars


def _linspace_bars(seed: int = 1) -> list:
    """Random-walk across many regimes (for percentile coverage)."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = []
    price = 3300.0
    for i in range(400):
        o = price
        c = o + float(rng.normal(0, 0.15))
        h = max(o, c) + abs(float(rng.normal(0, 0.2)))
        l = min(o, c) - abs(float(rng.normal(0, 0.2)))
        bars.append(bar(i, t0, o, h, l, c, vol=int(rng.integers(80, 400))))
        price = c
    return bars


def main() -> dict:
    regimes = {
        "TRENDING_UP": swing_high_bars(60, 3350.0, 3300.0),
        "TRENDING_DOWN": swing_low_bars(60, 3250.0, 3300.0),
        "RANGING": steady_bars(100),
        "VOLATILE": _volatile_bars(),
        "SWEEP": _sweep_bars(),
        "RANDOM_WALK": _linspace_bars(),
    }
    per_regime: dict[str, list[list[float]]] = {}
    for name, bars in regimes.items():
        # slide the window across the series to get many vectors per regime
        vecs = []
        for start in range(0, max(len(bars) - 55, 1), 5):
            win = bars[start : start + 56]
            if len(win) < 56:
                continue
            try:
                f = compute_liquidity_features(
                    win,
                    decision_at=win[-1].timestamp,
                    mid_price=float(win[-1].close),
                )
                v = np.asarray(f.as_vector(), dtype=float)
                vecs.append(v)
            except Exception as exc:  # pragma: no cover - engine contract
                print(f"  [{name}] engine error: {exc}", file=sys.stderr)
        per_regime[name] = [v.tolist() for v in vecs]

    all_v = np.vstack([np.asarray(v) for vs in per_regime.values() for v in vs])
    report: dict[str, dict] = {}
    for j, fname in enumerate(LIQUIDITY_FEATURE_NAMES):
        col = all_v[:, j]
        finite = np.isfinite(col)
        nonzero = col[finite] != 0.0
        pcts = np.percentile(col[finite], [1, 5, 25, 50, 75, 95, 99]) if finite.any() else [0] * 7
        report[fname] = {
            "min": round(float(col[finite].min()), 4) if finite.any() else None,
            "max": round(float(col[finite].max()), 4) if finite.any() else None,
            "mean": round(float(col[finite].mean()), 4) if finite.any() else None,
            "median": round(float(np.median(col[finite])), 4) if finite.any() else None,
            "std": round(float(col[finite].std()), 4) if finite.any() else None,
            "p01": round(float(pcts[0]), 4),
            "p05": round(float(pcts[1]), 4),
            "p25": round(float(pcts[2]), 4),
            "p50": round(float(pcts[3]), 4),
            "p75": round(float(pcts[4]), 4),
            "p95": round(float(pcts[5]), 4),
            "p99": round(float(pcts[6]), 4),
            "zero_rate": round(float(1.0 - nonzero.size / finite.size), 4) if finite.size else None,
            "missing_rate": round(float(1.0 - finite.size / col.size), 4),
            "unique_count": int(np.unique(col[finite]).size) if finite.any() else 0,
            "saturated_at_minus3": round(float((col[finite] <= -3.0).mean()), 4) if finite.any() else None,
            "saturated_at_plus3": round(float((col[finite] >= 3.0).mean()), 4) if finite.any() else None,
            "constant": bool(finite.any() and np.unique(col[finite]).size == 1),
        }
        # near-constant: 99% of values identical
        if finite.any() and np.unique(col[finite]).size > 1:
            vc = np.unique(col[finite], return_counts=True)
            report[fname]["near_constant"] = bool((vc[1].max() / col[finite].size) >= 0.99)
        else:
            report[fname]["near_constant"] = False
    report["_meta"] = {
        "probe": "TASK-04 brief-8 liquidity distribution audit",
        "vectors_total": int(all_v.shape[0]),
        "feature_names": list(LIQUIDITY_FEATURE_NAMES),
        "regimes": {k: len(v) for k, v in per_regime.items()},
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "note": "SYNTHETIC deterministic regimes (fixtures) — real-market audit "
                "requires the 70D dataset (TASK-3). Distribution shape is "
                "informative; absolute rates are fixture-dependent.",
    }
    return report


if __name__ == "__main__":
    rep = main()
    out = "scratch/liq60d_distribution_audit.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    # human summary to stdout
    print(f"{'feature':<28}{'min':>8}{'max':>8}{'mean':>8}{'zero%':>8}{'sat+3%':>8}{'uniq':>6}")
    for name, d in rep.items():
        if name.startswith("_"):
            continue
        print(
            f"{name:<28}{d['min']:>8}{d['max']:>8}{d['mean']:>8}"
            f"{d['zero_rate']*100:>7.1f}%{d['saturated_at_plus3']*100:>7.1f}%{d['unique_count']:>6}"
        )
    print(f"\nvectors: {rep['_meta']['vectors_total']} | regimes: {rep['_meta']['regimes']}")