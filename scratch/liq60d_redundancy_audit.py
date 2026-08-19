"""TASK-04-70D-MODEL-VALIDATION — Liquidity-vs-Base feature redundancy audit (brief 9).

Executable TODAY: compute the 50D base + 10D liquidity vectors on the same
deterministic windows and report Pearson/Spearman between every liquidity
feature and every base feature (max |corr|, near-duplicate detection).

Purpose: does the Liquidity block add NEW information or merely re-encode
the existing 50D structure? Flag |r| >= 0.85 as near-duplicate (redundant).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np

from nexus_scalp.features.liquidity_engine import (
    LIQUIDITY_FEATURE_NAMES,
    compute_liquidity_features,
)
from nexus_scalp.features.scalp_features import (
    FEATURE_NAMES,
    ScalpFeatureEngine,
)
from nexus_scalp.domain.models import TickData
from tests.helpers.liquidity_fixtures import (
    bar,
    steady_bars,
    swing_high_bars,
    swing_low_bars,
)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    if denom == 0.0:
        return 0.0
    return float((a * b).sum() / denom)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return _pearson(ra, rb)


def _sweep_bars() -> list:
    rng = np.random.default_rng(42)
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars, price = [], 3300.0
    for i in range(70):
        drift = 0.08 if i < 45 else -0.12
        o = price
        c = o + drift + float(rng.normal(0, 0.02))
        h = max(o, c) + 0.15
        l = min(o, c) - 0.15
        if i == 55:
            h = max(h, 3350.0)
            c = 3348.0
        bars.append(bar(i, t0, o, h, l, c, vol=150 if i != 55 else 600))
        price = c
    return bars


def _volatile_bars() -> list:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars, base = [], 3300.0
    for i in range(90):
        o = base
        up = i % 2 == 0
        c = base + (0.4 if up else -0.4)
        h = max(o, c) + 0.6
        l = min(o, c) - 0.6
        bars.append(bar(i, t0, o, h, l, c, vol=300))
        base = c
    return bars


def _random_walk(seed: int = 1) -> list:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars, price = [], 3300.0
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
        "RANDOM_WALK": _random_walk(),
    }
    base_rows: list[np.ndarray] = []
    liq_rows: list[np.ndarray] = []
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    for name, bars in regimes.items():
        for start in range(0, max(len(bars) - 55, 1), 5):
            win = bars[start : start + 56]
            if len(win) < 56:
                continue
            last = win[-1]
            tick = TickData(
                symbol="XAUUSD",
                timestamp=last.timestamp,
                bid=float(last.close),
                ask=float(last.close) + 0.5,
                volume=int(last.tick_volume or 0),
            )
            try:
                fv = engine.compute_from_bars(win, tick)
                x50 = np.asarray(fv.to_tensor_input(), dtype=float)
                lf = compute_liquidity_features(
                    win,
                    decision_at=last.timestamp,
                    mid_price=float(last.close),
                    atr=fv.atr_m1,
                )
                xl = np.asarray(lf.as_vector(), dtype=float)
                base_rows.append(x50)
                liq_rows.append(xl)
            except Exception:
                continue
    B = np.vstack(base_rows)
    L = np.vstack(liq_rows)

    report: dict = {}
    flag_threshold = 0.85
    for j, lname in enumerate(LIQUIDITY_FEATURE_NAMES):
        pearson_max = 0.0
        spearman_max = 0.0
        best_base = ""
        for i, bname in enumerate(FEATURE_NAMES):
            pc = _pearson(B[:, i], L[:, j])
            sc = _spearman(B[:, i], L[:, j])
            if abs(pc) > abs(pearson_max):
                pearson_max, best_pearson = pc, bname
            if abs(sc) > abs(spearman_max):
                spearman_max, best_spearman = sc, bname
        report[lname] = {
            "best_pearson_with": best_pearson,
            "pearson": round(pearson_max, 4),
            "best_spearman_with": best_spearman,
            "spearman": round(spearman_max, 4),
            "near_duplicate": max(abs(pearson_max), abs(spearman_max)) >= flag_threshold,
        }
    report["_meta"] = {
        "probe": "TASK-04 brief-9 liquidity-vs-base redundancy audit",
        "vectors": int(B.shape[0]),
        "flag_threshold_abs_corr": flag_threshold,
        "note": "SYNTHETIC deterministic regimes — structure informative; real "
                "market rates need the 70D dataset (TASK-3).",
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    return report


if __name__ == "__main__":
    rep = main()
    with open("scratch/liq60d_redundancy_audit.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(f"{'feature':<28}{'best-base':<28}{'pearson':>9}{'spearman':>9}  flag")
    for name, d in rep.items():
        if name.startswith("_"):
            continue
        flag = "NEAR-DUP" if d["near_duplicate"] else ""
        print(
            f"{name:<28}{d['best_pearson_with']:<28}{d['pearson']:>9}{d['spearman']:>9}  {flag}"
        )
    print(f"\nvectors: {rep['_meta']['vectors']}")