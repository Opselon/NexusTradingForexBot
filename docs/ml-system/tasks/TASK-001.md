# TASK-001 — 50D Normalization End-to-End Parity Verification

## Priority
P0

## Status
PENDING

## Objective
Verify and unit-test end-to-end numerical parity for 50D feature normalization between training (WalkForwardTrainer) and live serving (InferenceService).

## Why This Task Exists
Any discrepancy in feature mean or standard deviation between training folds and live inference causes severe out-of-distribution model predictions.

## Current Evidence
src/nexus_scalp/training/walk_forward_trainer.py:1816-1825 (_fit_scaler); src/nexus_scalp/application/live/inference.py:52-88 (validate_feature_vector).

## Scope
Add deterministic round-trip test asserting (X - mean)/std equality across synthetic frames, artifact persistence (.scaler.npz), and live InferenceService transform.

## Explicit Non-Goals
Do not change feature calculations or normalization math.

## Dependencies
None

## Source Areas
src/nexus_scalp/training/walk_forward_trainer.py, src/nexus_scalp/application/live/inference.py, tests/unit/test_50d_normalization_parity.py

## Files Likely Involved
src/nexus_scalp/training/walk_forward_trainer.py, src/nexus_scalp/application/live/inference.py, tests/unit/test_50d_normalization_parity.py

## Investigation Required
Confirm constant column handling (std < 1e-8 -> 1.0) and non-finite value rejection across all 50 features.

## Implementation Outline
1. Write tests/unit/test_50d_normalization_parity.py; 2. Assert fit -> save -> load -> transform cycle produces bitwise identical tensors.

## Tests Required
tests/unit/test_50d_normalization_parity.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Unit test passes cleanly, asserting zero drift between offline and live scaler transforms.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.
