# ML-VAL-001 — Purged Walk-Forward Fold Monotonicity & Embargo Boundary Audit

STREAM: STREAM G — VALIDATION/OOS
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-DATA-001
AGENT_ROLE: AGENT-ML-VALIDATION
OWNERSHIP_SCOPE: src/nexus_scalp/training/walk_forward_trainer.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify that WalkForwardTrainer._split_fold_with_embargo strictly enforces 15-bar purge gaps and 15-bar embargo windows across all 34 folds, proving zero temporal data leakage.

## WHY_IT_EXISTS
Temporal leakage in financial ML models produces unrealistically high backtest metrics that collapse in live execution. Purge and embargo boundaries must be mathematically verified to guarantee no sample overlap.

## CURRENT_EVIDENCE
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

## FACTS
- 34 expanding walk-forward folds generated.
- Purge gap = 15 bars, embargo = 15 bars.

## UNKNOWNs
- Boundary index behavior when fold splits cross weekend market closures.

## SCOPE
Write tests/unit/test_purge_embargo_monotonicity.py checking index sets and timestamps across all 34 folds; assert set(train).isdisjoint(test) and temporal gap >= 15 bars.

## NON_GOALS
Do not modify the 15-bar constant values.

## SOURCE_AREAS
- `src/nexus_scalp/training/walk_forward_trainer.py:1850-2010`

## FILES_LIKELY_TO_CHANGE
- `tests/unit/test_purge_embargo_monotonicity.py`

## INVESTIGATION_PLAN
Verify whether purge gap removes bars by array index row count or by timestamp difference.

## IMPLEMENTATION_PLAN
1. Create tests/unit/test_purge_embargo_monotonicity.py.
2. Generate synthetic time series of 50,000 bars.
3. Generate all 34 folds via _generate_expanding_folds.
4. For every fold: assert train and test index sets are strictly disjoint.
5. Assert: min(test_idx) - max(train_idx) > 15.
6. Assert: max(train_timestamp) < min(test_timestamp).

## TEST_PLAN
- `pytest tests/unit/test_purge_embargo_monotonicity.py -v`

## BENCHMARK_PLAN
Audit 34 folds; zero index collisions across all folds.

## EVIDENCE_REQUIRED
- Test file: tests/unit/test_purge_embargo_monotonicity.py
- Pytest output verifying disjointness across all 34 folds

## ACCEPTANCE_CRITERIA
1. tests/unit/test_purge_embargo_monotonicity.py passes with 0 failures.
2. Mathematically proven zero index overlap between training and validation across all folds.

## ABORT_CONDITIONS
If any fold has overlapping train/test indices, flag critical data leakage bug and STOP.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_purge_embargo_monotonicity.py`

## SHARED_FILE_RISK
Low. Test file only.
