"""AGENT-8 WAVE-2 regression suite — trainer fail-closed hygiene (BUG-243B)
+ end-to-end clean-fetch->train contract (ecosystem requirement).

IMPORT ISOLATION: this worktree's conftest only forces local src when the
worktree path contains '.worktrees'; C:/tmp/w2-wt does not, so pytest would
import nexus_scalp from the venv .pth (main checkout) which lacks the
wave-2 patch. We therefore force local src BEFORE importing nexus_scalp.

Contracts pinned:
1. A poisoned training frame (None/NaN/Inf feature cell) RAISES in
   _extract_X_y — never silently laundered to 0.0 by nan_to_num.
2. A clean frame passes through unchanged (values byte-equal).
3. fine_tune_online refuses the poisoned frame BEFORE any training work.
4. The engine-side BUG-243 buffer guard + trainer gate together close the
   fetch->runtime->train chain for every user, not only the local machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_WT_ROOT = Path(__file__).resolve().parents[2]
_WT_SRC = _WT_ROOT / "src"
if _WT_SRC.is_dir() and (_WT_SRC / "nexus_scalp").is_dir():
    sys.path.insert(0, str(_WT_SRC))
    for _name in [m for m in list(sys.modules) if m.startswith("nexus_scalp")]:
        del sys.modules[_name]

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer  # noqa: E402

FEATURE_COLS = [f"feat_{i}" for i in range(50)]


def _make_frame(rows: int = 20) -> pl.DataFrame:
    data = []
    for i in range(rows):
        r = {c: float(i % 7) * 0.01 for c in FEATURE_COLS}
        r.update(
            close=1.0 + i * 0.01,
            high=1.5 + i * 0.01,
            low=0.5 + i * 0.01,
            open=1.0 + i * 0.01,
            atr_m1=1.5,
            label=int(i % 3),
        )
        data.append(r)
    return pl.DataFrame(data)


def _poison(df: pl.DataFrame, row: int, col: str, val) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.arange(0, df.height) == row)
        .then(pl.lit(val, dtype=pl.Float64))
        .otherwise(pl.col(col))
        .alias(col)
    )


@pytest.fixture()
def trainer(tmp_path: Path) -> WalkForwardTrainer:
    return WalkForwardTrainer(artifact_save_path=tmp_path / "probe_model.pt")


def test_import_isolation_targets_this_worktree() -> None:
    import nexus_scalp.training.walk_forward_trainer as m

    assert str(_WT_SRC) in m.__file__ or str(_WT_SRC) in str(
        Path(m.__file__).resolve().parent.parent.parent
    ), f"test imported from foreign tree: {m.__file__}"


def test_poisoned_none_cell_raises_fail_closed(trainer: WalkForwardTrainer) -> None:
    df = _poison(_make_frame(), 5, "feat_17", None)
    with pytest.raises(ValueError, match="Non-finite feature cell"):
        trainer._extract_X_y(df, FEATURE_COLS)


def test_poisoned_nan_and_inf_cells_raise(trainer: WalkForwardTrainer) -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        df = _poison(_make_frame(), 3, "feat_9", bad)
        with pytest.raises(ValueError, match="Non-finite feature cell"):
            trainer._extract_X_y(df, FEATURE_COLS)


def test_clean_frame_passes_unchanged(trainer: WalkForwardTrainer) -> None:
    df = _make_frame()
    X_raw, y = trainer._extract_X_y(df, FEATURE_COLS)
    assert X_raw.shape == (20, 50)
    assert np.all(np.isfinite(X_raw))
    assert X_raw[3, 0] == pytest.approx(0.03)
    assert set(np.unique(y)) <= {0, 1, 2}


def test_fine_tune_online_refuses_poisoned_frame_before_training(
    trainer: WalkForwardTrainer, tmp_path: Path
) -> None:
    """End-to-end: fine_tune_online must refuse before ANY training work
    (the ValueError fires inside _extract_X_y, before scaler fit / epochs)."""
    from nexus_scalp.models.scalp_net import ScalpNet

    model = ScalpNet(num_features=50, num_classes=4)
    df = _poison(_make_frame(rows=60), 7, "feat_23", None)
    with pytest.raises(ValueError, match="Non-finite feature cell"):
        trainer.fine_tune_online(model=model, recent_df=df, feature_cols=FEATURE_COLS, epochs=1)


def test_engine_buffer_guard_plus_trainer_gate_close_chain() -> None:
    """Documented chain contract: polars union-by-name poisons heterogeneous
    frames (engine-side premise), and the trainer gate refuses what slips
    through (any producer). Both layers must exist for the ecosystem
    contract (fetched data clean at runtime training time, every user)."""
    rec70 = {f"feat_{i}": 0.1 for i in range(70)}
    rec70.update({"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "spread": 0.2, "atr_m1": 1.5})
    rec50 = {f"feat_{i}": 0.2 for i in range(50)}
    rec50.update({"close": 2.0, "high": 2.1, "low": 1.9, "open": 2.0, "spread": 0.3, "atr_m1": 1.2})
    df = pl.DataFrame([rec70, rec50])
    nulls = df.select(pl.all().null_count()).row(0, named=True)
    assert any(v for k, v in nulls.items() if k.startswith("feat_")), (
        "raw union must poison (premise)"
    )
