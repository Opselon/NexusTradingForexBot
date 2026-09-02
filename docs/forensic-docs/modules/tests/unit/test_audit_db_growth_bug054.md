# tests/unit/test_audit_db_growth_bug054.py

- GUARDS: BUG-054 regression suite — persistent signal dedup, guard telemetry, lean payloads, bounded retention purge (spec §23).
- KEY ASSERTIONS:
  - identical decision twice → exactly ONE row (across request_ids); different decisions → rows preserved; TICK_DUPLICATE_SUPPRESSED ticks don't append; purge keeps DB growth bounded; telemetry records guard hits (15 asserts).
- PITFALLS IT ENCODES: dedup key spans request ids (dedup is by decision, not request); retention purge must never delete distinct decisions.
- NOTES: Same-second probe ticks are TICK_DUPLICATE_SUPPRESSED — advance probe timestamps a full minute for genuine second signal rows.
