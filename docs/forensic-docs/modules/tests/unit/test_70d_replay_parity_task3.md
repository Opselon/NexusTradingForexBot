# tests/unit/test_70d_replay_parity_task3.py

- GUARDS: TASK-03-70D-PARITY — dataset == replay parity + anti-leakage + golden corpus (TEST-70D-PARITY-05/07/08/13/18/30): replay produces identical 70D; dataset == replay bit-exact on the same causal window.
- KEY ASSERTIONS:
  - same-window bit-exactness; no CUL (current/unclosed) leakage into the dataset; golden corpus scenarios all produce the same vector via builder and replay (19 asserts).
- PITFALLS IT ENCODES: leakage is proven ABSENT only if the corpus contains known-future events and they never appear — engineered corpus covers trending/ranging/volatile/news/liquidity scenarios.
- NOTES: Uses tests/helpers/golden70d.py corpus builders.
