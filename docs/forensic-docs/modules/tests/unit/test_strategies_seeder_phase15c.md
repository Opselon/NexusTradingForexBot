# tests/unit/test_strategies_seeder_phase15c.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 15C built-in strategy seeder & research-worker integration tests: Ichimili built-ins are seeded into the research registry.
- Guards: seed registers BOTH ichimili candidates (registry count == 2); seed is IDEMPOTENT and PRESERVES validation (existing backtest/walkforward/OOS/robustness results survive re-seed); seed versions content-addressed (version == canonical_version); worker seeds on FIRST cycle (seeding happens before dataset/discovery each cycle).
- 6 defs / 113 lines; `audit_repo` fixture + `_count_registry`.
- NOTE: research-safety contract — seeding is registry-only, no order authority, no MT5.