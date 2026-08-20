# tests/integration/test_experience_execution_boundary.py

- GUARDS: Phase 08 Experience Gate <-> Execution boundary — the Experience Intelligence layer participates in the REAL decision loop: harmful strategies are rejected BEFORE any order is attempted; healthy context reaches dispatch and the risk engine still governs.
- KEY ASSERTIONS:
  - `test_harmful_strategy_is_rejected_before_any_order_is_attempted`; `test_healthy_context_reaches_dispatch_and_risk_engine_still_governs`; `test_experience_tables_persist_across_repository_restart`; `test_all_experience_tables_and_indexes_exist`; `test_experience_rest_endpoints_expose_real_state` (34 asserts).
- PITFALLS IT ENCODES: gate ordering — experience rejection happens before dispatch; risk engine remains the backstop even for healthy strategies (defense in depth is the contract, not either/or).
- NOTES: Wired through LiveEngine with real worker; persistence across restart proves restart-safety of experience tables.
