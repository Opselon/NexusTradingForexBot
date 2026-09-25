# src/nexus_scalp/features/inference_validator.py

- **PURPOSE:** Pre-inference validation gate for the 70D live path — the
  `InferenceValidator` checks the scaler contract, schema hash, vector
  dimension/finiteness/bounds, and returns a typed `ValidationResult` with a
  `RejectionCode`. Model inputs are validated BEFORE entering the network so
  a bad tensor never reaches inference.
- **ARCHITECTURE LAYER:** Features (validation gate; runs on the live
  inference path).
- **RESPONSIBILITY:** (a) `ScalerContract` (expected scaler hash/version);
  (b) `validate(vector, expected_schema_hash, ...)` → PASS or REJECT with
  machine-readable `RejectionCode` (e.g. DIMENSION_MISMATCH, NON_FINITE,
  OUT_OF_BOUNDS, SCHEMA_HASH_MISMATCH, SCALER_MISMATCH); (c)
  `compatible_model_schema(model)` — quick width check helper for runtime
  decisions.
- **DEPENDENCIES:** `schema_contract` (hash/geometry/family), scaler
  metadata (hash comparison), `observability.logging`.
- **CONNECTS TO:** LiveEngine 70D inference (`_infer_probabilities` path for
  the 70D candidate models), shadow70, model runtime, tests
  (test_70d_inference_validator_task3, test_70d_model_validation_task4).
- **KEY CONCEPTS:** Fail-closed design: ANY check failure → REJECT with code —
  never a "best effort" pass. The validator is cheap (O(n) scan + hash
  compare) so running it per inference on the live path is affordable; the
  `context` parameter tags WHICH stage flagged the rejection for the Debug
  Console (`MODEL CONTRACT INVALID` / `70D CONTRACT BROKEN` provenance).
- **EDGE CASES & PITFALLS:** Validator must handle a `None` scaler (model
  loaded without scaler) → scaler contract rejection, not crash; hash
  mismatch is recorded as REJECT even when the model would technically
  accept the width — because a reordered feature contract would silently
  mis-infer.