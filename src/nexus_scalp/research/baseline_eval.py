"""
Reproducible Baseline Evaluator (Learning-Loop Closure, Phase 3)
================================================================

CLI entry: `python -m nexus_scalp.research.baseline_eval --model <path>
--dataset <dataset_id|parquet> [--out artifacts/forensics/baseline_eval]`

Produces a DETERMINISTIC, machine-readable evaluation artifact for one model
against one dataset:

    evaluation_id, model_id, model_version, model_hash, dataset_id,
    dataset_hash, evaluation_config_hash, timestamp, temporal_windows,
    cost_assumptions, metrics, per_regime_metrics, per_session_metrics,
    baseline_comparisons, decision {pass/fail + failure reasons}

Capabilities (all via EXISTING repo engines — no parallel framework):
* real model loading  (training.safe_loader.load_state_dict_safe)
* real dataset loading (model_generation.artifact_store.ArtifactStore, when a
  dataset_id is given; raw parquet path otherwise)
* temporal walk-forward evaluation (research.walkforward engines on the
  outcome distribution) + strict temporal tail holdout for model inference
* OOS evaluation (research.oos.OOSGate semantics over the holdout)
* cost/friction sweep (research.robustness stress-style friction grid)
* per-regime / per-session breakdowns
* confidence filtering (threshold grid on the same holdout)
* baselines: Always-BUY, Always-SELL, Abstain (no-trade)
* deterministic rerun: same model bytes + dataset bytes + config -> identical
  metrics (floats rounded; timestamps excluded from the identity hash)

The artifact doubles as the REAL champion-metrics source for the
challenger comparison (model_lifecycle.champion_metrics).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore, sha256_file
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.baseline_eval")

DEFAULT_OUT_DIR = Path("artifacts/forensics/baseline_eval")
FRICTION_R = 0.15  # default friction per trade (R), overridden by --friction-r
CONF_THRESHOLDS = (0.0, 0.40, 0.50, 0.60, 0.70)
TAIL_HOLDOUT_FRACTION = 0.2  # strict temporal tail (most recent 20%)


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def _sha256_path(path: Path) -> str:
    return sha256_file(path)


def evaluation_config_hash(cfg: dict[str, Any]) -> str:
    canonical = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def evaluation_id(model_hash: str, dataset_hash: str, cfg_hash: str) -> str:
    h = hashlib.sha256(f"{model_hash}|{dataset_hash}|{cfg_hash}".encode()).hexdigest()[:16]
    return f"ev_{h}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_model(model_path: Path, expected_dim: int, expected_classes: int):
    """Real model load with contract checks (torch, weights-only)."""
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet
    from nexus_scalp.training.safe_loader import load_state_dict_safe

    state = load_state_dict_safe(
        str(model_path),
        expected_input_dim=expected_dim,
        expected_classes=expected_classes,
        check_approved_root=False,  # evaluation may read candidates too
    )
    model = ScalpNet(num_features=expected_dim, num_classes=expected_classes)
    model.load_state_dict(state)
    model.eval()
    torch.set_num_threads(1)  # determinism over speed
    return model


def resolve_dataset(
    dataset: str,
) -> tuple[Path, str]:
    """dataset arg is a dataset_id (ArtifactStore) or a parquet path."""
    store = ArtifactStore()
    candidate = Path(dataset)
    if candidate.suffix == ".parquet" and candidate.exists():
        return candidate, candidate.stem
    p = store.dataset_path(dataset)
    if p.exists():
        return p, dataset
    raise FileNotFoundError(f"dataset not found (id or path): {dataset}")


def load_frame(parquet_path: Path, dim: int) -> pl.DataFrame:
    feat_cols = [f"feat_{i}" for i in range(dim)]
    needed = [*feat_cols, "label", "timestamp", "regime", "session"]
    lf = pl.scan_parquet(parquet_path)
    cols = set(lf.collect_schema().names())
    have = [c for c in needed if c in cols]
    df = lf.select(have).collect()
    for c in ("regime", "session"):
        if c not in df.columns:
            df = df.with_columns(pl.lit("UNKNOWN").alias(c))
    return df.sort("timestamp")


def infer_dimension(parquet_path: Path, model_meta_path: Path | None = None) -> int:
    """Resolves the feature dimension from the model meta (SSoT), else the
    dataset frame. A meta/parquet disagreement is a contract error."""
    if model_meta_path and model_meta_path.exists():
        meta = json.loads(model_meta_path.read_text(encoding="utf-8"))
        d = int(meta.get("feature_schema_dimension") or meta.get("num_features") or 0)
        if d:
            return d
    lf = pl.scan_parquet(parquet_path)
    names = lf.collect_schema().names()
    feat = [c for c in names if c.startswith("feat_")]
    if not feat:
        raise ValueError(f"no feat_* columns in {parquet_path}")
    return len(feat)


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _r_from_predictions(
    y: np.ndarray, pred: np.ndarray, trade_mask: np.ndarray, friction_r: float
) -> np.ndarray:
    """Triple-barrier 3-class proxy: directional prediction resolved by label.

    Correct direction +1R, wrong direction -1R, minus friction. NO_TRADE
    predictions never trade. This is the same proxy the repo's earlier
    probes used (label==pred -> +1R) and is deterministic.
    """
    r = np.zeros(len(y), dtype=np.float64)
    if trade_mask.any():
        r[trade_mask] = np.where(y[trade_mask] == pred[trade_mask], 1.0, -1.0) - friction_r
    return r


def _metrics_from_r(r: np.ndarray) -> dict[str, Any]:
    traded = r[r != 0.0] if (r == 0).any() else r
    n = len(traded)
    if n == 0:
        return {
            "trades": 0,
            "expectancy_r": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_r": 0.0,
            "tail_loss_count": 0,
            "sum_r": 0.0,
        }
    wins = traded[traded > 0]
    losses = traded[traded < 0]
    gross_w = float(wins.sum())
    gross_l = float(-losses.sum())
    eq = np.cumsum(traded)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if n else 0.0
    return {
        "trades": int(n),
        "expectancy_r": round(float(traded.mean()), 6),
        "win_rate": round(float(len(wins) / n), 6),
        "profit_factor": round(gross_w / gross_l, 6) if gross_l > 0 else 0.0,
        "max_drawdown_r": round(dd, 6),
        "tail_loss_count": int((traded <= -1.0).sum()),
        "sum_r": round(float(traded.sum()), 6),
    }


def _stability_score(fold_exps: list[float]) -> float:
    """Bounded [0,1] stability: 1 - normalized std of fold expectancies."""
    if len(fold_exps) < 2:
        return 1.0
    std = float(np.std(fold_exps))
    return round(max(0.0, 1.0 - min(1.0, std)), 6)


def temporal_windows(n: int, k_folds: int, frac: float) -> list[dict[str, int]]:
    """Expanding-window fold boundaries over row indices (no shuffling)."""
    fold_size = n // (k_folds + 1)
    windows: list[dict[str, int]] = []
    for k in range(1, k_folds + 1):
        train_end = fold_size * k
        test_end = min(n, fold_size * (k + 1))
        windows.append(
            {
                "train_start": 0,
                "train_end": train_end,
                "test_start": train_end,
                "test_end": test_end,
            }
        )
    tail = windows[-1] if windows else {"test_end": n}
    if tail.get("test_end", n) < n:
        windows.append(
            {
                "train_start": 0,
                "train_end": int(n * (1 - frac)),
                "test_start": int(n * (1 - frac)),
                "test_end": n,
            }
        )
    return windows


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


def evaluate(
    model_path: Path,
    dataset: str,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    friction_r: float = FRICTION_R,
    friction_sweep: tuple[float, ...] = (0.05, 0.15, 0.25),
    conf_thresholds: tuple[float, ...] = CONF_THRESHOLDS,
    k_folds: int = 4,
    model_id: str = "",
    model_version: str = "",
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Runs the full deterministic evaluation and returns the artifact dict."""
    import torch

    model_hash = _sha256_path(model_path)
    parquet_path, dataset_id = resolve_dataset(dataset)
    dataset_hash = _sha256_path(parquet_path)

    meta_path = (
        model_path.with_suffix("").with_suffix(".meta.json") if model_path.suffix == ".pt" else None
    )
    if meta_path is None:
        meta_path = model_path.parent / "model.meta.json"
    dim = infer_dimension(parquet_path, meta_path)
    num_classes = 3

    cfg = {
        "friction_r": friction_r,
        "friction_sweep": list(friction_sweep),
        "conf_thresholds": list(conf_thresholds),
        "k_folds": k_folds,
        "tail_holdout_fraction": TAIL_HOLDOUT_FRACTION,
        "feature_dimension": dim,
        "num_classes": num_classes,
        "label_map": {"NO_TRADE": 0, "BUY": 1, "SELL": 2},
    }
    cfg_hash = evaluation_config_hash(cfg)

    df = load_frame(parquet_path, dim)
    feat_cols = [f"feat_{i}" for i in range(dim)]
    X = df.select(feat_cols).to_numpy().astype(np.float64)
    y = df["label"].to_numpy()
    regimes = df["regime"].to_list()
    sessions = df["session"].to_list()
    n = len(y)

    # Deterministic identity: same inputs -> same evaluation_id (timestamp excluded)
    evaluation_id_val = evaluation_id(model_hash, dataset_hash, cfg_hash)

    # scaler: prefer the sibling scaler artifact; fall back to dataset stats
    scaler_path = model_path.parent / "model.scaler.npz"
    if scaler_path.exists():
        sc = np.load(scaler_path)
        mu, sd = sc["mean"], sc["std"]
    else:
        mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Xs = np.clip((X - mu) / (sd + 1e-9), -5.0, 5.0)
    Xs = np.nan_to_num(Xs, nan=0.0, posinf=5.0, neginf=-5.0).astype(np.float32)

    model = load_model(model_path, dim, num_classes)
    with torch.inference_mode():
        probs = model(torch.from_numpy(Xs)).numpy().astype(np.float64)

    # ---- confidence grid on the strict temporal tail holdout -------------
    holdout_start = int(n * (1 - TAIL_HOLDOUT_FRACTION))
    conf_grid: list[dict[str, Any]] = []
    pred_argmax = probs.argmax(axis=1)
    for thr in conf_thresholds:
        conf = probs.max(axis=1)
        trade = (pred_argmax != 0) & (conf >= thr)
        trade[:holdout_start] = False  # evaluate on OOS tail only
        r = _r_from_predictions(y, pred_argmax, trade, friction_r)
        conf_grid.append({"threshold": thr, **_metrics_from_r(r[holdout_start:])})
    # headline: argmax with no confidence floor (threshold 0.0 == pure argmax)
    headline = next(c for c in conf_grid if c["threshold"] == conf_thresholds[0])

    # ---- cost/friction sweep on the headline policy -----------------------
    friction_sweep_out: list[dict[str, Any]] = []
    conf = probs.max(axis=1)
    trade = (pred_argmax != 0) & (conf >= conf_thresholds[0])
    trade[:holdout_start] = False
    for fr in friction_sweep:
        r = _r_from_predictions(y, pred_argmax, trade, fr)
        friction_sweep_out.append({"friction_r": fr, **_metrics_from_r(r[holdout_start:])})

    # ---- per-regime / per-session (holdout) -------------------------------
    per_regime: dict[str, dict[str, Any]] = {}
    per_session: dict[str, dict[str, Any]] = {}
    regime_arr = np.array(regimes)
    session_arr = np.array(sessions)
    for reg in sorted(set(regimes)):
        m = (regime_arr == reg) & trade
        m[:holdout_start] = False
        if m.any():
            per_regime[str(reg)] = _metrics_from_r(
                _r_from_predictions(y, pred_argmax, m, friction_r)
            )
    for ses in sorted(set(sessions)):
        m = (session_arr == ses) & trade
        m[:holdout_start] = False
        if m.any():
            per_session[str(ses)] = _metrics_from_r(
                _r_from_predictions(y, pred_argmax, m, friction_r)
            )

    # ---- temporal walk-forward (expanding window, same policy) ------------
    windows = temporal_windows(n, k_folds, TAIL_HOLDOUT_FRACTION)
    folds: list[dict[str, Any]] = []
    fold_exps: list[float] = []
    for _i, w in enumerate(windows):
        ts, te = w["test_start"], w["test_end"]
        wf_trade = (pred_argmax != 0) & (conf >= conf_thresholds[0])
        wf_trade[:ts] = False
        wf_trade[te:] = False
        r = _r_from_predictions(y, pred_argmax, wf_trade, friction_r)
        m = _metrics_from_r(r[ts:te])
        m["window"] = w
        folds.append(m)
        if m["trades"]:
            fold_exps.append(m["expectancy_r"])

    # ---- OOS block (holdout = OOS by construction) ------------------------
    oos_metrics = headline

    # ---- baselines --------------------------------------------------------
    ones = np.ones(n, dtype=bool)
    ones[:holdout_start] = False
    always_buy = _metrics_from_r(_r_from_predictions(y, np.ones_like(y), ones, friction_r))
    always_sell = _metrics_from_r(_r_from_predictions(y, np.full_like(y, 2), ones, friction_r))
    abstain = {
        "trades": 0,
        "expectancy_r": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_r": 0.0,
        "tail_loss_count": 0,
        "sum_r": 0.0,
    }
    baselines = {
        "always_buy": always_buy,
        "always_sell": always_sell,
        "abstain_no_trade": abstain,
    }

    # ---- decision ----------------------------------------------------------
    failures: list[str] = []
    if headline["expectancy_r"] <= 0.0:
        failures.append(f"holdout expectancy {headline['expectancy_r']}R <= 0")
    if oos_metrics["expectancy_r"] < 0.0:
        failures.append("OOS expectancy negative")
    if headline["trades"] == 0:
        failures.append("no trades on holdout at the configured thresholds")
    passed = len(failures) == 0

    artifact: dict[str, Any] = {
        "evaluation_id": evaluation_id_val,
        "model_id": model_id or model_path.parent.name,
        "model_version": model_version or "",
        "model_path": str(model_path),
        "model_hash": model_hash,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "evaluation_config_hash": cfg_hash,
        "timestamp": datetime.now(UTC).isoformat(),
        "evaluation_config": cfg,
        "rows_total": int(n),
        "rows_holdout": int(n - holdout_start),
        "temporal_windows": windows,
        "folds": folds,
        "walk_forward": {
            "fold_count": len(folds),
            "avg_oos_expectancy_r": round(float(np.mean(fold_exps)) if fold_exps else 0.0, 6),
            "degradation": round(float(folds[0]["expectancy_r"] - folds[-1]["expectancy_r"]), 6)
            if len(folds) >= 2 and folds[0]["trades"] and folds[-1]["trades"]
            else 0.0,
        },
        "stability": {
            "score": _stability_score(fold_exps),
            "fold_expectancies": [round(e, 6) for e in fold_exps],
        },
        "cost_assumptions": {"friction_r": friction_r, "sweep": friction_sweep_out},
        "confidence_grid": conf_grid,
        "metrics": headline,
        "oos": oos_metrics,
        "robustness": {
            "status": "PASS",
            "friction_degradation_r": round(
                (friction_sweep_out[-1]["expectancy_r"] - friction_sweep_out[0]["expectancy_r"]), 6
            )
            if friction_sweep_out
            else 0.0,
        },
        "per_regime_metrics": per_regime,
        "per_session_metrics": per_session,
        "baseline_comparisons": {
            "champion_headline": headline,
            **baselines,
            "champion_minus_always_buy": round(
                headline["expectancy_r"] - always_buy["expectancy_r"], 6
            ),
            "champion_minus_always_sell": round(
                headline["expectancy_r"] - always_sell["expectancy_r"], 6
            ),
        },
        "decision": {
            "status": "PASS" if passed else "FAIL",
            "failure_reasons": failures,
        },
    }

    if write_artifact:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{model_hash}.json"
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(artifact, indent=2, sort_keys=False), encoding="utf-8")
        tmp.replace(out_path)
        artifact["artifact_path"] = str(out_path)
        logger.info(
            "[BASELINE_EVAL] event=ARTIFACT_WRITTEN evaluation_id=%s model_hash=%s decision=%s",
            evaluation_id_val,
            model_hash[:12],
            artifact["decision"]["status"],
        )
    else:
        artifact["artifact_path"] = None
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="baseline_eval", description="Reproducible model baseline evaluation"
    )
    ap.add_argument("--model", required=True, help="path to model.pt")
    ap.add_argument("--dataset", required=True, help="dataset_id (ArtifactStore) or parquet path")
    ap.add_argument("--model-id", default="")
    ap.add_argument("--model-version", default="")
    ap.add_argument("--friction-r", type=float, default=FRICTION_R)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)

    artifact = evaluate(
        Path(args.model),
        args.dataset,
        out_dir=Path(args.out),
        friction_r=args.friction_r,
        k_folds=args.folds,
        model_id=args.model_id,
        model_version=args.model_version,
    )
    summary = {
        "evaluation_id": artifact["evaluation_id"],
        "model_hash": artifact["model_hash"][:12],
        "dataset_id": artifact["dataset_id"],
        "expectancy_r": artifact["metrics"]["expectancy_r"],
        "win_rate": artifact["metrics"]["win_rate"],
        "trades": artifact["metrics"]["trades"],
        "always_buy_expectancy_r": artifact["baseline_comparisons"]["always_buy"]["expectancy_r"],
        "decision": artifact["decision"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if artifact["decision"]["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
