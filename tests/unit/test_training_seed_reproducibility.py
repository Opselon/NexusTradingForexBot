"""Seed-reproducibility regression net (Agent-4 reproducibility lane, 2026-09-09).

Pins the executable reproducibility contracts of the canonical walk-forward
trainer that the multi-seed probes (scratch/ns_agent4_seed/*) rely on:

  1. TRAIN-SEED-01  same (dataset, seed) => byte-identical fold evidence
                    (fold economics, OOS accuracy, prediction class counts,
                    converged head weights) — full train_and_validate, twice.
  2. TRAIN-SEED-02  seed locality: an interleaved run at a DIFFERENT seed must
                    not perturb a later same-seed rerun (the trainer re-seeds
                    the global torch/numpy streams at construction; oversampling
                    uses a local Generator). Regression net for the class of
                    cross-run RNG coupling defects.
  3. TRAIN-SEED-03  multi-seed stability contract: across N>=3 distinct seeds on
                    identical data, per-seed OOS evidence must be VARIANCE-
                    REPORTED, not hidden: fold economics + prediction class
                    counts must differ somewhere (distinct seeds produce
                    distinct training runs) and every per-seed run must carry
                    the same evidence keys — the evidence shape FinalReplica
                    selection consumes.
  4. TRAIN-SEED-04  baseline sanity on the same OOS population: a majority-class
                    policy must not be beaten by a degenerate predictor — pins
                    the _calculate_fold_economics no-trade=0.0 semantics so a
                    collapsed model can never look economically neutral-positive.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

DIM = 50
FOLDS = 4
EPOCHS = 3


def _frame(n: int = 2400, seed: int = 7) -> pl.DataFrame:
    """Learnable synthetic frame: label is a noisy linear rule over feat_0..3.
    Identical data for every trainer seed — only training stochasticity varies."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, DIM)).astype(np.float32)
    score = 1.4 * X[:, 0] - 1.2 * X[:, 1] + 0.9 * X[:, 2] - 0.8 * X[:, 3]
    noise = rng.normal(size=n) * 0.6
    y = np.where(score + noise > 1.0, 1, np.where(score + noise < -1.0, 2, 0))
    data = {f"feat_{i}": X[:, i].tolist() for i in range(DIM)}
    data["label"] = y.astype(int).tolist()
    data["label_evaluated"] = [True] * n
    data["is_purged"] = [False] * n
    return pl.DataFrame(data)


def _run(df: pl.DataFrame, out: Path, seed: int) -> dict:
    torch.set_num_threads(1)  # thread-count determinism on shared CI boxes
    tr = WalkForwardTrainer(
        num_folds=FOLDS,
        epochs_per_fold=EPOCHS,
        batch_size=128,
        purge_gap_bars=15,
        random_seed=seed,
        artifact_save_path=out / f"m_{seed}.pt",
        smoke=True,
        governance_override=True,  # synthetic frame carries UNKNOWN lineage
    )
    model = tr.train_and_validate(df, [f"feat_{i}" for i in range(DIM)])
    cm = tr.last_convergence_metadata
    return {
        "fold_economics": cm["fold_economics"],
        "net_expectancy_r": cm["net_expectancy_r"],
        "oos_accuracy": cm["oos_accuracy"],
        "oos_samples": cm["oos_samples"],
        "oos_prediction_class_counts": cm["oos_prediction_class_counts"],
        "head_weights_head": [float(v) for v in model.classifier.weight.flatten()[:6].detach()],
    }


def _evidence_key(r: dict) -> str:
    import json

    return json.dumps(r, sort_keys=True)


# ---------------------------------------------------------------------------
# TRAIN-SEED-01 — same-seed byte-identical evidence
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_fold_evidence(tmp_path: Path) -> None:
    df = _frame()
    r1 = _run(df, tmp_path, 42)
    r2 = _run(df, tmp_path, 42)
    assert _evidence_key(r1) == _evidence_key(r2), (
        "same (dataset, seed) must reproduce identical fold economics, OOS "
        "accuracy, prediction counts and converged head weights"
    )
    # and the evidence must be non-trivial: folds actually evaluated
    assert r1["oos_samples"] > 0
    assert len(r1["fold_economics"]) == FOLDS


# ---------------------------------------------------------------------------
# TRAIN-SEED-02 — seed locality under interleaved foreign-seed runs
# ---------------------------------------------------------------------------


def test_seed_locality_under_interleaved_foreign_seed(tmp_path: Path) -> None:
    df = _frame()
    r1 = _run(df, tmp_path, 42)
    _run(df, tmp_path, 43)  # foreign seed consumes the global RNG streams
    r3 = _run(df, tmp_path, 42)
    assert _evidence_key(r1) == _evidence_key(r3), (
        "a later same-seed rerun must not be perturbed by an interleaved "
        "different-seed run (trainer owns its seeding at construction)"
    )


# ---------------------------------------------------------------------------
# TRAIN-SEED-03 — multi-seed: distinct runs, identical evidence shape
# ---------------------------------------------------------------------------


def test_multi_seed_runs_differ_but_report_identical_evidence_shape(tmp_path: Path) -> None:
    df = _frame()
    runs = {seed: _run(df, tmp_path, seed) for seed in (42, 43, 44)}
    keys = {
        "fold_economics",
        "net_expectancy_r",
        "oos_accuracy",
        "oos_samples",
        "oos_prediction_class_counts",
        "head_weights_head",
    }
    for seed, r in runs.items():
        assert set(r) == keys, f"seed {seed} evidence shape drifted: missing {keys - set(r)}"
    # distinct seeds => distinct training outcomes (different converged weights)
    heads = [_evidence_key(runs[s]["head_weights_head"]) for s in (42, 43, 44)]
    assert len(set(heads)) == 3, "distinct seeds produced byte-identical head weights"
    # fold count identical across seeds (same deterministic fold geometry)
    for r in runs.values():
        assert len(r["fold_economics"]) == FOLDS
        for fold in r["fold_economics"]:
            assert set(fold) >= {
                "net_expectancy_r",
                "gross_expectancy_r",
                "trades",
                "max_drawdown_r",
                "no_trade_rate",
            }


# ---------------------------------------------------------------------------
# TRAIN-SEED-04 — baseline sanity: no-trade fold cannot fake positive economics
# ---------------------------------------------------------------------------


def test_collapsed_all_no_trade_model_scores_zero_not_positive_economics() -> None:
    tr = WalkForwardTrainer(friction_r=0.15, reward_r=1.2)
    preds = [0] * 60
    targets = np.array([1, 2] * 30, dtype=np.int64)
    econ = tr._calculate_fold_economics(preds, targets)
    assert econ["trades"] == 0
    assert econ["net_expectancy_r"] == 0.0
    assert econ["no_trade_rate"] == pytest.approx(1.0)


def test_no_information_active_trading_is_economically_negative() -> None:
    """No-information active trading must price out negative under the canonical
    friction/reward geometry: chance correctness (1/3) on directional classes
    cannot cover -1R + friction — a positive no-info run would mean the
    reward/friction geometry is mispriced (or a directional-only target
    distribution inflates chance correctness)."""
    tr = WalkForwardTrainer(friction_r=0.15, reward_r=1.2)
    # adversarial BEST CASE for the no-info policy: every target IS directional
    # (max chance correctness 1/2 across BUY/SELL) and predictions are balanced.
    targets = np.array([1, 2] * 150, dtype=np.int64)
    preds = np.array([1, 2] * 150, dtype=np.int64)
    rng = np.random.default_rng(5)
    perm = rng.permutation(len(targets))
    preds = preds[perm]  # chance-level alignment, not memorized order
    econ = tr._calculate_fold_economics(list(preds), targets)
    assert econ["trades"] == 300
    # chance correctness ~1/2 over directional classes:
    # E[net] = 0.5*1.2 - 0.5*1.0 - 0.15 = -0.05R < 0 (sampling noise tolerated)
    assert econ["net_expectancy_r"] < 0.0, (
        "no-information baseline must be economically negative; a positive "
        "no-info run would mean the reward/friction geometry is mispriced"
    )
