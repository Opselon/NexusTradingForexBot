# tests/unit/test_liquidity_engine_contract.py

- GUARDS: TASK-01-60D-LIQUIDITY — contract, registry, BSL/SSL, EQH/EQL, missing-value, edge cases (TEST-LIQ-01..11, 32-37, 44).
- KEY ASSERTIONS:
  - BSL/SSL and EQH/EQL compute to expected levels on known bars; missing values are None/unknown, never fabricated; registry dimension names/order canonical; edge cases (one-bar, empty, all-symmetric) handled without error (50 asserts).
- PITFALLS IT ENCODES: missing reference levels must be honestly absent; registry is the naming authority for the 60 dims.
- NOTES: Contract foundation for causality/features siblings.
