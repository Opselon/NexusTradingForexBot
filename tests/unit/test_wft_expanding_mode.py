"""Expanding (anchored) walk-forward mode tests (research/training-parity P0).

Geometry contract:
  * blocked (default)  — each fold trains only within its own slice:
        fold 1: [A|train][A|val]  fold 2: [B|train][B|val]  ...
  * expanding          — fold k trains on ALL rows from the dataset start
        through its purged train tail: TRAIN [A] -> [A+B] -> [A+B+C].
  * Both modes share identical purge/embargo semantics and identical
    validation windows — the mode ONLY changes the training window.
  * The mode is explicit (constructor arg, fail-loud on unknown values),
    recorded in convergence metadata and in the bundle manifest.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def _frame(n: int = 600, seed: int = 13) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    data = {name: rng.randn(n).tolist() for name in FEATURE_NAMES}
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [labels[i % 3] for i in range(n)]
    data["label_evaluated"] = [True] * n
    data["is_purged"] = [False] * n
    return pl.DataFrame(data)


def _capture_train_slices(monkeypatch: pytest.MonkeyPatch):
    """Captures the training row count per fold by spying on _fit_scaler.

    NOTE: test_agent8_w2_trainer_hygiene.py performs a module-level
    sys.modules purge + re-import, which can leave MULTIPLE
    walk_forward_trainer module instances alive in one pytest session. The
    test file's top-level `from ... import WalkForwardTrainer` may be bound
    to a DIFFERENT class object than `sys.modules[...]` holds at test time.
    To be robust, patch _fit_scaler on EVERY live instance of the module,
    wrapping the EXISTING attribute (staticmethod or bound function).
    """
    import sys

    import nexus_scalp.training.walk_forward_trainer as wf_module

    slices: list[int] = []
    seen_classes: set[int] = set()

    def patch_class(cls: Any) -> None:
        if id(cls) in seen_classes:
            return
        seen_classes.add(id(cls))
        current = cls.__dict__.get("_fit_scaler")
        # _fit_scaler is a @staticmethod: the stored attribute on the class
        # is a staticmethod wrapper whose __func__ is the plain function.
        target = current.__func__ if hasattr(current, "__func__") else current
        # Wrap as a plain function taking (self, X_raw); assigning a plain
        # function to the class makes it a bound method on instances.
        def spy(self, X_raw):  # type: ignore[no-untyped-def]
            slices.append(int(X_raw.shape[0]))
            return target(self, X_raw)

        monkeypatch.setattr(cls, "_fit_scaler", spy)

    for name, module in list(sys.modules.items()):
        if name == "nexus_scalp.training.walk_forward_trainer" and module is not None:
            cls = getattr(module, "WalkForwardTrainer", None)
            if cls is not None:
                patch_class(cls)
    # Also patch the class object THIS test file's import resolved to. After
    # the w2 purge, sys.modules holds a NEW module while this file's
    # top-level WalkForwardTrainer binding is the ORPHANED old class —
    # instances built from it never touch the sys.modules class.
    patch_class(WalkForwardTrainer)
    patch_class(wf_module.WalkForwardTrainer)
    return slices


def test_unknown_mode_fails_loud(tmp_path) -> None:
    with pytest.raises(ValueError, match="walk_forward_mode"):
        WalkForwardTrainer(
            artifact_save_path=tmp_path / "m.pt",
            walk_forward_mode="bogus",
        )


def test_blocked_mode_default_is_explicit(tmp_path) -> None:
    tr = WalkForwardTrainer(artifact_save_path=tmp_path / "m.pt")
    assert tr.walk_forward_mode == "blocked"
    tr2 = WalkForwardTrainer(
        artifact_save_path=tmp_path / "m2.pt", walk_forward_mode="blocked"
    )
    assert tr2.walk_forward_mode == "blocked"


def test_expanding_training_window_grows_across_folds(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In expanding mode the per-fold training set strictly GROWS: fold 2's
    train window contains fold 1's train window (anchored at dataset start).
    Validation windows stay fold-local in both modes."""
    n = 600
    num_folds = 3
    slices = _capture_train_slices(monkeypatch)

    tr = WalkForwardTrainer(
        num_folds=num_folds,
        epochs_per_fold=1,
        min_rows_per_train_split=50,
        min_rows_per_test_split=20,
        artifact_save_path=tmp_path / "exp" / "m.pt",
        walk_forward_mode="expanding",
        smoke=True,
        governance_override=True,
    )
    tr.train_and_validate(_frame(n), FEATURE_NAMES)

    # Last _fit_scaler call is the full-dataset final training; the first
    # num_folds calls are the folds. Fold train sizes must strictly increase.
    fold_sizes = slices[:num_folds]
    assert len(fold_sizes) == num_folds
    assert fold_sizes[0] < fold_sizes[1] < fold_sizes[2], fold_sizes
    # Anchored at dataset start: fold 2 and 3 start at 0 — the sizes must
    # exceed the pure-fold train sizes (blocked would be ~0.7*200=140 each).
    assert fold_sizes[1] > fold_sizes[0] + 100
    # Geometry metadata recorded.
    assert tr.last_convergence_metadata["walk_forward_mode"] == "expanding"
    geom = tr.last_convergence_metadata["fold_geometry"]
    assert [g["fold"] for g in geom] == [1, 2, 3]
    assert all(g["walk_forward_mode"] == "expanding" for g in geom)
    # Anchored: every fold's train window starts at index 0.
    assert all(g["train_start_idx"] == 0 for g in geom)
    # Strictly growing train windows.
    assert geom[0]["train_end_idx"] < geom[1]["train_end_idx"] < geom[2]["train_end_idx"]
    # Validation windows are fold-local and disjoint across folds.
    assert geom[0]["test_end_idx"] <= geom[1]["test_start_idx"]
    assert geom[1]["test_end_idx"] <= geom[2]["test_start_idx"]
    # Purge present between train end and test start in every fold.
    for g in geom:
        assert g["test_start_idx"] - g["train_end_idx"] >= tr.purge_gap


def test_blocked_geometry_unchanged(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocked mode keeps fold-local training windows (no accidental growth)."""
    n = 600
    num_folds = 3
    slices = _capture_train_slices(monkeypatch)

    tr = WalkForwardTrainer(
        num_folds=num_folds,
        epochs_per_fold=1,
        min_rows_per_train_split=50,
        min_rows_per_test_split=20,
        artifact_save_path=tmp_path / "blk" / "m.pt",
        walk_forward_mode="blocked",
        smoke=True,
        governance_override=True,
    )
    tr.train_and_validate(_frame(n), FEATURE_NAMES)

    fold_sizes = slices[:num_folds]
    assert len(fold_sizes) == num_folds
    # Blocked: each fold trains ~70% of one slice — roughly equal sizes,
    # none reaches the expanding sizes (which include prior folds).
    assert max(fold_sizes) < min(fold_sizes) * 1.5
    assert tr.last_convergence_metadata["walk_forward_mode"] == "blocked"
    geom = tr.last_convergence_metadata["fold_geometry"]
    assert all(g["train_start_idx"] == i * (n // num_folds) for i, g in enumerate(geom))


def test_no_future_samples_enter_training_in_either_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial: the training window must NEVER reach the fold's
    validation period, in either geometry."""
    for mode in ("blocked", "expanding"):
        n = 600
        num_folds = 3
        _capture_train_slices(monkeypatch)
        tr = WalkForwardTrainer(
            num_folds=num_folds,
            epochs_per_fold=1,
            min_rows_per_train_split=50,
            min_rows_per_test_split=20,
            artifact_save_path=tmp_path / f"nf_{mode}" / "m.pt",
            walk_forward_mode=mode,
            smoke=True,
            governance_override=True,
        )
        tr.train_and_validate(_frame(n), FEATURE_NAMES)
        geom = tr.last_convergence_metadata["fold_geometry"]
        for g in geom:
            assert g["train_end_idx"] + tr.purge_gap <= g["test_start_idx"], (
                f"mode={mode} fold={g['fold']}: training window reaches "
                "past the purged boundary"
            )
        monkeypatch.undo()
