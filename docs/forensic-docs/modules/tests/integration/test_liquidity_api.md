# tests/integration/test_liquidity_api.py

- GUARDS: TASK-02-70D-INTEGRATION — Liquidity API (REST + SSE), TEST-70D-13/14/16/17 + runtime smoke over HTTP: /api/liquidity/state (real backend status + ten values), /api/liquidity/features, toggling, live-state embedding, reconnect snapshot.
- KEY ASSERTIONS:
  - `test_70d_13_state_endpoint_reports_disabled_honestly` / `..._real_values`; `test_70d_10_toggle_persists_and_returns_new_state`; `test_70d_11_toggle_off_keeps_engine_untouched`; `test_70d_16_live_state_includes_liquidity_section`; `test_70d_17_reconnect_snapshot_restores_liquidity`; `test_70d_12_model_compatibility_reported` / `..._incompatible_engine_blocked_flag` (50 asserts).
- PITFALLS IT ENCODES: disabled state must be reported honestly (not fake zeros); toggle-off must not mutate the running engine; incompatible model flag must actually block.
- NOTES: Fake adapter/aggregator harness drives real HTTP app; SSE reconnect snapshot contract.
