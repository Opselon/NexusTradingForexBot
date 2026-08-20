# tests/unit/test_mt5_status_endpoint.py

- GUARDS: /api/mt5/status endpoint + runtime-mode safety (Phase 14): account snapshot, live tick, chart-history source, runtime mode (PAPER live / LIVE_CONFIGURED-MT5_DISCONNECTED), versioning, error hygiene.
- KEY ASSERTIONS:
  - `test_account_snapshot_present`; `test_live_tick_present`; `test_chart_history_paper_source`; `test_runtime_mode_paper`; `test_live_configured_but_mt5_disconnected`; `test_mt5_disconnect_surfaces_in_live_state`; `test_state_version_monotonic`; `test_no_tracebacks_exposed`; `test_engine_offline_safe_payload` (33 asserts).
- PITFALLS IT ENCODES: MT5 disconnect must be surfaced in live state (never silent); endpoint payloads must never expose tracebacks; engine offline → safe payload.
- NOTES: `_FakeShadowAdapter`; pair with the live-state contract suite.
