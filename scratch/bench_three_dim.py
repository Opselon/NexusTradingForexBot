"""Fair 50D vs 60D vs 70D walk-forward benchmark (money-path PHASE 1B).

Same source data (ds_70d_clean_m1_20260904: 99,946 rows, 2026-05-01..2026-08-17,
scalp_v3, TripleBarrier 3-class labels, no_trade_stride=2, labeling friction
friction_usd=0.35), same rows, same purged/embargoed folds, same seed - ONLY
the feature prefix width differs:

    50D = feat_0..49   (scalp_v1 base block)
    60D = feat_0..59   (base + the NEWS block, which is ALL-ZERO in this
                        dataset - verified feat_50..59 == 0.0. This arm is
                        "50D + 10 dead inputs", a DEAD-INPUT ablation, NOT
                        canonical scalp_v2: canonical scalp_v2's feat_50..59
                        are compute_60d_extras, semantically different.
                        A true scalp_v2 arm requires build_60d_dataset from
                        raw bars - separate budgeted run.)
    70D = feat_0..69   (base + news(zeros) + liquidity, canonical scalp_v3)

Metrics-only runs: this driver's trainer subclass (a) skips artifact
publication (the canonical emission gate hard-locks published artifacts to
input_dim=70, which is correct for serving; no artifact from this driver is
written at all, so nothing can ever be served from it) and (b) captures the
trainer's out-of-sample predictions to compute trading metrics in R.

Artifacts: artifacts/model_generation/three_dim_benchmark/<runid>/report.json
"""

from __future__ import annotations

import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

DATASET_ID = "ds_70d_clean_m1_20260904"
DATASET_SHA = "3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe"
VARIANTS: dict[str, tuple[list[str], str]] = {
    "50D": ([f"feat_{i}" for i in range(50)], "scalp_v1"),
    "60D": ([f"feat_{i}" for i in range(60)], "scalp_v2"),
    "70D": ([f"feat_{i}" for i in range(70)], "scalp_v3"),
}
NUM_FOLDS = 10
EPOCHS = 6
SEED = 42
FRICTION_R = 0.35  # friction_usd=0.35 at ~1R risk (labeling convention)
WIN_R = 1.0  # direction-correct trade scores +1R (barrier symmetric approx)
LOSS_R = -1.0


class _BenchTrainer(WalkForwardTrainer):
    """Benchmark-only trainer: no artifact publication + OOS capture.

    Everything else (purged folds, embargo, scaling, early stopping, class
    weights, final training) is the canonical production trainer behavior.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.oos_capture: list[tuple[list[int], list[int]]] = []

    def _publish_candidate_bundle(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    def _evaluate_global_performance(self, preds: list[int], targets: list[int]) -> dict[str, str]:
        self.oos_capture.append((list(preds), list(targets)))
        return super()._evaluate_global_performance(preds, targets)


def r_metrics(preds: list[int], targets: list[int]) -> dict[str, float | int]:
    """Trading metrics in R from 3-class OOS predictions (1=BUY, 2=SELL)."""
    p = np.array(preds, dtype=np.int64)
    t = np.array(targets, dtype=np.int64)
    active = (p == 1) | (p == 2)
    n_trades = int(active.sum())
    if n_trades == 0:
        return {"trades": 0}
    wins_mask = p[active] == t[active]
    gross = np.where(wins_mask, WIN_R, LOSS_R)
    net = gross - FRICTION_R
    expectancy = float(net.mean())
    std = float(net.std()) + 1e-9
    sharpe = expectancy / std * (252**0.5)
    wins_n = int((net > 0).sum())
    losses_n = n_trades - wins_n
    gp = float(net[net > 0].sum())
    gl = float(-net[net < 0].sum())
    pf = gp / gl if gl > 0 else float("inf")
    cum = np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = peak - cum
    # long/short split
    longs = int((p[active] == 1).sum())
    shorts = n_trades - longs
    return {
        "trades": n_trades,
        "long_trades": longs,
        "short_trades": shorts,
        "win_rate": round(wins_n / n_trades, 4),
        "expectancy_r": round(expectancy, 4),
        "sharpe_proxy": round(float(sharpe), 3),
        "profit_factor": round(pf, 3) if gl > 0 else None,
        "max_dd_r": round(float(dd.max()), 3),
        "sum_r": round(float(net.sum()), 2),
        "wins": wins_n,
        "losses": losses_n,
    }


def main() -> int:
    df = pl.read_parquet(
        REPO / "artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet"
    )
    # trainable rows exactly as the production filter: label_evaluated & not purged
    if "label_evaluated" in df.columns:
        df = df.filter(pl.col("label_evaluated"))
    if "is_purged" in df.columns:
        df = df.filter(~pl.col("is_purged"))
    print(
        f"trainable rows: {df.height} span {df['timestamp'].min()} .. {df['timestamp'].max()}",
        flush=True,
    )

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = REPO / "artifacts/model_generation/three_dim_benchmark" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "run_id": run_id,
        "dataset_id": DATASET_ID,
        "dataset_sha256": DATASET_SHA,
        "trainable_rows": df.height,
        "num_folds": NUM_FOLDS,
        "epochs_per_fold": EPOCHS,
        "seed": SEED,
        "host": platform.node(),
        "python": platform.python_version(),
        "created_utc": datetime.now(UTC).isoformat(),
        "cost_model": {
            "labeling_friction_usd": 0.35,
            "friction_r_per_trade": FRICTION_R,
            "win_r": WIN_R,
            "loss_r": LOSS_R,
            "note": "labels built by TripleBarrierLabeler(friction_usd=0.35); scored +1R/-1R direction-correct net of friction",
        },
        "note": "metrics-only (no artifact published); identical rows/folds/seed across variants",
        "variants": {},
    }

    for name, (cols, schema_id) in VARIANTS.items():
        t0 = time.perf_counter()
        trainer = _BenchTrainer(
            num_folds=NUM_FOLDS,
            batch_size=256,
            learning_rate=5e-4,
            epochs_per_fold=EPOCHS,
            early_stopping_patience=3,
            purge_gap_bars=15,
            random_seed=SEED,
            artifact_save_path=out_dir / name / "model.pt",
            feature_schema_id=schema_id,
            label_origin="CLEAN_HISTORICAL",
        )
        status = "OK"
        try:
            trainer.train_and_validate(df, cols)
        except Exception as exc:  # noqa: BLE001 - record and continue
            status = f"FAILED: {exc}"
            report["variants"][name] = {"error": str(exc)[:500]}
            print(f"[{name}] {status}", flush=True)
            (out_dir / "report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            continue
        elapsed = time.perf_counter() - t0
        all_preds: list[int] = []
        all_targets: list[int] = []
        for preds, targets in trainer.oos_capture:
            all_preds.extend(preds)
            all_targets.extend(targets)
        metrics = r_metrics(all_preds, all_targets)
        metrics["oos_samples"] = len(all_preds)
        metrics["elapsed_sec"] = round(elapsed, 1)
        report["variants"][name] = {"status": status, "oos": metrics}
        print(f"[{name}] {status} OOS: {metrics}", flush=True)
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

    print("report:", out_dir / "report.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
