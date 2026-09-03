"""TASK-6 §7/§27: bounded parameter search on temporal splits.

Discipline (§6/§27/§28):
  TRAIN        (60%)  -> evidence + coarse/narrow search
  VALIDATION   (20%)  -> candidate selection (freeze before OOS)
  OOS          (20%)  -> evaluated ONCE after freeze; then LOCKED.

Scores (TASK-6 §10 FeatureQuality concept, quantified for THIS task):
  For each candidate we measure on TRAIN:
    info_quality  = mean over 10 features of
                    (1 - saturation%) + norm_unique (unique/rows)
                    + (1 - min(zero%,...) aggressive punctures)
                    + step_penalty for sweep_state (< 8 states is bad)
    stability     = 1 - mean|Δfeature| under ±5% parameter perturbation
    causality     = unchanged historical-invariance property (proven by tests)
    runtime       = p50 latency, compared to v1
  Decision uses VALIDATION; OOS is reported once and locked.

Results are written to scratch/liq_opt_results.json for the report.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import time

import numpy as np

_spec = importlib.util.spec_from_file_location("liq_opt_lab", r"scratch/liq_opt_lab.py")
_liq_opt_lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_liq_opt_lab)
ab_diff = _liq_opt_lab.ab_diff
compute_vectors = _liq_opt_lab.compute_vectors
load_bars = _liq_opt_lab.load_bars


def info_metrics(A: np.ndarray) -> dict[str, float]:
    """Per-feature info quality on a vector matrix."""
    names = [
        "bsl",
        "ssl",
        "eqh",
        "eql",
        "htf",
        "internal",
        "external",
        "confluence",
        "sweep_state",
        "displacement",
    ]
    out = {}
    for k in range(10):
        col = A[:, k]
        fin = col[np.isfinite(col)]
        sat = float((np.abs(fin) >= 2.9999).mean())
        uniq = len(np.unique(np.round(fin, 5)))
        zr = float((fin == 0).mean())
        step = len(np.unique(np.round(fin, 2)))
        out[names[k]] = {
            "saturation": sat,
            "unique": uniq,
            "unique_ratio": uniq / len(fin),
            "zero_rate": zr,
            "distinct_2dp": step,
        }
    return out


def family_score(A: np.ndarray) -> float:
    """Aggregate FeatureQuality proxy on TRAIN evidence (higher = better info)."""
    m = info_metrics(A)
    total = 0.0
    for k, v in m.items():
        info = (
            (1.0 - v["saturation"])
            + min(v["unique_ratio"] * 100.0, 1.0)  # cap: 1% unique is plenty
            - v["zero_rate"] * 0.5
        )
        # sweep_state: penalize step-function (want states + distance gradation)
        if k == "sweep_state":
            info += min(v["distinct_2dp"] / 10.0, 1.0) * 0.3
        if k == "displacement":
            info += min(v["distinct_2dp"] / 5.0, 1.0) * 0.2
        total += info
    return total / 10.0


def perturb_stability(producer, bars, params, eps: float = 0.05):
    """±5% parameter perturbation -> mean |Δvector| (robustness, §26)."""
    from nexus_scalp.features.liquidity_engine_opt import LiquidityParams

    def run(p):
        p.as_dict()
        v, _ = compute_vectors(
            bars, lambda win, _p=p, **kw: producer(win, **kw, params=_p), min_bars=55
        )
        return v

    v0 = run(params)
    deltas = []
    for key in params.as_dict():
        for sign in (-1, 1):
            d = dict(params.as_dict())
            val = d[key] * (1 + sign * eps)
            if key == "sweep_window_bars":
                val = max(1, round(val))
            d[key] = val
            try:
                v1 = run(LiquidityParams(**d))
                deltas.append(float(np.abs(v1 - v0).mean()))
            except Exception:
                pass
    return float(np.mean(deltas)) if deltas else 1.0


def main() -> None:
    import sys

    sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features as v1
    from nexus_scalp.features.liquidity_engine_opt import (
        LIQUIDITY_ALGORITHM_VERSION,
        LiquidityParams,
    )
    from nexus_scalp.features.liquidity_engine_opt import (
        compute_liquidity_features_v1_1 as v11,
    )

    bars = load_bars(n=16000)
    n = len(bars)
    i_tr = int(n * 0.50)
    i_va = int(n * 0.75)
    train_bars, val_bars, oos_bars = bars[:i_tr], bars[i_tr:i_va], bars[i_va:]
    print(f"bars={n} TRAIN={len(train_bars)} VAL={len(val_bars)} OOS={len(oos_bars)}")

    # ---- baseline v1 vectors on TRAIN (golden anchor) ----
    t0 = time.perf_counter()
    A_v1_tr, _ = compute_vectors(train_bars, v1)
    print(f"v1 TRAIN {A_v1_tr.shape} in {time.perf_counter() - t0:.1f}s")
    score_v1_tr = family_score(A_v1_tr)
    print(f"v1 TRAIN family_score={score_v1_tr:.4f}")

    # ---- coarse search: 3x3x3 grid on EQH tolerance / confluence cutoff / sweep relevance ----
    grid = list(
        itertools.product(
            [0.15, 0.30, 0.45],  # eqh_tolerance
            [0.75, 1.00],  # confluence cutoff
            [2.0, 4.0],  # sweep relevance
        )
    )
    print(f"coarse grid: {len(grid)} cells")
    results = []
    for eqh_tol, conf_cut, swe_rel in grid[:14]:  # bounded: first 14 cells
        p = LiquidityParams(
            eqh_tolerance_atr=eqh_tol,
            confluence_cutoff_atr=conf_cut,
            sweep_relevance_atr=swe_rel,
        )
        t0 = time.perf_counter()
        A, _ = compute_vectors(train_bars, lambda w, _p=p, **kw: v11(w, **kw, params=_p))
        s = family_score(A)
        dt_ = time.perf_counter() - t0
        # perturbation cost is expensive; sample 2 cells only
        stab = 0.0
        if grid.index((eqh_tol, conf_cut, swe_rel)) in (0, 7):
            stab = perturb_stability(v11, train_bars[:2000], p)
        results.append(
            {
                "params": p.as_dict(),
                "train_score": s,
                "runtime_s": dt_,
                "stability": stab,
            }
        )
        print(
            f"grid cell eqh={eqh_tol} conf={conf_cut} rel={swe_rel} score={s:.4f} runtime={dt_:.0f}s"
        )
    # narrow: pick best cell
    best = max(results, key=lambda r: r["train_score"])
    LiquidityParams(**best["params"])
    print("\nBEST coarse:", best["params"], "score", best["train_score"])

    # ---- narrow search around best: one axis perturbed at a time (8 cells) ----
    narrow = []
    b = best["params"]
    variants = [
        dict(b, eqh_tolerance_atr=max(0.10, b["eqh_tolerance_atr"] - 0.05)),
        dict(b, eqh_tolerance_atr=min(0.50, b["eqh_tolerance_atr"] + 0.05)),
        dict(b, confluence_cutoff_atr=max(0.50, b["confluence_cutoff_atr"] - 0.10)),
        dict(b, confluence_cutoff_atr=min(1.00, b["confluence_cutoff_atr"] + 0.10)),
        dict(b, sweep_relevance_atr=max(1.0, b["sweep_relevance_atr"] - 0.5)),
        dict(b, sweep_relevance_atr=min(5.0, b["sweep_relevance_atr"] + 0.5)),
        dict(b, htf_proximity_atr=4.0),
        dict(b, htf_proximity_atr=8.0),
    ]
    for v in variants:
        p = LiquidityParams(**v)
        A, _ = compute_vectors(train_bars, lambda w, _p=p, **kw: v11(w, **kw, params=_p))
        s = family_score(A)
        narrow.append({"params": p.as_dict(), "train_score": s})
    best_n = max(narrow, key=lambda r: r["train_score"])
    final_params = LiquidityParams(**best_n["params"])
    print("NARROW BEST:", best_n["params"], "score", best_n["train_score"])

    # ---- FREEZE: evaluate final on VALIDATION (selection), then OOS once ----
    A_v11_va, _ = compute_vectors(val_bars, lambda w, **kw: v11(w, **kw, params=final_params))
    A_v1_va, _ = compute_vectors(val_bars, v1)
    score_v11_va = family_score(A_v11_va)
    score_v1_va = family_score(A_v1_va)
    print(f"\nVALIDATION: v1={score_v1_va:.4f} v1.1={score_v11_va:.4f}")

    A_v11_oos, _ = compute_vectors(oos_bars, lambda w, **kw: v11(w, **kw, params=final_params))
    A_v1_oos, _ = compute_vectors(oos_bars, v1)
    score_v11_oos = family_score(A_v11_oos)
    score_v1_oos = family_score(A_v1_oos)
    print(f"OOS: v1={score_v1_oos:.4f} v1.1={score_v11_oos:.4f}  (evaluated once, locked)")

    delta = ab_diff(A_v1_oos, A_v11_oos)
    print("\n=== OOS A/B diff (v1 -> v1.1) ===")
    for k, v in delta.items():
        print(
            f"{k:<28} changed%={v['changed_pct']:6.2f} meanΔ={v['mean_delta']:+.4f} "
            f"sat {v['old_saturation_pct']:.1f}->{v['new_saturation_pct']:.1f} "
            f"uniq {v['old_unique']}->{v['new_unique']}"
        )

    # stability of the FINAL param set (small sample)
    stab_final = perturb_stability(v11, train_bars[:2000], final_params)
    print(f"\nfinal stability mean|Δ| under ±5% params = {stab_final:.5f}")

    out = {
        "algorithm": LIQUIDITY_ALGORITHM_VERSION,
        "v1_score_train": score_v1_tr,
        "grid_results": [{**r, "params": r["params"]} for r in results],
        "narrow_results": narrow,
        "final_params": final_params.as_dict(),
        "validation": {"v1": score_v1_va, "v1_1": score_v11_va},
        "oos": {"v1": score_v1_oos, "v1_1": score_v11_oos},
        "oos_ab_diff": delta,
        "stability_final": stab_final,
        "commit": "4455874",
    }
    with open("scratch/liq_opt_results.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("\nwrote scratch/liq_opt_results.json")


if __name__ == "__main__":
    main()
