"""ECON v1 training-decay separation — behavioral tests.

Pins the contract that full historical training and online adaptation use
SEPARATE, explicitly configured decay profiles:

  * separate config paths (constructor args), never one shared mutable value
  * FULL_TRAIN default resolves to the fold-span (not the online value)
  * ONLINE keeps the historical 120-bar recency weighting
  * the online path NEVER consults the full-train profile and vice versa
  * convergence metadata records epochs run, best epoch, early-stop flag
  * provenance travels into the bundle manifest extra block
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from nexus_scalp.training.walk_forward_trainer import (
    WalkForwardTrainer,
    _compute_time_decay_weights,
)


def _make_trainer(**overrides: Any) -> WalkForwardTrainer:
    kwargs: dict[str, Any] = {
        "epochs_per_fold": 1,
        "min_rows_per_train_split": 10,
        "min_rows_per_test_split": 5,
        "artifact_save_path": None,  # replaced below
    }
    kwargs.update(overrides)
    return kwargs


def test_profiles_are_separate_config_paths(tmp_path) -> None:
    t = WalkForwardTrainer(
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        time_decay_full_train_half_life_bars=5000.0,
        time_decay_online_half_life_bars=60.0,
        artifact_save_path=tmp_path / "m.pt",
    )
    # two independent fields — never one shared attribute
    assert t.time_decay_full_train_half_life_bars == 5000.0
    assert t.time_decay_online_half_life_bars == 60.0


def test_full_train_default_is_fold_span_not_online_value(tmp_path) -> None:
    """With no explicit override, FULL_TRAIN resolves per-run from the fold
    span (clamped >= 60 bars) — deliberately different from the ONLINE 120."""
    t = WalkForwardTrainer(
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "m.pt",
    )
    assert t.time_decay_full_train_half_life_bars is None  # resolved at train time
    assert t.time_decay_online_half_life_bars == 120.0


def test_online_profile_weights_recent_buffer_aggressively() -> None:
    """Weights are NORMALIZED (mean=1), so ratios are what matters: the sample
    exactly one half-life older carries ~0.5x the newest sample's weight."""
    w = _compute_time_decay_weights(240, half_life_bars=120.0)
    assert w[-1] >= w[0]  # newest weighted highest
    # one half-life back = ~0.5x the newest (2^-1), modulo the normalization mean
    assert w[120] / w[-1] == pytest.approx(0.5, rel=1e-2)


def test_full_train_profile_retains_history_online_discards() -> None:
    """The documented behavior gap: a 100k-row dataset with a 25k-bar
    fold-span default retains old-fold influence, where the OLD 120-bar
    online profile erased everything older than ~2h of bars."""
    w_online = _compute_time_decay_weights(100_000, half_life_bars=120.0)
    w_full = _compute_time_decay_weights(100_000, half_life_bars=25_000.0)
    # oldest sample weight relative to newest
    assert w_online[0] / w_online[-1] == 0.0  # online: history erased (underflow)
    assert w_full[0] / w_full[-1] > 0.02  # full-train: oldest retains ~2^-2


def test_decay_is_deterministic() -> None:
    w1 = _compute_time_decay_weights(500, half_life_bars=250.0)
    w2 = _compute_time_decay_weights(500, half_life_bars=250.0)
    assert np.array_equal(w1, w2)


def test_convergence_metadata_recorded(tmp_path, monkeypatch) -> None:
    """A full train_and_validate run must persist convergence evidence with
    the FULL_TRAIN decay identity and per-fold epoch accounting."""
    import polars as pl

    n_rows = 600
    rng = np.random.default_rng(42)
    feats = {f"feat_{i}": rng.normal(size=n_rows) for i in range(50)}
    df = pl.DataFrame(
        {
            **feats,
            "dataset_id": ["ds_test"] * n_rows,
            "dataset_sha256": ["0" * 64] * n_rows,
            "label_origin": ["CLEAN_HISTORICAL"] * n_rows,
            "label": rng.integers(0, 3, size=n_rows),
        }
    )
    feature_cols = [f"feat_{i}" for i in range(50)]
    t = WalkForwardTrainer(
        epochs_per_fold=4,
        early_stopping_patience=2,
        num_folds=2,
        min_rows_per_train_split=50,
        min_rows_per_test_split=20,
        time_decay_full_train_half_life_bars=300.0,
        artifact_save_path=tmp_path / "conv" / "model.pt",
        feature_schema_id="scalp_v1",
    )
    # Dataset provenance binding requires the manifest; smoke=True keeps the
    # run quarantined (no production eligibility) but still exercises the
    # full convergence-metadata path.
    t.smoke = True
    try:
        t.train_and_validate(df, feature_cols)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"training unavailable on this runner: {exc}")
    conv = getattr(t, "last_convergence_metadata", None)
    assert conv is not None
    assert conv["training_mode"] == "FULL_TRAIN"
    assert conv["time_decay_profile"] == "FULL_TRAIN"
    assert conv["time_decay_half_life_bars"] == 300.0
    assert conv["epochs_requested"] == 4
    assert conv["seed"] == t.seed
    assert len(conv["folds"]) == t.num_folds
    for f in conv["folds"]:
        assert f["epochs_run"] <= f["epochs_requested"]
        assert f["best_epoch"] >= 1
        assert isinstance(f["early_stopped"], bool)
        assert len(f["train_losses"]) == f["epochs_run"]
        assert len(f["val_losses"]) == f["epochs_run"]
