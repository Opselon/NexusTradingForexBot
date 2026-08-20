# src/nexus_scalp/observability/telegram_html.py + telegram_transport.py + ci_telegram_reporter.py + logging.py + __init__.py

- **PURPOSE:** The Telegram HTML toolchain + structured logging + CI
  reporting:
  - `telegram_html.py` — rendering helpers (esc/esc_short/code/link/
    _head/_kv/_kvk/_section/_sha_short) producing render-safe HTML for all
    notifier templates (the escaping discipline that makes user content
    safe in HTML notifications).
  - `telegram_transport.py` — `TelegramDocumentTransporter`: sendDocument
    uploads (diagnostic bundles) with response classification
    (`_classify_document_response`) + Retry-After parsing; `redact_secrets`
    — the token/credential scrubber applied before any payload leaves the
    process.
  - `ci_telegram_reporter.py` — `CITelegramReporter`: GitHub Actions →
    Telegram CI observability (HTML format, tag-safe split, secret
    redaction, correlation NEXUS-CI-<run>-<sha4>, sendDocument uploads,
    diagnostic bundle), exit-0-always semantics (`scripts/ci/telegram_notify.py`
    never fails CI on Telegram).
  - `logging.py` — structlog setup (`setup_logging`, `get_logger`),
    timestamp injection, severity-based log dirs + retention/prune
    throttle, entropy-based redaction (`_shannon_entropy`) for secrets;
    the module-level configuration both console + file output.
- **ARCHITECTURE LAYER:** Observability (cross-cutting).
- **RESPONSIBILITY:** Make every notification/log/cI-report safe
  (escaped, redacted, correlation-stamped) and observable.
- **DEPENDENCIES:** structlog, httpx (transport/reporter), stdlib logging.
- **CONNECTS TO:** telegram_notifier (all templates), CI workflow scripts,
  the whole system (logging), tests (test_telegram_html,
  test_ci_telegram_reporter, test_logging, test_log_autopsy_fixes).
- **KEY CONCEPTS:** Redaction is layered (transport redact_secrets +
  logging entropy redaction + settings masking); CI reporting never fails
  the build; logging prune throttle avoids hot-loop log churn; the
  structlog test-capture trap (default PrintLoggerFactory bypasses stdlib
  handlers until configure_logging is called) is a documented test
  pitfall.
- **EDGE CASES & PITFALLS:** configure_logging must be idempotent;
  force log-level overrides must survive handler rebuilds; document
  uploads must classify non-200 responses explicitly (never assume
  success on HTTP 200 alone — the ok-field rule).