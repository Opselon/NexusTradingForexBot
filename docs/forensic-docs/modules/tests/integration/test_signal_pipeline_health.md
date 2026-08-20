# tests/integration/test_signal_pipeline_health.py

- GUARDS: Signal pipeline health — end-to-end that a healthy engine produces signal rows end-to-end and health reporting reflects real state (5 asserts).
- KEY ASSERTIONS:
  - `TestConfigApiHotReload.test_signal_pipeline_health_integration`: signal generation path works through the full stack; health endpoint returns genuine pipeline status.
- PITFALLS IT ENCODES: probe ticks at the same instant are TICK_DUPLICATE_SUPPRESSED — advance probe timestamps a full minute for genuine second signal rows.
- NOTES: Smallest health-gate integration; same fake-engine harness family.
