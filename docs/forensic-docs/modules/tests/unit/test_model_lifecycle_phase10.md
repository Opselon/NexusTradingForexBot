# tests/unit/test_model_lifecycle_phase10.py

- GUARDS: PHASE 10 Controlled Model Training & Challenger engine — behavioral suite: training dataset reproducibility/provenance/temporal ordering/no future leakage; compatibility gates (50D/70D/schema/dimension/scaler/hash); AI-Hub artifact contract; gates (OOS/robustness/drawdown/collapse/calibration); champion/challenger separation; worker isolation; regressions (phase08/09/accounting/production inference intact).
- KEY ASSERTIONS:
  - `test_aihub_01_valid_50d_4class_passes`; `test_aihub_05_class_count_mismatch_rejected`; `test_bug118_champion_verified_logs_once_per_fingerprint` (verify-once memoization, cold start none-memoized, force reload fresh verify); `test_challenger_cannot_execute_production_orders`; `test_champion_unchanged_during_training`; `test_rejected_challenger_cannot_become_champion`; `test_promotion_lineage_immutable` (95 asserts).
- PITFALLS IT ENCODES: BUG-118 — champion verification logs go to CAPSYS not caplog, and must fire ONCE per fingerprint (verify-once is a contract, not an optimization).
- NOTES: Regression block proves phase08/09/accounting paths stay intact — cross-subsystem coupling is intentional here.
