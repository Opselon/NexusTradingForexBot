# ML-FEAT-001 Handoff — 50D Normalization End-to-End Parity Verification

## Task
- **Task ID:** ML-FEAT-001
- **Stream:** Stream B (Features)
- **Priority:** P0
- **Agent:** AGENT-FEATURE
- **Date:** 2026-09-18
- **Branch:** `agent/feature/ML-FEAT-001`
- **Base:** `origin/main` (`0185af22`)

---

## Scope
Exact files modified/added:
- `tests/unit/test_50d_normalization_parity.py` (New test suite, 6 deterministic parity & contract tests)
- `tests/critical_suite.txt` (Appended `tests/unit/test_50d_normalization_parity.py`)
- `docs/agent_handoffs/2026-09-18_AGENT-FEATURE_ML-FEAT-001.md` (This handoff report)
- `agents/locks.yaml` (Acquired lock for `tests/unit/test_50d_normalization_parity.py`)

No files under `src/nexus_scalp/` were touched (`git diff -- src/nexus_scalp/` is clean).

---

## Source Evidence

### 1. Trainer Implementation
- **Source:** `src/nexus_scalp/training/walk_forward_trainer.py:1863-1882`
- **`_fit_scaler(X_raw)`**:
  - `mean = np.mean(X_raw, axis=0, keepdims=True).astype(np.float32)`
  - `std = np.std(X_raw, axis=0, keepdims=True).astype(np.float32)` (ddof=0)
  - `std = np.maximum(std, 1e-3)` (production clamping floor at 1e-3, satisfying `std >= 1e-8`)
  - Returns `ScalerBundle(mean=mean, std=std)`.
- **`_transform_features(X_raw, scaler)`**:
  - `X = (X_raw - scaler.mean) / scaler.std`
  - `X = np.clip(X, self.clip_features_min, self.clip_features_max)` where min=-5.0, max=5.0
  - Returns `X.astype(np.float32)`.
- **Finiteness guard (`_assert_features_finite`)**:
  - `if not np.all(np.isfinite(X_raw)): raise ValueError(...)`

### 2. Live Inference & Scaler Implementation
- **Source:** `src/nexus_scalp/application/live_engine.py:157-205` (`ScalerBundle`)
  - `is_ready()`: Checks `mean is not None`, `std is not None`, `all(isfinite(std))`, `all(std > 0.0)`.
  - `transform(x)`: If not ready, returns `x` passthrough; otherwise `np.clip((x - self.mean) / self.std, -5.0, 5.0)`.
- **Source:** `src/nexus_scalp/application/live/model_bundle_store.py:447-495` (`_load_scaler_artifacts`)
  - Loads `.scaler.npz` containing arrays `mean` and `std`.
  - Verifies shape against declared feature dimension.
  - If std is degenerate (`std <= 0` or non-finite), logs `[SCALER_DEGRADED]` and marks `is_ready=False`.
- **Source:** `src/nexus_scalp/application/live/inference.py:52-88` (`InferenceService`)
  - `validate_feature_vector(features, context)`: delegates to `LiveEngine._validate_50d_tensor` when dimension is 50.
  - `LiveEngine._validate_50d_tensor`: raises `RuntimeError` if length != 50; sanitizes non-numeric and non-finite cells to 0.0 with warning and clips to `[-3.0, 3.0]`.

### 3. Feature Schema Registry (SSOT)
- **Source:** `src/nexus_scalp/features/schema.py:12-78`
  - `ACTIVE_SCHEMA_ID = "scalp_v1"`
  - `active_dimension() = 50`
  - `validate_vector(values)`: raises `ValueError` if `len(values) != 50`.

---

## Numerical Parity (Scenario #1)

- **Matrix shape:** `(10000, 50)`
- **Dtype:** `float32`
- **Seed:** `42`
- **Distribution mixture:**
  - Cols 0..19: Gaussian distributed (`normal(loc=0.0, scale=1.5)`)
  - Cols 20..34: Uniform distributed (`uniform(low=-10.0, high=10.0)`)
  - Cols 35..49: Exponential heavy-tailed (`exponential(scale=2.5)`)
- **Measured max_abs_error:** `0.0`
- **Tolerance:** `atol=1e-7`, `rtol=1e-7`
- **Result:** `PASS`
- **Explicit Invariant Confirmation:**
  $$\max(|X_{\text{batch}} - X_{\text{live}}|) = 0.0 \le 10^{-7}$$
  Bitwise exact match across all 10,000 observations and streaming single rows.

---

## Zero Variance (Scenario #2)
- **Synthetic setup:** 1,000 rows x 50 columns. Column index 5 = 2.5, column index 20 = 0.0 for every row.
- **Fitted std at index 5:** `0.001` (clamped by `maximum(std, 1e-3)`)
- **Fitted std at index 20:** `0.001` (clamped by `maximum(std, 1e-3)`)
- **Division-by-zero:** None
- **NaN / Inf:** None (`np.isfinite` is True across all outputs)
- **Constant columns output:** Exactly `0.0` for all rows
- **Parity max_abs_error:** `0.0 <= 1e-7`
- **Result:** `PASS`

---

## Extreme Values / Flash-Crash Inputs (Scenario #3)
- **Synthetic setup:** Standard fitted scaler tested against shock inputs including $+10^6$ and $-10^6$ across single cells, entire rows, and alternating half-rows.
- **Overflow:** None
- **NaN / Inf:** None (`np.all(np.isfinite)` is True)
- **Clipping bounds:** Outputs strictly bounded in `[-5.0, 5.0]` (both offline trainer and live `ScalerBundle`)
- **Parity max_abs_error:** `0.0 <= 1e-7`
- **Result:** `PASS`

---

## Fail-Loud Validation (Scenario #4)
- **49 dimensions:**
  - Schema contract (`FeatureSchema.validate_vector`): `ValueError("Feature contract violation in test_49d: schema=scalp_v1 expected 50 features, got 49")`
  - Serving contract (`InferenceService.validate_feature_vector`): `RuntimeError("Feature contract violation in test_49d: schema=scalp_v1 expected 50, got 49")`
- **51 dimensions:**
  - Schema contract (`FeatureSchema.validate_vector`): `ValueError("Feature contract violation in test_51d: schema=scalp_v1 expected 50 features, got 51")`
  - Serving contract (`InferenceService.validate_feature_vector`): `RuntimeError("Feature contract violation in test_51d: schema=scalp_v1 expected 50, got 51")`
- **Non-finite values (NaN, +Inf, -Inf, None):**
  - Offline training (`WalkForwardTrainer._assert_features_finite`): Raises `ValueError("Non-finite feature cell in training frame...")` fail-closed.
  - Live serving (`InferenceService._validate_50d_tensor`): Sanitizes non-finite / non-numeric cells to 0.0 with warning and clip[-3.0, 3.0] per documented GAP L11-5 availability contract.
  - Scaler integrity (`ScalerBundle.is_ready`): Rejects degenerate std (`<= 0` or non-finite) with `is_ready=False`, safely passing features through unchanged to avoid zero-division NaN poisoning.
- **Result:** `PASS`

---

## Quality Gates

| Gate | Command | Status | Output / Notes |
|:-----|:--------|:-------|:---------------|
| Gate 1 — Ruff Lint | `ruff check tests/unit/test_50d_normalization_parity.py` | PASS | `All checks passed!` |
| Gate 2 — Ruff Format | `ruff format --check tests/unit/test_50d_normalization_parity.py` | PASS | `1 file already formatted` |
| Gate 3 — Mypy | `mypy tests/unit/test_50d_normalization_parity.py` | PASS | `Success: no issues found in 1 source file` |
| Gate 4 — Dedicated Pytest | `pytest tests/unit/test_50d_normalization_parity.py -vv` | PASS | `6 passed in 2.25s` |
| Gate 5 — Canonical Pre-Push Gate | `bash beforePush.sh` | PASS | Whole-tree ruff, whole-src mypy, critical manifest (198 files), decision IDs, fast tests battery (34.48s) |

### Pytest Execution Transcript
```text
tests/unit/test_50d_normalization_parity.py::test_scenario1_end_to_end_50d_normalization_parity PASSED [ 16%]
tests/unit/test_50d_normalization_parity.py::test_scenario2_zero_variance_std_clamping PASSED [ 33%]
tests/unit/test_50d_normalization_parity.py::test_scenario3_extreme_values_stability PASSED [ 50%]
tests/unit/test_50d_normalization_parity.py::test_scenario4_dimension_mismatch_49d_rejected PASSED [ 66%]
tests/unit/test_50d_normalization_parity.py::test_scenario4_dimension_mismatch_51d_rejected PASSED [ 83%]
tests/unit/test_50d_normalization_parity.py::test_scenario4_nonfinite_inputs_handled PASSED [100%]
============================== 6 passed in 2.25s ===============================
```

---

## Git & Pull Request Tracking

- **Commit SHA:** `708cb98aecd0dc766fc13f35f05ef1e93f17e40f`
- **PR Number:** #254
- **PR URL:** https://github.com/Opselon/NexusTradingForexBot/pull/254
- **Merge Commit SHA:** `bd61deb57be7f13e8b3c4ac2760350b60fe26507`
- **Status:** MERGED & VERIFIED ON MAIN (6/6 tests passing on main tip)
