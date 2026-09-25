# tests/unit/test_shadow70_health_drift.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- 70D Shadow feature health / drift / state suite (TASK-05-70D-SHADOW, TEST-SHADOW-18/20/22 + INV-70D-004..006): per-feature health stats, drift classification (NORMAL/WATCH/WARNING/CRITICAL), news+liquidity provenance.
- Guards: health statistics truthful (mean/std/count from real vectors); stale + missing values classified; drift severity classification bounded; insufficient-evidence FLOOR (no classification from tiny samples); PSI math: same distribution → 0; `test_drift_never_auto_acts` — drift may not directly trade/modify (report-only discipline).
- Provenance: records carry news + liquidity state.
- Store: queued-writer pattern asserted against the real audit repo (`test_store_queued_writer_with_audit_repo`); shadow48 replay parity deterministic; vector-70 contract families; feature-name order matches slices.
- 13 defs / 323 lines.