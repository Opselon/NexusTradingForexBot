# tests/integration/test_model_lifecycle_api.py

- GUARDS: PHASE 10 — Model Lifecycle API, worker & LiveEngine wiring: controlled training / challenger engine over REST; governance endpoints; safety boundaries; 70D governance extension.
- KEY ASSERTIONS:
  - `TestModelLifecycleAPI` (train/status/promote endpoints against real worker); `TestGovernanceAPI` (approval, promotion audit, rollback); `TestModelLifecycleSafety` (challenger cannot execute, champion untouched); `TestGovernance70API` (70D gates); `TestModelsIntegrityRegression` (artifact hash/schema integrity, BUG-118 verify-once logging) (88 asserts).
- PITFALLS IT ENCODES: promotion requires explicit approval; failed challenger must never overwrite champion; API exposes persisted registry truth only (no synthetic state). BUG-118: champion verification logs go to CAPSYS, not caplog.
- NOTES: Largest integration suite; pairs with unit test_model_lifecycle_phase10.py / test_model_governance_phase16.py.
