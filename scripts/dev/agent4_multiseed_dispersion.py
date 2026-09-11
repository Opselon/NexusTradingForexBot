#!/usr/bin/env python3
"""Agent-4 multi-seed dispersion driver — canonical 70D producer, N seeds, no artifact.

NEXT ACTION from TASK-AGENT4-ML-REPRO: promotion lane needs multi-seed
mean/CI evidence before any 70D learning claim. This driver produces that
evidence on the AUTHORITATIVE dataset using the canonical WalkForwardTrainer,
without publishing any artifact and without touching the champion:

  * seeds: base 42 + offsets (default 7/42/2026 = the audited seed set),
  * blocked purged+embargoed walk-forward, metrics-only (publish suppressed),
  * per seed: OOS accuracy / balanced accuracy / BUY+SELL precision /
    prediction distribution / trade count / net expectancy in R,
  * aggregate: mean / std / min / max across seeds + the evidence verdict
    (n >= 30 OOS trades required for ANY expectancy figure to be quotable),
  * majority-baseline comparison computed on the EXACT pooled OOS windows,
  * report JSON to artifacts/model_generation/pilots/agent4_multiseed_<ts>.json.

Read-only wrt production artifacts. The champion is never contacted; no
registry, gate, or serving path is touched. CPU-only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer  # noqa: E402

DATASET_ID = "ds_70d_clean_m1_20260904"
DATASET_SHA = "3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe"
SCHEMA_HASH = "235b8fccc96b7e0e"
FEATURE_DIM = 70

#: Evidence floor for ANY expectancy figure (paired moving-block bootstrap
#: statistical promotion gate requires >= 30 paired trades; shadow policy
#: requires 100 for statistical sufficiency).
EVIDENCE_FLOOR_TRADES = 30


class _MetricsOnlyTrainer(WalkForwardTrainer):
    """Canonical trainer with artifact publication suppressed + OOS capture."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.oos_capture: list[tuple[list[int], list[int]]] = []

    def _publish_candidate_bundle(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        return None

    def _evaluate_global_performance(self, preds: list[int], targets: list[int]) -> dict[str, str]:
        self.oos_capture.append((list(preds), list(targets)))
        return super()._evaluate_global_performance(preds, targets)


def _seed_metrics(trainer: _MetricsOnlyTrainer) -> dict[str, object]:
    preds_l: list[int] = []
    targets_l: list[int] = []
    for p, t in trainer.oos_capture:
        preds_l.extend(p)
        targets_l.extend(t)
    preds = np.asarray(preds_l, dtype=np.int64)
    targets = np.asarray(targets_l, dtype=np.int64)

    def _precision(c: int) -> float | None:
        mask = preds == c
        return float((targets[mask] == c).mean()) if mask.sum() else None

    conv = trainer.last_convergence_metadata
    folds = conv.get("fold_economics") or []
    trades = int(((preds == 1) | (preds == 2)).sum())
    active = preds != 0
    wins = int((preds[active] == targets[active]).sum())
    return {
        "oos_samples": len(preds),
        "accuracy": round(float((preds == targets).mean()), 4),
        "balanced_accuracy": round(
            float(
                np.mean(
                    [
                        (preds[targets == c] == c).mean() if (targets == c).sum() else 0.0
                        for c in (0, 1, 2)
                    ]
                )
            ),
            4,
        ),
        "precision_buy": _precision(1),
        "precision_sell": _precision(2),
        "prediction_distribution": {str(c): int((preds == c).sum()) for c in (0, 1, 2)},
        "oos_trades": trades,
        "oos_win_rate": round(wins / trades, 4) if trades else None,
        "net_expectancy_r": conv.get("net_expectancy_r"),
        "fold_net_r": [f.get("net_expectancy_r") for f in folds],
        "fold_trades": [f.get("trades") for f in folds],
        "evidence_sufficient": trades >= EVIDENCE_FLOOR_TRADES,
    }


def _pooled_baselines(
    df: pl.DataFrame, rows: int, folds: int, train_ratio: float, embargo: int
) -> dict[str, float]:
    """Majority/random baselines on the EXACT pooled OOS windows."""
    n = df.height
    fold_size = n // folds
    targets: list[int] = []
    for f in range(folds):
        s = f * fold_size
        e = n if f == folds - 1 else (f + 1) * fold_size
        val_start = s + int((e - s) * train_ratio)
        val_end = e - embargo
        targets.extend(df["label"][val_start:val_end].to_list())
    t = np.asarray(targets)
    p0 = float((t == 0).mean())
    p1 = float((t == 1).mean())
    p2 = float((t == 2).mean())
    return {
        "oos_window_rows": len(t),
        "majority_accuracy": round(p0, 4),
        "majority_balanced_accuracy": round((1.0 + p0 + p0) / 3.0, 4),
        "random_guess_accuracy": round(p0 * p0 + p1 * p1 + p2 * p2, 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=24_000, help="contiguous temporal tail")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 2026])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    df = pl.read_parquet(REPO / f"artifacts/model_generation/datasets/{DATASET_ID}/dataset.parquet")
    if "label_evaluated" in df.columns:
        df = df.filter(pl.col("label_evaluated"))
    if "is_purged" in df.columns:
        df = df.filter(~pl.col("is_purged"))
    df = df.sort("timestamp").tail(args.rows)
    feat_cols = [f"feat_{i}" for i in range(FEATURE_DIM)]
    print(
        f"rows={df.height} seeds={args.seeds} folds={args.folds} epochs={args.epochs}", flush=True
    )

    runs: list[dict[str, object]] = []
    for seed in args.seeds:
        t0 = time.perf_counter()
        trainer = _MetricsOnlyTrainer(
            num_folds=args.folds,
            batch_size=args.batch,
            learning_rate=5e-4,
            epochs_per_fold=args.epochs,
            early_stopping_patience=3,
            purge_gap_bars=15,
            embargo_bars=15,
            random_seed=seed,
            artifact_save_path=REPO
            / "artifacts/model_generation/models/agent4_multiseed_tmp/model.pt",
            feature_schema_id="scalp_v3",
            smoke=True,
            label_origin="CLEAN_HISTORICAL",
        )
        trainer.declare_dataset_provenance(
            DATASET_ID,
            DATASET_SHA,
            feature_schema_hash=SCHEMA_HASH,
            label_schema_id="triple_barrier_3class_v1",
            pilot_subset_definition=f"contiguous temporal tail({args.rows})",
        )
        trainer.train_and_validate(df, feat_cols)
        m = _seed_metrics(trainer)
        m["seed"] = seed
        m["seconds"] = round(time.perf_counter() - t0, 1)
        runs.append(m)
        print(
            f"[seed {seed}] acc={m['accuracy']} bal={m['balanced_accuracy']} "
            f"trades={m['oos_trades']} netR={m['net_expectancy_r']} "
            f"dist={m['prediction_distribution']}",
            flush=True,
        )

    nets = [float(r["net_expectancy_r"]) for r in runs if r["net_expectancy_r"] is not None]  # type: ignore[arg-type,union-attr]
    accs = [float(r["accuracy"]) for r in runs]  # type: ignore[arg-type]
    bals = [float(r["balanced_accuracy"]) for r in runs]  # type: ignore[arg-type]
    trade_counts = [int(r["oos_trades"]) for r in runs]  # type: ignore[call-overload]
    baselines = _pooled_baselines(df, args.rows, args.folds, 0.70, 15)

    def _agg(vals: list[float]) -> dict[str, float | None]:
        return {
            "mean": round(float(np.mean(vals)), 4) if vals else None,
            "std": round(float(np.std(vals, ddof=1)), 4) if len(vals) >= 2 else None,
            "min": round(min(vals), 4) if vals else None,
            "max": round(max(vals), 4) if vals else None,
        }

    verdict = {
        "evidence_floor_trades": EVIDENCE_FLOOR_TRADES,
        "seeds_with_sufficient_trades": sum(1 for n in trade_counts if n >= EVIDENCE_FLOOR_TRADES),
        "max_seed_trades": max(trade_counts) if trade_counts else 0,
        "accuracy_vs_majority": (round(float(np.mean(accs)) - baselines["majority_accuracy"], 4)),
        "economics_quotable": max(trade_counts, default=0) >= EVIDENCE_FLOOR_TRADES,
    }
    report = {
        "run_id": datetime.now(UTC).strftime("%Y%m%d_%H%M%S"),
        "dataset_id": DATASET_ID,
        "dataset_sha256": DATASET_SHA,
        "config": {
            "rows": args.rows,
            "folds": args.folds,
            "epochs": args.epochs,
            "batch": args.batch,
            "seeds": args.seeds,
            "walk_forward_mode": "blocked",
        },
        "baselines": baselines,
        "runs": runs,
        "aggregate": {
            "accuracy": _agg(accs),
            "balanced_accuracy": _agg(bals),
            "net_expectancy_r": _agg(nets),
            "oos_trades": _agg([float(n) for n in trade_counts]),
        },
        "verdict": verdict,
        "note": "metrics-only multi-seed dispersion evidence; no artifact published; champion untouched",
    }
    out = args.out or (
        REPO / "artifacts/model_generation/pilots" / f"agent4_multiseed_{report['run_id']}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {out}", flush=True)
    print(f"verdict: {json.dumps(verdict)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
