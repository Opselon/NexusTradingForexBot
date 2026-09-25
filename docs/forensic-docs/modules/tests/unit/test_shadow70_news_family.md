# tests/unit/test_shadow70_news_family.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TEST-70D-NEWS-01..05 — TASK-10: the 70D NEWS-FAMILY projection contract. Regression guard for the LIVE-PATH `[:10]` truncation bug (news state encoding silently dropped from the 70D NEWS-FAMILY block at indices 50..59).
- Guards: news family is EXACTLY 10 features; state encoding PRESERVED through the projection (the bug: blind first-10 slice dropped news_state); values are REAL fields, not zeros; wrong-width projection REJECTED; family finite and bounded.
- 5 defs / 71 lines — minimal tight projection contract.
- NOTE: pairs with shadow70 module tests; the 70D block = Base 0..49 | News 10D 50..59 | Liquidity 10D 60..69 (canonical scalp_v3).