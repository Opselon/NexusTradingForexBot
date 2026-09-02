# tests/unit/test_hardened_protocol.py

- GUARDS: Hardened safety protocol: model rollback on health-check failure, NaN feature validation + fallback, risk-engine cascading safety clamps, execution throttling of pending modifications, authoritative regime guardian, safety state machine.
- KEY ASSERTIONS:
  - `test_model_rollback_on_health_check_failure`; `test_feature_pipeline_nan_validation_and_fallback`; `test_risk_engine_cascading_safety_clamps`; `test_execution_throttling_pending_modifications`; `test_authoritative_regime_guardian_blocks_execution`; `test_safety_state_machine_transitions` (16 asserts).
- PITFALLS IT ENCODES: NaN/inf features must fall back, never reach the model; regime guardian is authoritative (blocks execution, not just warns); throttling prevents modification storms.
- NOTES: Smoke-style 6-test suite across the whole stack (models, features, risk, execution, signals, training imports).
