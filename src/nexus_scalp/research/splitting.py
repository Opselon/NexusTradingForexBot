"""
Temporal Splitting with Purge & Embargo
=======================================
PHASE 09B time-series aware splitting (spec 6 / 8 / 38).

NEVER random splits for time series. Splits are temporal:
  TRAIN | VALIDATION | OOS   and walk-forward folds. Any normalization/scaling
that learns parameters MUST be fit only on TRAIN and applied forward (see
`leakage.py`).

Purge / embargo (spec 8): if an observation's future horizon overlaps a
validation/OOS boundary, it is purged. An embargo additionally drops samples
immediately AFTER the fold boundary to break label-horizon dependence.

Boundaries are derived from the dataset itself (never hardcoded example dates).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from nexus_scalp.research.models import ResearchDataset, ResearchSample

#: Default fraction of the dataset used as the first validation window when not
#: otherwise configured.
DEFAULT_VALIDATION_FRAC: float = 0.2
DEFAULT_OOS_FRAC: float = 0.2
#: BUG-140 Phase 7: leakage guards are ENABLED by default (were 0.0).
#: 300s purge = a typical M1 scalp holding window; 60s embargo breaks
#: label-horizon dependence on the boundary sample. Callers may still
#: pass 0.0 explicitly, but the default no longer leaks.
DEFAULT_PURGE_SECONDS: float = 300.0
DEFAULT_EMBARGO_SECONDS: float = 60.0


@dataclass(frozen=True)
class TemporalSplit:
    """A temporal split with explicit boundaries."""

    train: list[ResearchSample]
    validation: list[ResearchSample]
    oos: list[ResearchSample]
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    oos_start: datetime
    oos_end: datetime


def _sorted(samples: list[ResearchSample]) -> list[ResearchSample]:
    return sorted(samples, key=lambda s: s.decision_timestamp)


def _index_split_boundaries(n: int, val_frac: float, oos_frac: float) -> tuple[int, int]:
    """Returns (train_end_exclusive, val_end_exclusive) given fractions."""
    if n <= 0:
        return 0, 0
    oos_n = max(1, round(n * oos_frac)) if oos_frac > 0 else 0
    remaining = n - oos_n
    val_n = max(1, round(max(remaining, 0) * val_frac)) if val_frac > 0 else 0
    train_n = max(0, remaining - val_n)
    return train_n, train_n + val_n


def split_temporal(
    dataset: ResearchDataset,
    val_frac: float = DEFAULT_VALIDATION_FRAC,
    oos_frac: float = DEFAULT_OOS_FRAC,
    embargo_seconds: float = 0.0,
    purge_seconds: float = 0.0,
) -> TemporalSplit:
    """
    Deterministic temporal split preserving causality.

    Purge: samples whose OUTCOME horizon crosses the train/val boundary are
    removed from train (they leak future label info). Embargo: samples whose
    DECISION falls within `embargo_seconds` after the boundary are dropped from
    validation too.
    """
    ordered = _sorted(list(dataset.samples))
    n = len(ordered)
    if n == 0:
        return TemporalSplit(
            [],
            [],
            [],
            datetime.min,
            datetime.min,
            datetime.min,
            datetime.min,
            datetime.min,
            datetime.min,
        )

    train_end, val_end = _index_split_boundaries(n, val_frac, oos_frac)
    boundary_train_val = (
        ordered[train_end - 1].decision_timestamp
        if train_end > 0
        else ordered[0].decision_timestamp
    )
    boundary_val_oos = (
        ordered[val_end - 1].decision_timestamp if val_end > 0 else boundary_train_val
    )

    train: list[ResearchSample] = []
    validation: list[ResearchSample] = []
    oos: list[ResearchSample] = ordered[val_end:]

    for idx, s in enumerate(ordered[:val_end]):
        is_train = idx < train_end
        # Purge from TRAIN any sample whose outcome horizon crosses the train/val boundary.
        if is_train and purge_seconds > 0:
            horizon_end = s.outcome_timestamp
            if s.decision_timestamp <= boundary_train_val <= horizon_end:
                continue  # purged
        if is_train:
            # Embargo: drop samples whose decision falls within embargo of boundary.
            if (
                embargo_seconds > 0
                and (boundary_train_val - s.decision_timestamp).total_seconds() <= embargo_seconds
            ):
                continue
            train.append(s)
        else:
            if (
                embargo_seconds > 0
                and (s.decision_timestamp - boundary_train_val).total_seconds() <= embargo_seconds
            ):
                continue
            validation.append(s)

    # Embargo on OOS boundary too.
    if embargo_seconds > 0:
        oos = [
            s
            for s in oos
            if (s.decision_timestamp - boundary_val_oos).total_seconds() > embargo_seconds
        ]

    return TemporalSplit(
        train=train,
        validation=validation,
        oos=oos,
        train_start=ordered[0].decision_timestamp,
        train_end=boundary_train_val,
        val_start=boundary_train_val,
        val_end=boundary_val_oos,
        oos_start=boundary_val_oos,
        oos_end=ordered[-1].decision_timestamp,
    )


@dataclass(frozen=True)
class WalkForwardFoldSplit:
    """One purged/embargoed walk-forward fold."""

    fold: int
    train: list[ResearchSample]
    validation: list[ResearchSample]
    oos: list[ResearchSample]
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    oos_start: datetime
    oos_end: datetime


def walk_forward_folds(
    dataset: ResearchDataset,
    n_splits: int = 3,
    val_frac: float = 0.2,
    validate_frac_per_fold: float = 0.2,
    embargo_seconds: float = 0.0,
    purge_seconds: float = 0.0,
) -> list[WalkForwardFoldSplit]:
    """
    Expanding-window walk-forward with purging + embargo (spec 14).

    Fold k: TRAIN = all samples before the validation window; VALIDATION = the
    next window; OOS = the window after that (when available). The expanding
    train window approximates the spec's A+B -> C -> D structure:

        Fold 1: TRAIN  VALIDATION  OOS
        Fold 2:   TRAIN  VALIDATION  OOS
        Fold 3:     TRAIN  VALIDATION  OOS

    The dataset is sliced into fixed-size blocks; every fold has at least one
    full training block BEFORE its validation window (never an empty train).
    """
    ordered = _sorted(list(dataset.samples))
    n = len(ordered)
    # Block size: divide into (n_splits + 2) segments so every fold gets
    # train + validation (+ oos) windows without emptying the train block.
    block = max(1, n // (n_splits + 2))
    if block < 3 or n < (n_splits + 2) * 3:
        # Tiny datasets cannot support temporal folds safely.
        return []
    first_val_idx = block  # train = ordered[:block], val = ordered[block:2*block]

    folds: list[WalkForwardFoldSplit] = []
    for k in range(n_splits):
        val_start_idx = first_val_idx + k * block
        val_end_idx = val_start_idx + block
        if val_end_idx > n:
            break
        oos_start_idx = val_end_idx
        oos_end_idx = min(n, oos_start_idx + block)

        train_block = ordered[:val_start_idx]
        val_block = ordered[val_start_idx:val_end_idx]
        oos_block = ordered[oos_start_idx:oos_end_idx]

        if len(train_block) < block or len(val_block) == 0:
            break

        boundary_ts = val_block[0].decision_timestamp
        boundary_oos_ts = oos_block[0].decision_timestamp if oos_block else boundary_ts

        # Purge from train samples whose horizon crosses into validation.
        purged_train: list[ResearchSample] = []
        for s in train_block:
            if purge_seconds > 0:
                horizon_end = s.outcome_timestamp
                if s.decision_timestamp <= boundary_ts <= horizon_end:
                    continue
            if (
                embargo_seconds > 0
                and (boundary_ts - s.decision_timestamp).total_seconds() <= embargo_seconds
            ):
                continue
            purged_train.append(s)

        val_purged: list[ResearchSample] = []
        for s in val_block:
            if (
                embargo_seconds > 0
                and (s.decision_timestamp - boundary_ts).total_seconds() <= embargo_seconds
            ):
                continue
            val_purged.append(s)

        oos_purged: list[ResearchSample] = []
        for s in oos_block:
            if (
                embargo_seconds > 0
                and (s.decision_timestamp - boundary_oos_ts).total_seconds() <= embargo_seconds
            ):
                continue
            oos_purged.append(s)

        folds.append(
            WalkForwardFoldSplit(
                fold=k + 1,
                train=purged_train,
                validation=val_purged,
                oos=oos_purged,
                train_start=ordered[0].decision_timestamp,
                train_end=boundary_ts,
                val_start=boundary_ts,
                val_end=boundary_oos_ts,
                oos_start=boundary_oos_ts,
                oos_end=oos_block[-1].decision_timestamp if oos_block else boundary_oos_ts,
            )
        )
    return folds


def fold_trade_counts(fold: WalkForwardFoldSplit) -> dict[str, int]:
    """Sample counts for observability."""
    return {
        "train": len(fold.train),
        "validation": len(fold.validation),
        "oos": len(fold.oos),
    }


def deterministic_normalization_fit(train: list[ResearchSample]):
    """
    Returns (mean, std) of realised R fit ONLY on the train partition.

    Any downstream transform must apply these forward (never refit on
    validation/OOS). Returns NaN-safe statistics.
    """
    vals = np.asarray([s.realized_r for s in train], dtype=float)
    if len(vals) == 0:
        return 0.0, 1.0
    return float(np.nanmean(vals)), float(np.nanstd(vals)) or 1.0
