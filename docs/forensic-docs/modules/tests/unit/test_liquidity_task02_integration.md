# tests/unit/test_liquidity_task02_integration.py

- GUARDS: TASK-02 (60D liquidity integration) — config contract + runtime state: default Liquidity = OFF (config, first install, upgrade path); typed persisted setting (true/false round-trip, invalid rejected); runtime state reflects config.
- KEY ASSERTIONS:
  - default-off on fresh/legacy installs; setting round-trips through persistence with type safety; invalid values rejected; runtime/UI state mirrors the setting (72 asserts).
- PITFALLS IT ENCODES: feature-flag defaults are a contract — Liquidity must ship OFF and only turn on explicitly; persistence is typed (no truthy-string confusion).
- NOTES: TEST-TASK02-01..09/16/19-26 mapped; pairs with the phase18 runtime suite.
