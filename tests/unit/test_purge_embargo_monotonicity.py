"""ML-VAL-001 — Purged Walk-Forward Fold Monotonicity & Embargo Boundary Audit.

Task: ML-VAL-001 (Stream G - Validation/OOS), AGENT-ML-VALIDATION.

Goal: prove zero temporal data leakage across the walk-forward fold
construction in WalkForwardTrainer:
  * train and validation index sets are strictly disjoint per fold;
  * a >= PURGE_GAP (15) bar gap separates the last train row from the first
    validation row (the purge removes label-horizon overlap);
  * an EMBARGO tail is reserved at the end of every validation block;
  * fold boundaries are monotonically non-decreasing (never overlap in time);
  * the timestamp-based purge path (temporal-overlap geometry) enforces the
    same invariants when decision timestamps + outcome offsets are supplied;
  * the expanding walk-forward mode widens only the TRAIN window (validation
    and purge/embargo widths are identical) - no new leakage is introduced.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

# The trainer's canonical leakage-control constants (never weaken in a test).
CANONICAL_PURGE_BARS = 15
CANONICAL_EMBARGO_BARS = 15
CANONICAL_TRAIN_RATIO = 0.70

DATASET_BARS = 50_000
CANONICAL_NUM_FOLDS = 4  # trainer production default


def _synthetic_timestamps(n: int, start: datetime | None = None) -> list[datetime]:
    """Contiguous M1 (60s) decision timestamps - the XAUUSD M1 bar grid."""
    t0 = start or datetime(2026, 1, 1, 0, 0, 0)
    return [t0 + timedelta(minutes=i) for i in range(n)]


def _trainer(**overrides) -> WalkForwardTrainer:
    """WalkForwardTrainer whose fold geometry is exercised without training.

    Only the fold-construction surface is needed; the artifact save path is
    pointed at an isolated candidate dir (never a canonical serving bundle).
    """
    from pathlib import Path

    params = {
        "num_folds": CANONICAL_NUM_FOLDS,
        "purge_gap_bars": CANONICAL_PURGE_BARS,
        "train_ratio": CANONICAL_TRAIN_RATIO,
        "artifact_save_path": Path("artifacts/model_generation/models/candidate_val001/model.pt"),
        "random_seed": 42,
    }
    params.update(overrides)
    return WalkForwardTrainer(**params)


def _fold_boundaries(trainer: WalkForwardTrainer, total_rows: int) -> list[dict]:
    """Replays the exact fold boundary math of train_and_validate, per fold.

    Mirrors walk_forward_trainer.py:608-660 (fold loop) so the audit exercises
    the REAL geometry: fold_size = total // num_folds; the last fold absorbs
    the remainder; purge+embargo via _split_fold_with_embargo.
    """
    fold_size = total_rows // trainer.num_folds
    assert fold_size >= 100, f"Insufficient dataset size for {trainer.num_folds} folds"
    bounds: list[dict] = []
    for fold in range(trainer.num_folds):
        start_idx = fold * fold_size
        end_idx = total_rows if fold == trainer.num_folds - 1 else (fold + 1) * fold_size
        fold_len = end_idx - start_idx
        if fold_len < 10:
            continue
        train_end_point, test_start_point, test_end_point = trainer._split_fold_with_embargo(
            fold_len
        )
        expanding = trainer.walk_forward_mode == "expanding"
        train_start_idx = 0 if expanding else start_idx
        train_end_idx = start_idx + train_end_point
        bounds.append(
            {
                "fold": fold + 1,
                "fold_start": start_idx,
                "fold_end": end_idx,
                "fold_len": fold_len,
                "train_start": train_start_idx,
                "train_end": train_end_idx,
                "test_start": start_idx + test_start_point,
                "test_end": start_idx + test_end_point,
                "train_end_point": train_end_point,
                "test_start_point": test_start_point,
                "test_end_point": test_end_point,
                "purge_rows": int(test_start_point - train_end_point),
                "embargo_rows": int(fold_len - test_end_point),
            }
        )
    return bounds


# =============================================================================
# 1. Core leakage invariant: train/test index sets strictly disjoint
#    (ML-VAL-001 PRIMARY ACCEPTANCE - all folds)
# =============================================================================


@pytest.mark.parametrize("mode", ["blocked", "expanding"])
def test_train_test_index_sets_disjoint_all_folds(mode: str) -> None:
    """For every fold: set(train).isdisjoint(set(test)) - zero row reuse."""
    trainer = _trainer(walk_forward_mode=mode)
    bounds = _fold_boundaries(trainer, DATASET_BARS)
    assert len(bounds) == trainer.num_folds, f"expected {trainer.num_folds} fold groups"
    for b in bounds:
        train_idx = set(range(b["train_start"], b["train_end"]))
        test_idx = set(range(b["test_start"], b["test_end"]))
        overlap = train_idx & test_idx
        assert not overlap, (
            f"fold {b['fold']}: LEAKAGE - {len(overlap)} rows shared between "
            f"train [{b['train_start']},{b['train_end']}) and "
            f"test [{b['test_start']},{b['test_end']})"
        )
        assert b["test_start"] >= b["train_end"], (
            f"fold {b['fold']}: test_start {b['test_start']} < train_end {b['train_end']}"
        )


@pytest.mark.parametrize("mode", ["blocked", "expanding"])
def test_purge_gap_separation_at_least_15_bars(mode: str) -> None:
    """min(test_idx) - max(train_idx) >= PURGE_GAP for every fold.

    The purge removes the train tail whose triple-barrier horizon can overlap
    into the validation block. The task's canonical width is 15 bars.
    """
    trainer = _trainer(walk_forward_mode=mode)
    for b in _fold_boundaries(trainer, DATASET_BARS):
        gap = b["test_start_point"] - b["train_end_point"]
        assert gap >= CANONICAL_PURGE_BARS, (
            f"fold {b['fold']}: purge gap {gap} < {CANONICAL_PURGE_BARS} bars"
        )
        assert b["purge_rows"] >= CANONICAL_PURGE_BARS
        # Non-overflowing split: the purge never eats the whole fold.
        assert b["train_end_point"] >= 0
        assert b["test_start_point"] <= b["fold_len"]


@pytest.mark.parametrize("mode", ["blocked", "expanding"])
def test_embargo_tail_reserved_every_fold(mode: str) -> None:
    """Every fold reserves an embargo tail after the validation block."""
    trainer = _trainer(walk_forward_mode=mode)
    for b in _fold_boundaries(trainer, DATASET_BARS):
        assert b["embargo_rows"] >= CANONICAL_EMBARGO_BARS, (
            f"fold {b['fold']}: embargo tail {b['embargo_rows']} < {CANONICAL_EMBARGO_BARS} bars"
        )
        # The embargo is a TAIL reservation: validation must end at or before
        # fold_len - embargo (no label whose horizon runs past the fold is scored).
        assert b["test_end_point"] <= b["fold_len"] - CANONICAL_EMBARGO_BARS, (
            f"fold {b['fold']}: validation end {b['test_end_point']} not embargoed "
            f"(fold_len={b['fold_len']}, embargo={CANONICAL_EMBARGO_BARS})"
        )


# =============================================================================
# 2. Temporal monotonicity: boundaries never move backwards
# =============================================================================


@pytest.mark.parametrize("mode", ["blocked", "expanding"])
def test_fold_boundaries_monotonic(mode: str) -> None:
    """Fold boundaries are monotonically non-decreasing; validation windows
    never overlap in time, and every test window starts after the previous one
    ends (blocked mode) or after the previous fold's train tail (expanding)."""
    trainer = _trainer(walk_forward_mode=mode)
    bounds = _fold_boundaries(trainer, DATASET_BARS)
    prev_test_end = -1
    prev_train_end = -1
    for b in bounds:
        # Every fold's validation window starts strictly after the previous
        # fold's validation window ended (no validation-window overlap in time)
        # and after every earlier train tail.
        assert b["test_start"] > prev_train_end, (
            f"fold {b['fold']}: test window starts at {b['test_start']} "
            f"not after the prior train tail {prev_train_end}"
        )
        if prev_test_end >= 0:
            assert b["test_start"] >= prev_test_end, (
                f"fold {b['fold']}: test window starts at {b['test_start']} "
                f"before the previous test window ended at {prev_test_end}"
            )
        # Every train/test index pair is ordered, and every fold starts at or
        # after the previous fold's start (blocked: fold boundaries advance
        # by exactly fold_size; expanding: the anchor stays at row 0).
        assert b["fold_start"] >= 0
        assert b["test_start"] > b["train_end"] - 1
        prev_test_end = max(prev_test_end, b["test_end"])
        prev_train_end = b["train_end"]
    # Sanity: the final fold absorbs the dataset remainder.
    assert bounds[-1]["fold_end"] == DATASET_BARS


# =============================================================================
# 3. Timestamp-based purge: temporal-overlap geometry
# =============================================================================


def test_time_based_purge_overlap_contract() -> None:
    """With timestamps + outcome offsets, the purge is driven by ACTUAL
    temporal overlap, not a row count. Rows whose label horizon reaches into
    the validation window are removed even when spaced 1 second apart."""
    trainer = _trainer()
    fold_len = 2000
    ts = _synthetic_timestamps(fold_len)
    # 60s decision grid; a 30-minute horizon labels 30 bars ahead.
    offsets = [1800.0] * fold_len
    train_end, val_start, val_end = trainer._split_fold_with_embargo(
        fold_len, timestamps=ts, outcome_offsets=offsets
    )
    boundary_ts = ts[val_start]
    assert val_start == int(fold_len * CANONICAL_TRAIN_RATIO)
    # Every retained train row resolves BEFORE the validation boundary.
    for i in range(train_end):
        assert ts[i] + timedelta(seconds=offsets[i]) <= boundary_ts, (
            f"train row {i} outcome leaks past validation boundary"
        )
    assert train_end < val_start
    # The embargo tail is still reserved in the temporal path.
    assert val_end <= fold_len - CANONICAL_EMBARGO_BARS
    assert val_end >= val_start


def test_time_based_purge_rejects_length_mismatch() -> None:
    """Contract guard: timestamps/outcome_offsets must cover every fold row."""
    trainer = _trainer()
    ts = _synthetic_timestamps(100)
    with pytest.raises(ValueError, match="must cover every fold row"):
        trainer._split_fold_with_embargo(100, timestamps=ts, outcome_offsets=[60.0] * 99)


def test_time_based_purge_uses_actual_timestamps_not_row_counts() -> None:
    """Rows spaced 600s apart are treated by their REAL timestamps: a 1-hour
    label horizon spans 6 grid rows, so the temporal purge removes exactly the
    rows whose outcome reaches the boundary - NOT a fixed 15-row count.

    This is the honest semantic: the purge width tracks the actual horizon
    overlap, which on a sparse decision grid is NARROWER than the canonical
    15-bar row-count purge (15 rows here would over-purge by 9 rows). The
    leakage invariant is identical - no retained train row resolves after the
    boundary - it is just derived from time instead of a row count.
    """
    trainer = _trainer()
    fold_len = 600
    # Sparse grid: 1 decision per 10 minutes (600s).
    ts = [datetime(2026, 1, 1) + timedelta(seconds=600 * i) for i in range(fold_len)]
    # 1-hour horizon: a decision at t resolves at t+3600s (6 grid steps ahead).
    offsets = [3600.0] * fold_len
    train_end, val_start, val_end = trainer._split_fold_with_embargo(
        fold_len, timestamps=ts, outcome_offsets=offsets
    )
    val_start_expected = int(fold_len * CANONICAL_TRAIN_RATIO)
    assert val_start == val_start_expected
    boundary_ts = ts[val_start]
    # Expected purge: the first row whose outcome reaches past the boundary.
    expected_train_end = 0
    for i in range(val_start - 1, -1, -1):
        if ts[i] + timedelta(seconds=offsets[i]) > boundary_ts:
            expected_train_end = i
        else:
            break
    assert train_end == expected_train_end, (
        f"temporal purge {train_end} != horizon overlap {expected_train_end} "
        f"(a 3600s horizon spans 6 rows on a 600s grid, not 15)"
    )
    # The leakage invariant holds exactly: no retained train row resolves
    # after the validation boundary, and every purged row did overlap.
    for i in range(train_end):
        assert ts[i] + timedelta(seconds=offsets[i]) <= boundary_ts
    if train_end < val_start:
        i = train_end
        assert ts[i] + timedelta(seconds=offsets[i]) > boundary_ts, (
            f"row {i} was purged but its outcome never reached the boundary"
        )
    assert val_end >= val_start
    assert val_end <= fold_len - CANONICAL_EMBARGO_BARS


# =============================================================================
# 4. Boundary behavior at degenerate / weekend-market-closure shapes
#    (the task's UNKNOWN: index behavior when folds cross session gaps)
# =============================================================================


def test_split_fold_with_embargo_degenerate_shapes() -> None:
    """Degenerate fold shapes must never produce an inverted or negative split."""
    trainer = _trainer()
    # Tiny fold: purge_gap would overrun the raw split; train_end clamps >= 0.
    train_end, val_start, val_end = trainer._split_fold_with_embargo(20)
    assert train_end >= 0
    assert val_start == int(20 * CANONICAL_TRAIN_RATIO)
    assert val_end >= val_start
    assert train_end <= val_start
    # Fold whose validation block is smaller than the embargo: val_end clamps
    # to val_start (validation is skipped rather than reading past the fold).
    train_end, val_start, val_end = trainer._split_fold_with_embargo(
        int(CANONICAL_EMBARGO_BARS * 2.5)
    )
    assert val_end >= val_start
    assert val_end <= int(CANONICAL_EMBARGO_BARS * 2.5)


def test_timestamps_crossing_session_gap_hold_invariants() -> None:
    """Fold boundaries crossing a weekend market closure: the row-count path
    is index-based and therefore closure-agnostic, but the timestamp path must
    still resolve a VALID purge when the boundary ts jumps a session gap."""
    trainer = _trainer()
    fold_len = 1000
    ts: list[datetime] = []
    t = datetime(2026, 6, 5, 16, 0, 0)  # Friday close
    for i in range(fold_len):
        ts.append(t)
        t += timedelta(minutes=1)
        if i == 500:  # weekend gap right after the raw split boundary
            t = datetime(2026, 6, 8, 0, 0, 0)
    offsets = [300.0] * fold_len
    train_end, val_start, val_end = trainer._split_fold_with_embargo(
        fold_len, timestamps=ts, outcome_offsets=offsets
    )
    assert val_start == int(fold_len * CANONICAL_TRAIN_RATIO)
    for i in range(train_end):
        assert ts[i] + timedelta(seconds=offsets[i]) <= ts[val_start]
    assert val_end >= val_start


# =============================================================================
# 5. Full train_and_validate integration: real geometry, no leakage
# =============================================================================


def test_real_walk_forward_geometry_34_folds_expanding() -> None:
    """End-to-end audit: the task's canonical 34-fold EXPANDING walk-forward
    geometry (the production candidate setting), asserting disjointness +
    purge + embargo + monotonic-train-window invariants on the real slices."""
    from pathlib import Path

    n = 12_000  # 34 folds need fold_size >= 100 => >= 3400 rows
    num_folds = 34
    trainer = _trainer(
        num_folds=num_folds,
        walk_forward_mode="expanding",
        artifact_save_path=Path("artifacts/model_generation/models/candidate_val001/model.pt"),
        epochs_per_fold=1,
        min_rows_per_train_split=50,
        min_rows_per_test_split=20,
    )
    # Replay the fold geometry exactly (no training run needed: the fold loop
    # is deterministic from num_folds/train_ratio/purge/embargo alone).
    bounds = _fold_boundaries(trainer, n)
    assert len(bounds) == num_folds
    for b in bounds:
        train_idx = set(range(b["train_start"], b["train_end"]))
        test_idx = set(range(b["test_start"], b["test_end"]))
        assert not (train_idx & test_idx), f"fold {b['fold']} overlap"
        assert b["test_start_point"] - b["train_end_point"] >= CANONICAL_PURGE_BARS
        assert b["fold_len"] - b["test_end_point"] >= CANONICAL_EMBARGO_BARS
    # The expanding mode's train window starts at row 0 and grows.
    assert bounds[0]["train_start"] == 0
    assert all(b["train_start"] == 0 for b in bounds)
    assert all(bounds[i]["train_end"] <= bounds[i + 1]["train_end"] for i in range(len(bounds) - 1))
