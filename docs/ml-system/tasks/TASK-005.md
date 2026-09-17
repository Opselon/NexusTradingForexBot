# TASK-005 — Purge and Embargo Temporal Boundary Audit & Monotonicity Verification

Priority: P1
Status: EXECUTABLE AFTER TASK-002
Type: ML Integrity & Anti-Leakage
Dependencies: TASK-002
Blocks: TASK-013
Human Decision Required: NO
Risk: Medium (Subtle lookahead data leakage in walk-forward evaluation)
Estimated Scope: 1 dedicated verification test suite (~150 LOC)

## Objective
Verify that WalkForwardTrainer._split_fold_with_embargo strictly enforces 15-bar purge gaps and 15-bar embargo windows across all expanding folds, eliminating temporal data leakage.

## Problem / Why
In financial machine learning, overlap between training feature calculation windows and validation prediction windows causes severe information leakage, leading to inflated backtest metrics that fail catastrophically in live markets.

## Current Evidence
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1853-1937`)
  - **Symbol:** `WalkForwardTrainer._split_fold_with_embargo`
  - **Behavior:** Computes train_idx, purge_gap, test_idx, embargo_bars; applies purge_gap_bars=15 and embargo_bars=15
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1940-1990`)
  - **Symbol:** `WalkForwardTrainer._generate_expanding_folds`
  - **Behavior:** Generates 34 expanding walk-forward folds by default
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Write tests/unit/test_purge_embargo_monotonicity.py to inspect index sets and timestamps across all 34 folds.
2. Assert that: max(train_idx) + purge_gap_bars < min(test_idx).
3. Assert that for consecutive folds, no validation sample overlaps with subsequent training periods without proper embargo.
4. Validate behavior when fold boundaries coincide with weekends or market gaps.

## Non-Goals
Do not change the 15-bar constant values unless mathematical leakage is proven.

## Preconditions
TASK-002 complete (dataset generation available).

## Dependencies
TASK-002

## Blocks
TASK-013 (CI Training Validation Gap)

## Source Areas
- `src/nexus_scalp/training/walk_forward_trainer.py:1850-2010`

## Investigation
Inspect _split_fold_with_embargo to verify whether purge and embargo boundaries are computed by bar index count (row offset) or by absolute timestamp delta.

## Implementation Plan
1. Create tests/unit/test_purge_embargo_monotonicity.py.
2. Generate a synthetic dataset with monotonically increasing timestamps and known autocorrelation.
3. Instantiate WalkForwardTrainer and generate all fold splits via _generate_expanding_folds.
4. Loop through each fold and extract train_indices, test_indices, purge_indices, and embargo_indices.
5. Assert: set(train_indices).isdisjoint(test_indices).
6. Assert: min(test_indices) - max(train_indices) > 15.
7. Verify timestamp monotonicity across all folds.

## Tests
- `pytest tests/unit/test_purge_embargo_monotonicity.py -v`

## Validation / Benchmark
Zero index collisions between train and test across all 34 walk-forward folds. 100% of folds satisfy the 15-bar temporal boundary gap.

## Evidence Required
- Test file: tests/unit/test_purge_embargo_monotonicity.py
- Pytest output verifying index disjointness and boundary gap assertions across all 34 folds

## Acceptance Criteria
1. tests/unit/test_purge_embargo_monotonicity.py passes with 0 failures.
2. Mathematically proven zero index overlap between training and validation sets across all folds.

## Failure / Abort Conditions
If any fold exhibits index overlap or test timestamps prior to train timestamps, STOP immediately and flag temporal leakage bug.

## Human Stop Conditions
None.

## Expected Output
Comprehensive unit test verifying temporal integrity and anti-leakage guarantees of walk-forward folds.
