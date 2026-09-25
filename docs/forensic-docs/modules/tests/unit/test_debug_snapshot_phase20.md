# tests/unit/test_debug_snapshot_phase20.py

- GUARDS: TEST-DEBUG-01..32 — Debug 70D forensic console acceptance: the canonical /api/debug/state snapshot contract (schema, all 70 dims, liquidity/news/model sections, policy gate trace, risk trace, exposure, execution, SSE, correlation id, JSON serialization, secret non-leakage).
- KEY ASSERTIONS:
  - active schema displayed; 70 dims validated with registry feature names; values match backend; every section (01–32) rendered; invalid model contract displayed; 70D mismatch surfaced; debug never blocks the hot path; JSON/datetime serialization safe; NO secret leakage; snapshot comparison + feature diff (151 asserts).
- PITFALLS IT ENCODES: debug output must never leak secrets or interpolate exception text; section failures surface as structured reasons, not stack dumps; debug cost must not touch the hot path.
- NOTES: 32 requirement-mapped test methods + API-level tests; large fake-engine harness.
