# src/nexus_scalp/forensics/telegram_report.py

- PURPOSE: Periodic Telegram forensic report (TASK-12 §30-32) — a BOUNDED
  summarized report of the FORENSIC_HEALTH_SNAPSHOT (never every
  individual check). Config-driven (enabled/interval/minimum_severity/
  aggregation_window), deduplicated + cooldown-gated so identical
  recurring conditions never spam the operator. Report builder is pure
  (snapshot → text); delivery goes through the existing settings-service
  notifier (INV-010 — telegram is a read-only consumer of canonical
  state).
- ARCHITECTURE LAYER: Application (periodic reporter).
- RESPONSIBILITY: ForensicReportConfig (load_report_config),
  build_report_text (summarized text), TelegramReportScheduler
  (should_send/dedup/mark_sent/run_once + state persistence),
  DEFAULT_ENABLED/INTERVAL/MIN_SEVERITY/AGGREGATION_WINDOW, per-check
  COOLDOWN.
- DEPENDENCIES: forensics.engine, forensics.models, json, time,
  pathlib, logging; yaml lazy; settings service (notifier) lazy.
- CONNECTS TO: LiveEngine background task (periodic), notifier
  (delivery), dashboard state file artifacts/forensics/
  telegram_report_state.json.
- KEY CONCEPTS:
  - DEFAULTS: disabled by default; interval 6h; min severity WARNING;
    aggregation window 1h. Config from configs/base.yaml →
    forensic_report.*; never raises (defaults on any error).
  - build_report_text: aggregate counts + 16 group statuses + top 5
    incidents (CRITICAL/DEGRADED sorted by severity, evidence[:100]).
  - Scheduler state: persisted JSON (last_sent_at monotonic float +
    per-(check_id,status) cooldown map) — restart-safe dedup.
  - run_once flow: snapshot(persist=True) → dedup (checks in
    WARNING/DEGRADED/CRITICAL/UNKNOWN whose cooldown elapsed) →
    build text → gate: disabled → reason; interval not elapsed →
    reason; NO fresh conditions → reason + still marks sent-attempt so
    we don't rescan every tick; deliver via svc notifier.send() (failure
    logged, never raised; dry-run when deliver=False) → mark_sent.
  - DEDUP MODEL: identical (check_id,status) recurring conditions are
    aggregated — a check that fired within the aggregation window is
    skipped; a NEW status for the same check is a different fingerprint
    and fires.
- HOT PATH / PERFORMANCE: runs on the report interval (6h default) from
  a background task — never the tick path; snapshot runs the full check
  matrix (seconds).
- EDGE CASES & PITFALLS: default DISABLED means the whole feature is
  inert until config opts in (intentional safety); the interval gate
  uses time.monotonic while the persisted state stores the same
  monotonic float — a RESTART resets monotonic base, so _last_sent_at
  from disk is in a different epoch: after restart, (now_monotonic -
  persisted_monotonic) is negative-ish → interval gate passes
  immediately (double-send right after restart until first cycle);
  mark_sent writes cooldown entries for ALL checks (even PASS) — the map
  grows with check ids (bounded by check count, fine); dedup includes
  UNKNOWN statuses (correct — unknowns are notable); notifier lookup
  relies on getattr(svc, "notifier"_/_notifier) private attribute
  conventions; delivery failure sets sent=False but the cooldown is
  still marked — a broken notifier suppresses retries for the window.