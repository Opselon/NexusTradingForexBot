# tests/unit/test_liquidity_runtime_integration_phase18.py

- GUARDS: TASK-02-70D-INTEGRATION — 70D liquidity integration + UI/Runtime control plane, TEST-70D-01..28 acceptance (brief 33, adapted to the actual repo contract per TEST-29: registry already registers `scalp_v3` = 350D forward-declared research schema).
- KEY ASSERTIONS:
  - engine state honors the Liquidity toggle; 10 liquidity dims at 60..69 present/absent per mode; runtime hook path with news/liquidity on/off; UI/control-plane toggles persist and reflect state; model-compat flag for the registered schema (230 asserts).
- PITFALLS IT ENCODES: schema registry is the single source of truth (350D forward declaration must not break 70D runtime); toggling must not disturb the running engine.
- NOTES: Largest unit file (1141 lines); `_Champion50DEngine`/`_ChampLike` harness driving LiveEngine.
