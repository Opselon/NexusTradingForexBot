# tests/helpers/golden70d.py

- GUARDS: The deterministic 70D golden corpus (TASK-03-70D-PARITY, brief 36) — engineered market scenarios covering trending / ranging / high volatility / low volatility, News ON/OFF, Liquidity active/inactive (BSL, SSL, EQH, EQL, sweep, no sweep, HTF confluence). All downstream parity tests run against this corpus: if dataset builder, replay and inference adapter produce the SAME vector from it, parity holds.
- KEY ASSERTIONS: no asserts (pure data-builder); the corpus stores per scenario: timestamp, schema, 70D vector, news status, liquidity status.
- PITFALLS IT ENCODES: parity must be asserted on IDENTICAL known inputs (bit-exact), so the corpus is rebuilt deterministically from raw rows — never from a prior computation (would silently encode the same bug twice).
- NOTES: Functions: `_to_rows` (raw bar rows), `_compute_70d` (canonical compute), `build_70d_golden_corpus()`, `corpus_scenario_names()`. Used by test_70d_replay_parity_task3 & co., which also pin "no CUL in dataset" anti-leakage checks.
