# tests/unit/test_bug081_forensics.py + test_bug081_telegram_canonical.py + test_bug046_outcome_repair.py

# test_bug081_forensics.py
- **GUARDS:** BUG-081 split-fill context inheritance + broker-truth stop
  classification (RISK_FREE_SL_HIT/BREAK_EVEN_SL_HIT only when
  was_sl_modified=True; never-moved stops = HARD_SL_HIT).
- **KEY ASSERTIONS:** classifier CASE A-D; every sibling ticket of a
  split-fill resolves the SAME immutable entry context; provenance gaps
  recorded as NO_STAGED_CONTEXT (never silent 0.0 confidence);
  _pending_context_registry bounds (TTL 3600s, max 64).

# test_bug081_telegram_canonical.py
- **GUARDS:** Telegram close notifications use
  notify_canonical_close (same exit_mechanism the classifier writes) —
  never re-inferred from broker reason codes, never defaulted to MANUAL.
- **KEY ASSERTIONS:** the 3 close-notification call sites in
  order_manager use the canonical path; a BREAK_EVEN_SL_HIT broker truth
  renders as such even when the broker reason code is unknown (the
  ticket 152500222827 incident).

# test_bug046_outcome_repair.py
- **GUARDS:** experience/outcome_repair — repairs missing/broken outcome
  rows deterministically.
- **KEY ASSERTIONS:** repair is idempotent and provenance-safe (repairs
  only verifiable gaps, never overwrites existing outcomes); the repair
  path logs what it touched.