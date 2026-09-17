# TASK-005 — Purge & Embargo Boundary Audit & Verification Tests

## Priority
P1

## Status
PENDING

## Objective
Verify that purged walk-forward splits strictly eliminate temporal overlap and feature lookahead between folds.

## Why This Task Exists
Temporal leakage in financial ML models produces unrealistically high in-sample and validation metrics that fail in live markets.

## Current Evidence
src/nexus_scalp/training/walk_forward_trainer.py:1853-1937 (_split_fold_with_embargo).

## Scope
Assert that indices in train_idx, purge_gap, test_idx, and embargo_bars are disjoint and satisfy temporal monotonicity.

## Explicit Non-Goals
Do not change the 15-bar purge/embargo duration constants.

## Dependencies
TASK-002

## Source Areas
src/nexus_scalp/training/walk_forward_trainer.py, tests/unit/test_purge_embargo_semantics.py

## Files Likely Involved
src/nexus_scalp/training/walk_forward_trainer.py, tests/unit/test_purge_embargo_semantics.py

## Investigation Required
Examine boundary condition when fold indices approach array boundaries.

## Implementation Outline
Implement comprehensive unit tests checking fold boundary timestamps and row index offsets.

## Tests Required
tests/unit/test_purge_embargo_semantics.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Zero overlapping bar timestamps between any fold training set and its validation set.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.
