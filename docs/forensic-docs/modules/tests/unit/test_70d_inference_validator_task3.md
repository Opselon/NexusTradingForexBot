# tests/unit/test_70d_inference_validator_task3.py

- GUARDS: TASK-03-70D-PARITY — inference validator + model compatibility (TEST-70D-PARITY-11..13, 21..24 + scaler 09/10): 60D model accepts 60D only; 70D model accepts 70D only; 60D/70D mismatch blocked before inference.
- KEY ASSERTIONS:
  - dimension gate (60D vs 70D) enforces exact match; scaler dims/means matched to model schema; mismatched model artifact rejected with explicit error; valid pairs infer correctly (39 asserts).
- PITFALLS IT ENCODES: the validator is the ONLY door to inference — tests pin that wrong-dimension/scaler/schema inputs are rejected, never coerced.
- NOTES: Companion of test_70d_contract_parity_task3.py (schema side) at the artifact side.
