# tests/unit/test_70d_perf_task3.py + test_candle_intel_* + test_ci_telegram_reporter.py

# test_70d_perf_task3.py
- **GUARDS:** 70D parity PERFORMANCE (TASK-3) — the 70D path stays within
  the latency budget (feature compute + assembly + inference).
- **KEY ASSERTIONS:** compute_70d assembly time bounded; inference on the
  assembled vector is fast enough for the shadow cadence; memory bounded
  (no per-tick allocation growth).

# test_candle_intel_classifier_patterns.py
- **GUARDS:** candle_intelligence/classifier + patterns — close-quality
  classification and the 29-pattern engine.
- **KEY ASSERTIONS:** deterministic classification (identical geometry →
  identical summary); INVALID on malformed input (NaN/Inf/zero-range);
  pattern detection math pinned per pattern family; weak-close flagging.

# test_candle_intel_decision_store.py
- **GUARDS:** candle_intelligence decision layer + store — decisions
  persist with audit columns.
- **KEY ASSERTIONS:** decision hierarchy respected (hard veto →
  regime → close validation → pattern → risk → execution); persisted
  rows carry full audit metadata; deterministic serialization.

# test_candle_intel_perf.py
- **GUARDS:** candle-intel performance guard — the new-bar cadence stays
  cheap.
- **KEY ASSERTIONS:** classification + decision cost bounded (the module
  must never block the tick loop); DB write path off-thread.

# test_ci_telegram_reporter.py
- **GUARDS:** observability/ci_telegram_reporter — CI→Telegram flow.
- **KEY ASSERTIONS:** correlation NEXUS-CI-<run>-<sha4>; HTML escaping of
  CI context values; redaction of secrets; upload path classification;
  exit-0-always behavior at the script layer.