# tests/unit/test_liquidity_optimization_phase19.py

- GUARDS: TASK-6 TEST-LIQ-OPT-01..28 — 70D liquidity optimization contract: the v1.1 CANDIDATE module (src/nexus_scalp/features/liquidity_engine_opt.py) must keep every v1 guarantee while optimizing; the frozen v1 engine is the baseline.
- KEY ASSERTIONS:
  - v1.1 output == v1 output over the same inputs; same registry/schema; same parity guarantees; no behavioral regression across the whole TEST-LIQ matrix (35 asserts).
- PITFALLS IT ENCODES: an optimized engine may change only speed, never numbers — the frozen v1 is the oracle for the candidate.
- NOTES: Companion of test_70d_bug106_incremental_phase19 (same kept-identical-output philosophy).
