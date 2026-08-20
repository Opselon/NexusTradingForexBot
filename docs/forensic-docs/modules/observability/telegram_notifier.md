# src/nexus_scalp/observability/telegram_notifier.py

- **PURPOSE:** The full-lifecycle Telegram notification engine
  (BUG-059 → BUG-076): QUEUE + WORKER-backed HTML-formatted alerts with
  an observable lifecycle — every notification carries
  notification_id/correlation_id/event_type/priority/target_class and
  logs ENQUEUED → SEND_START → SEND_RESULT/SEND_FAILED → DELIVERED |
  FAILED_FINAL (never silent).
- **ARCHITECTURE LAYER:** Observability (application service; network I/O
  ONLY in the worker — Telegram can never block the tick path).
- **RESPONSIBILITY:** (a) queue + worker (heartbeat every 5s);
  (b) HTTP 200 is VERIFIED against the JSON `ok` field (200+ok=false =
  failure); 429/5xx bounded-retry; 400-class never retried;
  (c) explicit error taxonomy (AUTH/TARGET/NETWORK/TIMEOUT/RATE_LIMIT/
  SERVER/HTTP/API/SERIALIZATION/QUEUE/WORKER/UNKNOWN) each with
  retryable+severity+safe_message; (d) `health_state()` → READY/DEGRADED/
  STOPPED + queue/sent/failed/last_success/last_failure/failure_category;
  (e) get_me() probe + send_diagnostic() labeled test; (f) templates:
  startup/stop, order open/close (profit/loss), break-even, trailing-stop,
  risk/survival/kill-switch, error, market summary, test, engine_stopped,
  engine_error (CRITICAL), audit_purge, warmup (one-shot), daily_summary
  (from AccountingCore PeriodKind.DAY, never synthetic);
  (g) `notify_canonical_close` — the canonical outcome close notification
  built from the SAME exit_mechanism the classifier writes to the ledger
  (BUG-081; never re-inferred, never defaulted to MANUAL);
  (h) token never logged/leaked (`_redact_secrets`).
- **DEPENDENCIES:** httpx, telegram_html (escaping helpers), settings
  secret store (token source — NEVER live.yaml at runtime, INV-010),
  accounting core (daily summary), logging.
- **CONNECTS TO:** LiveEngine (wired purge→6h, warmup→READY, daily→24h),
  OrderManager (close/order notifs), web (/api/telegram/test uses the REAL
  worker verdict; /api/settings/telegram/status exposes truthful worker
  health), CLI settings, tests (test_telegram_notifier,
  test_telegram_reporting_bug057, test_bug081_telegram_canonical,
  test_telegram_html).
- **KEY CONCEPTS:** The worker owns ALL network I/O (queue drained by a
  background thread); state is observable (health_state) and every failure
  is categorized + retry-bounded; templates are HTML with safe escaping;
  the canonical-close path is the single source of close-truth for
  Telegram (3 call sites in order_manager use it; legacy notify_manual_close
  has NO callers).
- **EDGE CASES & PITFALLS:** route intermittent → the fallback IP
  transport (149.154.166.110) is the verified workaround; 400-class errors
  must never retry (permanent); token resolution via settings service with
  env override (NEXUS_TELEGRAM_*) as diagnosis escape hatch.