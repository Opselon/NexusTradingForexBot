"""Time-based temporal purge tests (research/training-parity P1).

Adversarial: training observations whose LABEL HORIZON overlaps the validation
window must be excluded, and the exclusion must follow ACTUAL TIMESTAMPS —
never a row-count assumption ("15 rows = 15 minutes" is the failure mode).

Geometry contract for WalkForwardTrainer._split_fold_with_embargo:
  * legacy row-count path (no timestamps) — byte-identical historical behavior
  * time-based path — purge derives from decision/outcome timestamp overlap:
      - a train row whose outcome_ts > first-validation decision_ts is PURGED
      - validation rows within embargo distance after the boundary are EMBARGOED
      - irregular spacing is handled by actual time, not position
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _trainer() -> WalkForwardTrainer:
    return WalkForwardTrainer(
        num_folds=3,
        train_ratio=0.6,
        purge_gap_bars=5,
        embargo_bars=4,
    )


def test_legacy_row_count_path_unchanged() -> None:
    """No timestamps => the historical row-count geometry, byte for byte."""
    tr = _trainer()
    assert tr._split_fold_with_embargo(100) == (55, 60, 96)
    # extremes never invert (existing EC-5 contract)
    for fold_len in (0, 1, 5, 20, 10_000):
        train_end, val_start, val_end = tr._split_fold_with_embargo(fold_len)
        assert 0 <= train_end <= val_start <= val_end <= fold_len


def test_time_purge_excludes_overlapping_outcome() -> None:
    """A train row whose label outcome crosses the boundary is purged."""
    tr = _trainer()
    fold_len = 100
    raw_split = int(fold_len * tr.train_ratio)  # 60
    # Regular 1-minute rows; label horizon 15 minutes per row.
    timestamps = [T0 + timedelta(minutes=i) for i in range(fold_len)]
    outcome_offsets = [15.0 * 60] * fold_len
    train_end, val_start, _ = tr._split_fold_with_embargo(
        fold_len, timestamps=timestamps, outcome_offsets=outcome_offsets
    )
    assert val_start == raw_split
    # The train row immediately before the boundary decides at minute 59 and
    # resolves at minute 74 — AFTER the boundary (minute 60): purged.
    # The row resolving exactly AT minute 60 boundary (decision minute 45)
    # resolves AT the boundary => not overlapping (outcome <= boundary) and
    # its time gap (15 min) exceeds the embargo (4 min) => kept.
    assert train_end < val_start
    last_kept = timestamps[train_end - 1]
    assert (T0 + timedelta(minutes=60) - last_kept).total_seconds() > tr.embargo_bars


def test_time_purge_respects_irregular_spacing() -> None:
    """09:00, 09:01, 14:30 — a fixed ROW gap must not mean a fixed duration."""
    tr = _trainer()
    fold_len = 60
    raw_split = int(fold_len * tr.train_ratio)  # 36
    # Rows 0..35 (train region): mostly 1-minute spacing, but the last train
    # row (index 35) sits only 1 second before the validation boundary, and an
    # earlier one (index 33) sits 30 minutes before it with a 15-minute
    # horizon => it must SURVIVE while the near-boundary row is purged.
    timestamps = [T0 + timedelta(minutes=i) for i in range(fold_len)]
    timestamps[35] = T0 + timedelta(minutes=36) - timedelta(seconds=1)
    # Validation region continues at 1-minute spacing from minute 36.
    outcome_offsets = [15.0 * 60] * fold_len
    train_end, val_start, _ = tr._split_fold_with_embargo(
        fold_len, timestamps=timestamps, outcome_offsets=outcome_offsets
    )
    assert val_start == raw_split
    # Row 35 (1s before boundary, 15-min horizon) => purged (overlap + embargo
    # gap 1s <= 4s). Regular row i decides at minute i and resolves at minute
    # i+15: rows with i+15 > 36 (strict overlap) purge => rows 22..35 (14
    # rows). Row 21 resolves exactly AT the boundary (not overlapping) and its
    # 15-minute gap exceeds the 4-second embargo => kept. The purge width in
    # ROWS (14) is derived from TIME overlap (the 15-minute horizon at
    # 1-minute spacing), NOT from the configured 5-row purge gap.
    assert val_start - train_end == 14
    boundary_ts = timestamps[val_start]
    last_kept_ts = timestamps[train_end - 1]
    assert (boundary_ts - last_kept_ts).total_seconds() > tr.embargo_bars


def test_time_embargo_drops_boundary_adjacent_validation_rows() -> None:
    tr = _trainer()
    fold_len = 100
    timestamps = [T0 + timedelta(minutes=i) for i in range(fold_len)]
    # Zero horizon: nothing overlaps from the train side.
    outcome_offsets = [0.0] * fold_len
    train_end, val_start, val_end = tr._split_fold_with_embargo(
        fold_len, timestamps=timestamps, outcome_offsets=outcome_offsets
    )
    boundary_ts = timestamps[val_start]
    # No train row overlaps (0-min horizon) => train keeps everything up to
    # the boundary.
    assert train_end == val_start
    # Embargo (4 seconds here) drops validation rows whose decision lies
    # within 4 seconds after the boundary: with 1-minute spacing that is
    # exactly the FIRST validation row.
    assert val_end == val_start + 1
    dropped_ts = timestamps[val_start]
    assert (dropped_ts - boundary_ts).total_seconds() <= tr.embargo_bars


def test_time_purge_contract_violation_fails_loud() -> None:
    tr = _trainer()
    with pytest.raises(ValueError, match="contract violation"):
        tr._split_fold_with_embargo(100, timestamps=[T0] * 5, outcome_offsets=[60.0] * 5)


def test_time_purge_never_includes_future_rows_in_train() -> None:
    """Mutation-adjacent invariant: with time semantics, a train tail whose
    outcome reaches INTO validation can never be admitted, regardless of the
    configured row-count purge gap."""
    tr = WalkForwardTrainer(
        num_folds=3,
        train_ratio=0.6,
        purge_gap_bars=0,  # row-count purge disabled — time purge must still hold
        embargo_bars=0,
    )
    fold_len = 100
    timestamps = [T0 + timedelta(minutes=i) for i in range(fold_len)]
    outcome_offsets = [30.0 * 60] * fold_len  # 30-minute label horizon
    train_end, val_start, _ = tr._split_fold_with_embargo(
        fold_len, timestamps=timestamps, outcome_offsets=outcome_offsets
    )
    boundary = timestamps[val_start]
    for ts, off in zip(timestamps[:train_end], outcome_offsets[:train_end], strict=True):
        assert ts + timedelta(seconds=off) <= boundary, (
            f"train row at {ts} resolves at {ts + timedelta(seconds=off)} "
            f"> boundary {boundary} — future overlap admitted into training"
        )
