"""TASK-07-70D-LIQUIDITY-RESEARCH — small-sample A/B/C/D ablation (step 10).

Why not the TASK-4 driver as-is: compute_70d_frame is O(n^2) per row over the
full history (liquidity engine swing detection over the whole window), so even
6000 rows takes hours (observed: driver stalled at cell C frame build, killed
after 17 min). For the ablation we need IDENTICAL timestamps across cells —
achieved here with the same LOOKBACK-bounded causal window used everywhere in
this task (decision_at gating preserved; only the last LOOKBACK bars feed the
engine, so weekly/daily pools stay intact but cost is O(LOOKBACK^2)).

Cells (all on the SAME decision timestamps, same labels, same split):
  A = 50D base features (feat_0..49 from the canonical 50D engine)
  B = 60D base+news (feat_0..59, news 10D from news_context_v1 or neutral)
  C = 60D base+liquidity (feat_0..49 + liquidity 10D at 50..59)
  D = 70D base+news+liquidity (feat_0..69)
Labels: triple-barrier style 3-class on forward 5-bar return vs 0.5*ATR
(identical across cells — the TASK-4 driver's own minimal label contract).
Evaluation: same tail-20% split; accuracy + macro-F1 + ECE + Brier + n.
Small-sample: n_rows=600 -> ~110 val rows -> LOW_EVIDENCE unless n>=100;
verdict INCONCLUSIVE if any cell n<100 (mission 34/35).

This is a research probe, NOT production training. Model architecture: the
project's LEGACY_SCALPNET_V1 through CandidateTrainer when cheap; otherwise a
plain logistic regression on the frozen features (documented) so the ablation
measures FEATURE information, not architecture tuning.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import UTC
from pathlib import Path

REPO = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from nexus_scalp.features.liquidity_engine import compute_liquidity_features, liquidity_atr  # noqa: E402
from nexus_scalp.features.scalp_features import ScalpFeatureEngine  # noqa: E402
from nexus_scalp.market_data.bar_aggregator import BarData  # noqa: E402

OUT = REPO / "scratch" / "task07_research"
OUT.mkdir(parents=True, exist_ok=True)
RESEARCH_RUN_ID = "task07_ablation_small_01"
BASELINE_ID = "e85de540e09d3339"
LOOKBACK = 2000
N_ROWS = 900          # decision points (bounded for runtime)
LABEL_HORIZON = 5     # bars
LABEL_THRESH_MULT = 0.5  # x ATR

SESSIONS = {"ASIAN_TOKYO": (0, 8), "LONDON": (8, 13), "LONDON_NY_OVERLAP": (13, 16), "NEW_YORK": (16, 21)}


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
        bars.append(BarData(symbol="XAUUSD", timeframe="M5", timestamp=ts,
                            open=float(row["open"]), high=float(row["high"]),
                            low=float(row["low"]), close=float(row["close"]),
                            tick_volume=int(row["tick_volume"] or 0), is_complete=True))
    return bars


def compute_row(bars: list[BarData], i: int) -> dict:
    """Causal features at decision bar i: 50D base + liquidity 10D + labels."""
    lo = max(0, i + 1 - LOOKBACK)
    window = bars[lo : i + 1]
    ts = window[-1].timestamp
    atr = float(liquidity_atr([b.high for b in window], [b.low for b in window], [b.close for b in window]))
    # canonical 50D engine (synthetic tick at decision close — the dataset
    # convention used by compute_60d_frame: decision tick at bar close)
    from nexus_scalp.domain.models import TickData  # noqa: PLC0415

    eng = ScalpFeatureEngine(symbol="XAUUSD")
    tick = TickData(
        symbol="XAUUSD",
        bid=float(window[-1].close) - 0.05,
        ask=float(window[-1].close) + 0.05,
        timestamp=ts,
        volume=0,
    )
    vec50 = eng.compute_from_bars(window, tick)
    feats50 = vec50.to_tensor_input()
    # liquidity 10D (v1.1 semantics via the frozen v1 engine + opt params? use
    # the committed v1 engine as_vector for contract parity with the 60D/70D
    # datasets; version recorded below)
    lf = compute_liquidity_features(window, decision_at=ts, atr=atr)
    liq10 = lf.as_vector()
    # label: forward 5-bar return vs 0.5*ATR (identical for all cells)
    n = len(bars)
    label = 0
    if i + LABEL_HORIZON < n:
        fut = bars[i + LABEL_HORIZON].close - window[-1].close
        thr = LABEL_THRESH_MULT * atr
        if fut > thr:
            label = 1
        elif fut < -thr:
            label = 2
    return {
        "timestamp": ts.isoformat(),
        "session": session_of(ts),
        "feat_0_49": [float(x) for x in feats50],
        "liq_50_59": [float(x) for x in liq10],
        "atr": atr,
        "label": label,
    }


def main() -> int:
    bars = load_bars()
    print(f"bars={len(bars)}", flush=True)
    # stride to spread N_ROWS across the history
    stride = max(1, (len(bars) - LOOKBACK) // N_ROWS)
    idxs = list(range(LOOKBACK, len(bars) - LABEL_HORIZON, stride))[:N_ROWS]
    print(f"decision points={len(idxs)} stride={stride}", flush=True)

    rows = []
    for k, i in enumerate(idxs):
        rows.append(compute_row(bars, i))
        if k % 200 == 0:
            print(f"  ..{k}/{len(idxs)}", flush=True)

    n = len(rows)
    X50 = np.array([r["feat_0_49"] for r in rows], dtype=np.float64)
    Xliq = np.array([r["liq_50_59"] for r in rows], dtype=np.float64)
    Xnews = np.zeros((n, 10), dtype=np.float64)  # neutral news block (no aligned news)
    y = np.array([r["label"] for r in rows], dtype=np.int64)

    # sanitize 50D the way the engine does (clip + nan->0)
    X50 = np.nan_to_num(X50, nan=0.0, posinf=3.0, neginf=-3.0)
    X50 = np.clip(X50, -3.0, 3.0)
    Xliq = np.clip(np.nan_to_num(Xliq, nan=0.0), -3.0, 3.0)

    cells = {
        "A": np.concatenate([X50], axis=1),
        "B": np.concatenate([X50, Xnews], axis=1),
        "C": np.concatenate([X50, Xliq], axis=1),
        "D": np.concatenate([X50, Xnews, Xliq], axis=1),
    }
    dims = {"A": 50, "B": 60, "C": 60, "D": 70}

    # train/val split: tail 20% (identical across cells)
    cut = int(n * 0.8)
    y_train, y_val = y[:cut], y[cut:]

    # simple, robust classifier for feature-information measurement:
    # multinomial logistic regression with L2 (sklearn) — architecture-agnostic
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.metrics import accuracy_score, f1_score  # noqa: PLC0415

    results = {}
    for cid in ["A", "B", "C", "D"]:
        X = cells[cid]
        Xtr, Xva = X[:cut], X[cut:]
        t0 = time.time()
        clf = LogisticRegression(max_iter=500, C=1.0, random_state=42)
        clf.fit(Xtr, y_train)
        preds = clf.predict(Xva)
        proba = clf.predict_proba(Xva)
        fit_s = round(time.time() - t0, 2)
        acc = accuracy_score(y_val, preds)
        mf1 = f1_score(y_val, preds, average="macro", zero_division=0)
        # ECE (10 bins)
        n_val = len(y_val)
        if proba.shape[1] == 3:
            conf = proba.max(axis=1)
            correct = (preds == y_val).astype(float)
            ece = 0.0
            for b in range(10):
                lo_, hi_ = b / 10, (b + 1) / 10
                m = (conf >= lo_) & (conf < hi_)
                if m.sum() > 0:
                    ece += abs(conf[m].mean() - correct[m].mean()) * m.sum() / n_val
        else:
            ece = float("nan")
        # Brier (one-hot)
        y_onehot = np.zeros((n_val, 3))
        y_onehot[np.arange(n_val), y_val] = 1
        brier = float(np.mean((proba - y_onehot) ** 2)) if proba.shape[1] == 3 else float("nan")
        results[cid] = {
            "dim": dims[cid],
            "train_n": int(len(y_train)),
            "val_n": int(n_val),
            "accuracy": round(float(acc), 4),
            "macro_f1": round(float(mf1), 4),
            "ece": round(float(ece), 4),
            "brier": round(float(brier), 4),
            "fit_seconds": fit_s,
        }
        print(f"  cell {cid}: acc={results[cid]['accuracy']} f1={results[cid]['macro_f1']} ece={results[cid]['ece']} brier={results[cid]['brier']} ({fit_s}s)", flush=True)

    # verdict logic (mission 34/35: n>=100 per cell else INCONCLUSIVE/LOW_EVIDENCE)
    ns = {c: results[c]["val_n"] for c in results}
    if any(v < 100 for v in ns.values()):
        verdict = {"outcome": "INCONCLUSIVE", "reason": "val_n < 100 (small-sample probe)", "n": ns}
    else:
        db = results["D"]["macro_f1"] - results["B"]["macro_f1"]
        da = results["C"]["macro_f1"] - results["A"]["macro_f1"]
        verdict = {
            "outcome": "POSITIVE" if db >= 0.02 and da >= 0.02 else "NEUTRAL" if abs(db) < 0.02 and abs(da) < 0.02 else "NEGATIVE" if db <= -0.02 or da <= -0.02 else "INCONCLUSIVE",
            "delta_D_minus_B_f1": round(db, 4),
            "delta_C_minus_A_f1": round(da, 4),
        }

    report = {
        "research_run_id": RESEARCH_RUN_ID,
        "research_baseline_id": BASELINE_ID,
        "algorithm_version": "liquidity-v1.0 (committed engine) — v1.1 version isolation recorded separately",
        "method": "small-sample exploratory ablation; identical timestamps/labels/split across cells; logistic regression (feature-information probe, architecture-agnostic); tail-20% val",
        "samples": n,
        "time_range": [rows[0]["timestamp"], rows[-1]["timestamp"]],
        "label_contract": f"forward {LABEL_HORIZON}-bar return vs {LABEL_THRESH_MULT}*ATR (identical across cells)",
        "cells": results,
        "verdict": verdict,
        "limitations": ["small n (LOW_EVIDENCE unless n>=100)", "news block NEUTRAL (no aligned news)", "linear model — measures feature information, not the production architecture"],
    }
    (OUT / "ablation_abcd_small.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"cells": results, "verdict": verdict}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())