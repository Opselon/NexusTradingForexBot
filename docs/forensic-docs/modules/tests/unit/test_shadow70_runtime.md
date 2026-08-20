# tests/unit/test_shadow70_runtime.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TEST-SHADOW-01..35 (TASK-05-70D-SHADOW) 70D shadow runtime unit suite (59 defs / 1083 lines).
- Load validation (01-05b): valid 70D candidate loads → SHADOW_READY/READY with contract dimension + schema id; NO candidate → `NO_VALIDATED_CANDIDATE`/IDLE (truthful); invalid manifest → `MANIFEST_VALID` gate; schema mismatch → `SCHEMA_VALID` + BLOCKED; scaler mismatch blocked; artifact hash mismatch blocked; non-validated candidate blocked.
- Inference (06-07b): shadow inference succeeds; inference failure ISOLATED (shadow never breaks champion path); bad vector rejected.
- Champion protection: shadow decisions never reach MT5; champion output unchanged during shadow.
- Storage: queued-writer pattern (`assert "_queue.put_nowait" in src_w` — source-level guard for the async-write discipline); `wk._queue.qsize() <= 10` bounded; outcome rows flushed before reads.
- BUG-105 guard: `h.shadow70_count() == 1` — "70D hook must run even without a 50D shadow".
- Fixtures: `default_runtime`, `fake_audit_repo`; `test_shadow48_replay_parity_deterministic` replay parity.