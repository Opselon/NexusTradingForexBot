# src/nexus_scalp/research/splitting.py

- PURPOSE: PHASE 09B temporal (never random) splitting with purge & embargo
  (spec 6/8/38): TRAIN | VALIDATION | OOS plus expanding-window walk-forward
  folds, all driven by dataset-derived boundaries (never hardcoded dates).
- ARCHITECTURE LAYER: Research (pure functions over ResearchDataset; no I/O,
  no order authority).
- RESPONSIBILITY: provide deterministic, causality-preserving partitions so
  downstream engines (backtest use_split, OOS gate, walk-forward, leakage
  guards) measure on uncontaminated windows.
- DEPENDENCIES: `research.models` (ResearchDataset/ResearchSample), numpy.
- CONNECTS TO: backtest.BacktestEngine.run (use_split), oos.OOSGate.evaluate,
  walkforward.WalkForwardEngine.validate, leakage guards; dataset.build_
  for_strategy(as_of) supplies the causal inputs.

- KEY CONCEPTS:
  - Defaults: DEFAULT_VALIDATION_FRAC=0.2, DEFAULT_OOS_FRAC=0.2 (lines 29-30).
  - `TemporalSplit` dataclass (lines 33-45): explicit boundary timestamps so
    consumers can see exactly which window a sample belongs to.
  - `_index_split_boundaries` (lines 52-60): index math — OOS gets
    round(n*oos_frac) samples (>=1 when frac>0), validation round of the
    remainder, train = the rest. Deterministic from fractions.
  - `split_temporal` (lines 63-148): sorts by decision_timestamp; builds
    train/validation/oos. Purge (lines 110-113): a TRAIN sample whose outcome
    horizon crosses the train/val boundary
    (`decision <= boundary <= outcome`) is REMOVED from train — its label
    leaks future info across the boundary. Embargo (lines 116-128, 131-136):
    samples whose DECISION falls within `embargo_seconds` of the boundary are
    dropped from BOTH sides of train/val AND from OOS (boundary_val_oos).
    Empty dataset returns a degenerate split with datetime.min boundaries.
  - `WalkForwardFoldSplit` (lines 151-164) + `walk_forward_folds`
    (lines 167-264): the dataset is sliced into fixed-size blocks:
    block = n // (n_splits + 2); fold k uses train = ordered[:val_start_idx]
    (EXPANDING window), validation = next block, oos = the block after.
    Refuses tiny datasets (block < 3 or n < (n_splits+2)*3 → []). Purge is
    applied against each fold's validation boundary; embargo applied to
    train (trailing side), validation (leading side) and oos (leading side).
    Folds are 1-indexed; a fold is only emitted when a full train block
    precedes it (never an empty train).
  - `fold_trade_counts` (lines 267-273): observability helper.
  - `deterministic_normalization_fit` (lines 276-286): mean/std of realized R
    fit ONLY on the train partition, NaN-safe (np.nanmean/np.nanstd, std
    fallback 1.0); the docstring is explicit that any downstream transform
    must apply these forward (never refit on val/OOS) — this is the
    normalization-fit boundary the leakage guards enforce.

- HOT PATH / PERFORMANCE: O(n log n) per call; folds are pure list slices —
  fine for worker-cycle use, never the tick path.

- EDGE CASES & PITFALLS:
  - `_index_split_boundaries` uses max(1, round(...)) — for tiny datasets the
    val/OOS windows can exceed what the fractions imply, and for very small n
    train can be 0 samples (no guard here; walk_forward_folds has its own
    minimum-size refusal).
  - Purge semantics differ subtly between split_temporal (purges only TRAIN,
    with `decision <= boundary <= horizon_end`) and walk_forward_folds (same
    test per fold). Neither purges samples from the OOS block whose horizon
    crosses the OOS end — the final window's labels are accepted as-is.
  - The walk-forward structure mirrors spec 14's A+B → C → D layout as
    expanding blocks, but it is NOT a true refit walk-forward: normalization
    fit is external (see leakage.py); the engine only re-runs compute_backtest
    per window.
  - Embargo is applied as `<= embargo_seconds` (inclusive); an embargo of 0.0
    disables the check entirely (strict `> 0` guards every branch).
  - `deterministic_normalization_fit` returns (0.0, 1.0) for an empty train —
    downstream standardization is then identity; callers must gate on sample
    counts themselves (scoring sample-confidence does).