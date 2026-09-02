# tests/unit/test_telegram_notifier.py + test_telegram_html.py + test_telegram_reporting_bug057.py + test_telegram_forensics_bug072.py

- **GUARDS:** The Telegram observability layer: notifier lifecycle,
  HTML escaping, reporting, and settings-forensics.
- **test_telegram_notifier.py:** queue+worker lifecycle (ENQUEUED →
  SEND_START → SEND_RESULT/DELIVERED|FAILED_FINAL); HTTP 200 + ok:false =
  failure; 429/5xx bounded retry; 400-class never retried; error taxonomy
  categories; health_state() READY/DEGRADED/STOPPED; worker heartbeat;
  templates (close/breakeven/trailing/survival/kill-switch/daily).
- **test_telegram_html.py:** esc/esc_short/code/link/_head/_kv/_section
  escaping — quotes/tags/surrogate pairs; tag-safe split (segments never
  orphan an open tag; emoji count as chars); Persian/RTL passes byte-exact;
  redaction (secrets never in output).
- **test_telegram_reporting_bug057.py:** canonical daily summary from
  AccountingCore PeriodKind.DAY (never synthetic); the reporting
  templates render valid HTML.
- **test_telegram_forensics_bug072.py:** settings-driven telegram config
  forensics (BUG-072): token resolution via SettingsService (never
  live.yaml); masking; the get_me probe verdict; sender status truth.
- **PITFALLS IT ENCODES:** structlog capture trap (configure_logging
  first; raiseExceptions=False; restore in finally); the fallback-IP
  transport works when api.telegram.org is unreachable (probe first,
  retry on timeout).