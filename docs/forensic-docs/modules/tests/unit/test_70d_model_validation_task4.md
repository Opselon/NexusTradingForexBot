# tests/unit/test_70d_model_validation_task4.py

- GUARDS: TEST-70D-MODEL-01..25 — TASK-04 70D fair-benchmark contract, executable TODAY on the existing 50D/60D artifact pair (proves the METHOD), with 70D-specific assertions activated via parametrization when a 70D artifact exists.
- KEY ASSERTIONS:
  - fairness (same dataset, split, seed), safety (champion untouched, challenger no MT5 access), geometry gates (dimension/schema/scaler), validation gates (class collapse, calibration, OOS rejection), deterministic seeds (103 asserts).
- PITFALLS IT ENCODES: a fair benchmark compares models on ONE dataset config with identical splits; the champion-pair requirement must not be skipped when artifacts are missing (skips vs fails are explicit).
- NOTES: Largest docstring in the suite (25 test-requirement mappings); parametrized 70D activation.
