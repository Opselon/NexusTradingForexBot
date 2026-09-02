# tests/unit/test_order_manager_exit_bugs.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-7 exit-intelligence regression suite (BUG-085..090 class) from live forensics (artifacts/audit.db).
- BUG-085 protective-mod truthfulness: `_last_modify_sl` must ONLY advance on a CONFIRMED broker modification — failed breakeven / failed trailing do NOT pollute it; retry happens after cooldown; modify-SL dispatch NEVER loosens protection (monotonic SL enforcement).
- BUG-086..090 class: TP/partial-close, pending-cancel, exit-accounting truthfulness regressions — a position is only counted closed when the broker confirms; failed modifies leave the OM's internal exit book unchanged.
- Pattern: `_prime`/`_advance_pos` build a position timeline; MockMT5Adapter records order_send/modify/cancel counts so tests assert broker-interaction counts (zero-broker-touch assertions where required).
- 30 defs / 450 lines; DB via AuditRepository with queued-writer flush.
- NOTE: keys off confirmed-broker-state only — never optimistic bookkeeping (root cause of the BUG-085 class).