"""Final-replica training tests (research/training-parity P2).

Contract:
  * N>=2 replicas with DISTINCT seeds (variance requires more than one).
  * Selection = MEDIAN replica by net expectancy — a lucky best seed is
    never auto-selected.
  * Every replica records seed, net expectancy, drawdown, best epoch, and
    the exact artifact sha256 (identity binding).
  * Aggregates: mean/median/std/min/max over replicas.
  * No evidence => selection refused (fail-closed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from nexus_scalp.model_lifecycle.final_replicas import (
    DEFAULT_REPLICAS,
    FinalReplicaTrainer,
)
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def _frame(n: int = 400, seed: int = 9, dim: int = 50) -> pl.DataFrame:
    import numpy as np

    rng = np.random.RandomState(seed)
    data = {f"feat_{i}": rng.randn(n).tolist() for i in range(dim)}
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [labels[i % 3] for i in range(n)]
    data["label_evaluated"] = [True] * n
    data["is_purged"] = [False] * n
    return pl.DataFrame(data)


def _trainer(tmp_path: Path, **over: Any) -> FinalReplicaTrainer:
    base: dict[str, Any] = dict(
        num_replicas=2,
        base_seed=42,
        num_folds=2,
        epochs_per_fold=1,
        purge_gap_bars=5,
        walk_forward_mode="expanding",
        artifact_dir=tmp_path / "replicas",
        trainer_kwargs={
            "min_rows_per_train_split": 20,
            "min_rows_per_test_split": 10,
            "governance_override": True,  # synthetic frames are UNKNOWN lineage
        },
        smoke=True,
    )
    base.update(over)
    return FinalReplicaTrainer(**base)


def test_replicas_get_distinct_seeds_and_identity(tmp_path: Path) -> None:
    out = _trainer(tmp_path, feature_dimension=50).train_replicas(_frame())
    rows = out.rows
    assert len(rows) == 2
    seeds = {r["seed"] for r in rows}
    assert seeds == {42, 43}
    shas = {r["artifact_sha256"] for r in rows}
    assert len(shas) == 2, "identical sha across different seeds is suspicious"
    assert all(len(r["artifact_sha256"]) == 64 for r in rows)


def test_summary_has_full_variance_block(tmp_path: Path) -> None:
    out = _trainer(tmp_path, feature_dimension=50).train_replicas(_frame())
    assert out.replicas == 2
    for key in (
        "net_expectancy_mean_r",
        "net_expectancy_median_r",
        "net_expectancy_std_r",
        "net_expectancy_min_r",
        "net_expectancy_max_r",
        "drawdown_max_r",
    ):
        assert key in out.to_dict()


def test_median_selection_not_lucky_best(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With 3 replicas whose OOS net expectancies are fabricated to 0.5 / 0.1 /
    5.0, the MEDIAN (0.1) is selected — never the lucky 5.0."""
    tr = _trainer(tmp_path, num_replicas=3)
    # Deterministic evidence injection: intercept each replica's full training
    # to record scripted OOS net expectancies and a stand-in artifact whose
    # bytes differ per replica.
    import nexus_scalp.training.walk_forward_trainer as wfm

    scripted = iter([(0.5, "a"), (0.1, "b"), (5.0, "c")])

    def fake_train(self, df, feature_cols):  # type: ignore[no-untyped-def]
        net, tag = next(scripted)
        self.last_convergence_metadata = {
            "net_expectancy_r": net,
            "max_fold_drawdown_r": 1.0,
            "folds": [{"best_epoch": 1}],
        }
        # Write a stand-in artifact file whose bytes differ per replica.
        path = self.artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tag.encode())
        return self._create_model(num_features=len(feature_cols))

    monkeypatch.setattr(WalkForwardTrainer, "train_and_validate", fake_train, raising=True)
    out = tr.train_replicas(_frame(), list(f"feat_{i}" for i in range(50)))
    # Scripted: replica1=0.5R, replica2=0.1R, replica3=5.0R. The median
    # (0.5R) is replica 1 — the lucky 5.0R replica is recorded but NOT
    # selected.
    assert out.selected_replica == 1
    assert out.selected_seed == 42
    assert out.net_expectancy_max_r == 5.0
    assert out.net_expectancy_median_r == 0.5
    assert out.selected_artifact_sha256  # identity recorded for the exact bytes


def test_single_replica_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="num_replicas"):
        _trainer(tmp_path, num_replicas=1)


def test_no_evidence_selection_refused(tmp_path: Path) -> None:
    tr = _trainer(tmp_path)

    import nexus_scalp.model_lifecycle.final_replicas as fr

    # Force _train_one to produce replicas with no expectancy evidence.
    orig = fr.FinalReplicaTrainer._train_one

    def no_evidence(self, df, feature_cols, replica, seed):  # type: ignore[no-untyped-def]
        from nexus_scalp.model_lifecycle.final_replicas import ReplicaResult

        return None, ReplicaResult(
            replica=replica,
            seed=seed,
            net_expectancy_r=None,
            max_fold_drawdown_r=None,
            best_epoch=None,
            artifact_sha256="0" * 64,
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(fr.FinalReplicaTrainer, "_train_one", no_evidence)
    with pytest.raises(RuntimeError, match="SELECTION_REFUSED"):
        tr.train_replicas(_frame(), list(f"feat_{i}" for i in range(50)))
    monkeypatch.undo()
    assert orig is not None
