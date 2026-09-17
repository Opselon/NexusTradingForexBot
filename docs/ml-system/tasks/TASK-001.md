# TASK-001 — 50D Feature Normalization End-to-End Parity Verification

Priority: P0
Status: READY
Type: Verification & Contract Test
Dependencies: None
Blocks: TASK-004, TASK-010
Human Decision Required: NO
Risk: Low (Test & Verification only, zero runtime modification)
Estimated Scope: 1 new test file (~150 LOC), zero source modifications

## Objective
Verify and mathematically prove end-to-end numerical parity for 50D feature normalization between offline training (WalkForwardTrainer) and real-time live inference (InferenceService).

## Problem / Why
Discrepancies in feature mean, variance clamping, or epsilon handling between the training scaler and the live inference scaler create out-of-distribution feature vectors, corrupting model confidence and signal calibration in live trading without throwing runtime exceptions.

## Current Evidence
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1816-1830`)
  - **Symbol:** `WalkForwardTrainer._fit_scaler, _transform_features`
  - **Behavior:** Fits mean and sample std on training fold; applies np.where(std < 1e-8, 1.0, std); serializes arrays into .scaler.npz
  - **Classification:** `PRODUCTION-COMPATIBLE TRAINING PATH`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12-18`)
  - **Symbol:** `ACTIVE_SCHEMA_ID, FEATURE_DIMENSION`
  - **Behavior:** Hardcodes ACTIVE_SCHEMA_ID = 'scalp_v1', FEATURE_DIMENSION = 50
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `52-110`)
  - **Symbol:** `InferenceService.validate_feature_vector, _load_scaler`
  - **Behavior:** Validates feature vector length == 50; applies (x - mean) / std before forward pass
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Write a dedicated unit test suite tests/unit/test_50d_normalization_parity.py asserting that a synthetic (N, 50) matrix transformed via WalkForwardTrainer._transform_features is bitwise identical (within float32 epsilon <= 1e-7) to the output produced by InferenceService._transform_vector.
2. Verify edge case handling for zero-variance features (std < 1e-8 -> std = 1.0), infinite values, and NaN inputs.
3. Validate serialization round-trip: save to model.scaler.npz and deserialize via InferenceService.

## Non-Goals
Do not change feature definitions, index assignments, or the normalization mathematical formula.

## Preconditions
Repository clean at HEAD; Python test environment with numpy and torch available.

## Dependencies
None

## Blocks
TASK-004 (Model Promotion Gate Audit), TASK-010 (50D vs 70D Feasibility)

## Source Areas
- `src/nexus_scalp/training/walk_forward_trainer.py:1816-1835`
- `src/nexus_scalp/features/schema.py:1-25`
- `src/nexus_scalp/application/live/inference.py:52-115`

## Investigation
Inspect WalkForwardTrainer._fit_scaler and InferenceService._load_scaler to confirm whether ddof=0 or ddof=1 is used for std computation, and verify that np.where(std < 1e-8, 1.0, std) is applied identically in both training and live serving.

## Implementation Plan
1. Create tests/unit/test_50d_normalization_parity.py.
2. Generate synthetic (1000, 50) float32 feature frame with varied distributions (Gaussian, uniform, constant columns, extreme values).
3. Fit scaler using WalkForwardTrainer._fit_scaler and serialize to temporary .scaler.npz.
4. Transform features via WalkForwardTrainer._transform_features.
5. Initialize InferenceService with saved scaler and transform identical single-row vectors.
6. Assert np.allclose(offline_transformed, live_transformed, atol=1e-7, rtol=1e-7).
7. Assert ValueError is raised when vector dimension != 50 or contains non-finite values.

## Tests
- `pytest tests/unit/test_50d_normalization_parity.py -v`
- `pytest tests/unit/test_inference_service.py -v`

## Validation / Benchmark
Evaluate numerical absolute difference max(|offline - live|) over 10,000 synthetic rows across all 50 feature indices. Baseline tolerance: atol=1e-7. Artifact: test run log.

## Evidence Required
- File created: tests/unit/test_50d_normalization_parity.py
- Command output: pytest tests/unit/test_50d_normalization_parity.py passing with 0 failures
- Numerical parity assertion log showing maximum delta < 1e-7

## Acceptance Criteria
1. tests/unit/test_50d_normalization_parity.py exists and passes.
2. Offline and live normalization outputs match with max absolute error <= 1e-7 across all 50 dimensions.
3. Input vectors with length != 50 or containing NaNs/Infs fail loud with explicit validation errors.

## Failure / Abort Conditions
If WalkForwardTrainer and InferenceService use fundamentally conflicting normalization formulas (e.g. MinMax vs StandardScore), STOP immediately and document the contradiction.

## Human Stop Conditions
None (No architectural decision required; pure verification and test task).

## Expected Output
PR containing tests/unit/test_50d_normalization_parity.py with all parity tests passing.
