# src/nexus_scalp/research/leakage.py

- PURPOSE: PHASE 09B hard invariants against leakage / lookahead
  (spec 7/8/27): centralizes the guards so every consumer uses ONE
  implementation.
- ARCHITECTURE LAYER: Research (pure; no I/O, no order authority).
- RESPONSIBILITY: (1) assert no sample's DECISION is at/after a cutoff,
  (2) provide the fit-on-train-only forward standardization parameters,
  (3) sentinel-guard against iterative OOS contamination (spec 27),
  (4) expose a guard that backtest expectancies use train-only
  normalization.
- DEPENDENCIES: `research.models` (BacktestResult, ResearchSample), numpy.
- CONNECTS TO: dataset.build_for_strategy(as_of) (the practical leak guard),
  splitting.split_temporal/walk_forward_folds (purge/embargo),
  deterministic_normalization_fit (sibling fit helper); consumers of the
  research pipeline can assert these guards at run boundaries.

- KEY CONCEPTS:
  - `assert_no_future_decisions(samples, as_of, label)` (lines 24-39): the
    hard invariant — raises ValueError naming the offending sample_id if any
    `decision_timestamp >= as_of`. This is the enforcement counterpart to
    dataset's build-time filter: a build with as_of is safe by construction,
    and this function catches callers who pass un-filtered lists.
  - `ForwardStats` (lines 42-53): mean/std learned ONLY from a train
    partition; `apply(value)` standardizes with the FIT parameters and never
    refits; std <= 0 → returns 0.0 (degenerate constant series).
  - `fit_forward_stats(train)` (lines 56-65): NaN-safe mean/std of realized
    R; empty train → (0.0, 1.0); non-finite/zero std → std=1.0. Mirrors
    splitting.deterministic_normalization_fit (duplicated helper exists in
    both modules — see pitfall).
  - `validate_no_train_leakage(oos_result_used_in_train=False)`
    (lines 68-81): sentinel — if a candidate is modified after seeing OOS
    results it is a NEW version requiring full revalidation; callers assert
    the flag stays False.
  - `backtest_properly_fit(result, stats, tolerance_ticks=0.05)`
    (lines 84-92): always returns True for raw-R backtests (the backtest
    engine never standardizes), kept as an explicit assertable guard so
    consumers can state the invariant in tests/CI; the tolerance argument is
    currently decorative.

- HOT PATH / PERFORMANCE: trivial loops over sample lists; worker-cycle only.

- EDGE CASES & PITFALLS:
  - The module's most defensive functions are no-ops today:
    `validate_no_train_leakage` only raises when a caller (none in this
    package) passes True, and `backtest_properly_fit` unconditionally returns
    True — they are contract documentation, not runtime enforcement.
  - `fit_forward_stats` duplicates `splitting.deterministic_normalization_fit`
    with identical semantics but independent implementation; a future change
    to one silently desynchronizes the other.
  - Nothing in this module verifies that purge/embargo were actually applied
    to a given split — those checks live in splitting.py, and there is no
    cross-assertion here linking a split's boundary to the samples used.