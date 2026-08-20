# tests/unit/test_liquidity_engine_features.py

- GUARDS: TASK-01-60D-LIQUIDITY — HTF liquidity, internal/external distance, confluence, training/live/replay parity, legacy-model dimension gates, config switch, dataset artifact smoke (TEST-LIQ-12..17, 29-31, 38-40, 45).
- KEY ASSERTIONS:
  - HTF and internal/external distances compute with known direction; confluence combines levels; training/live/replay parity of the liquidity dims; legacy models gated on dimension compatibility; config switch toggles liquidity engine (26 asserts).
- PITFALLS IT ENCODES: parity applies to liquidity dims too (same causal window → same dims everywhere).
- NOTES: Features-level counterpart of the parity suites.
