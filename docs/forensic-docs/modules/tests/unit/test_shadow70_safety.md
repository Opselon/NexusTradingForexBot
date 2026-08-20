# tests/unit/test_shadow70_safety.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- 70D Shadow SAFETY & champion-protection proofs (TASK-05-70D-SHADOW) — TEST-SHADOW-36..40.
- TEST-SHADOW-36: Champion BUY vs Shadow SELL → champion output NEVER altered (`test_shadow36_champion_output_never_altered`).
- TEST-SHADOW-37: broker order/modify/cancel count == 0 over the whole run (`test_shadow37_broker_interaction_zero` — MockBroker counters).
- TEST-SHADOW-38: failure cascade isolation — a failing component leaves champion + shadow book intact.
- TEST-SHADOW-39: memory bounded under load.
- TEST-SHADOW-40: worker persists to REAL DB (RealRepo + `_flush_readonly`; never close() mid-test — BUG-058).
- 20 defs / 289 lines.