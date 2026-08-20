# tests/unit/test_70d_parity_task3.py

- GUARDS: TASK-03-70D-PARITY — Training/Live/Replay exact parity (TEST-03-01..20). Central invariant (brief 1): ONE CAUSAL MARKET WINDOW → ONE CANONICAL LIQUIDITY CALCULATION → TRAINING == REPLAY == LIVE (same 10 liquidity dims at indices 60..69).
- KEY ASSERTIONS:
  - training-window features == replay-window features; live computation at runtime equals both; the PROVEN FIX (TASK-03) that training reads only past bars (35 asserts).
- PITFALLS IT ENCODES: anti-leakage — the same function must compute the window in all three contexts; any context-dependent difference is a parity failure.
- NOTES: The parity suite the whole 70D migration is anchored on; golden corpus is the input.
