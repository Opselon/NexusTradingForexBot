# ML-FEAT-001 — 50D Feature Normalization End-to-End Parity Verification

STREAM: STREAM B — FEATURE ENGINEERING
PRIORITY: P0
STATUS: DONE (PR #254, merge bd61deb5)
DEPENDENCIES: None
AGENT_ROLE: AGENT-FEATURE
OWNERSHIP_SCOPE: tests/unit/test_50d_normalization_parity.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify and mathematically prove numerical parity for 50D feature normalization between offline training (WalkForwardTrainer) and real-time live inference (InferenceService).

## WHY_IT_EXISTS
Discrepancies in mean, variance clamping, or epsilon handling between the training scaler and live serving create out-of-distribution inputs, corrupting model confidence in live trading without throwing runtime exceptions.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1816-1830`)
  - **Symbol:** `WalkForwardTrainer._fit_scaler`
  - **Behavior:** Fits mean and std on training fold; applies np.where(std < 1e-8, 1.0, std); serializes to .scaler.npz
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12-18`)
  - **Symbol:** `ACTIVE_SCHEMA_ID, FEATURE_DIMENSION`
  - **Behavior:** Hardcodes ACTIVE_SCHEMA_ID = 'scalp_v1', FEATURE_DIMENSION = 50
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `52-110`)
  - **Symbol:** `InferenceService.validate_feature_vector`
  - **Behavior:** Validates length == 50; applies (x - mean) / std before forward pass
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Training fits mean and std with std < 1e-8 clamped to 1.0.
- Live inference applies (x - mean) / std.
- Active dimension is 50.

## UNKNOWNs
- Whether any feature has zero variance across specific market regimes (e.g. session flags during weekend ticks).

## SCOPE
Write comprehensive unit test asserting bitwise numerical parity (atol <= 1e-7) between offline scaler and live InferenceService scaler across Gaussian, uniform, constant, and extreme distributions.

## NON_GOALS
Do not change the mathematical formula of feature calculations or normalizations.

## SOURCE_AREAS
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `src/nexus_scalp/application/live/inference.py`
- `src/nexus_scalp/features/schema.py`

## FILES_LIKELY_TO_CHANGE
- `tests/unit/test_50d_normalization_parity.py`

## INVESTIGATION_PLAN
Inspect whether ddof=0 or ddof=1 is used for std calculation across training and live code.

## IMPLEMENTATION_PLAN
1. Create tests/unit/test_50d_normalization_parity.py.
2. Generate synthetic (10,000, 50) float32 array with varied distributions.
3. Fit scaler via WalkForwardTrainer._fit_scaler and serialize to temporary .scaler.npz.
4. Transform via WalkForwardTrainer._transform_features.
5. Initialize InferenceService with saved scaler and transform identical single rows.
6. Assert np.allclose(offline, live, atol=1e-7, rtol=1e-7).
7. Assert ValueError on length != 50 or non-finite values.

## TEST_PLAN
- `pytest tests/unit/test_50d_normalization_parity.py -v`

## BENCHMARK_PLAN
Transform 10,000 vectors; assert max absolute error <= 1e-7.

## EVIDENCE_REQUIRED
- Test file tests/unit/test_50d_normalization_parity.py
- Pytest output showing 0 failures and max delta < 1e-7

## ACCEPTANCE_CRITERIA
1. tests/unit/test_50d_normalization_parity.py passes cleanly.
2. Max absolute error between offline and live transforms <= 1e-7.
3. Input vectors with length != 50 or NaNs fail loud with validation errors.

## ABORT_CONDITIONS
If WalkForwardTrainer and InferenceService use fundamentally conflicting normalization logic, STOP and document the defect.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_50d_normalization_parity.py`

## SHARED_FILE_RISK
Zero. Test file only.
