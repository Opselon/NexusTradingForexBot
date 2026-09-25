# src/nexus_scalp/observability/logging.py

- **PURPOSE:** Structured logging (structlog) setup + sanitization —
  `setup_logging` (console + file, severity dirs, retention/prune
  throttle), `get_logger`, timestamp injection, and entropy-based
  secret redaction (`_shannon_entropy`, `_redact_value`,
  `_redact_sensitive_fields`) so tokens/keys never reach logs.
- **ARCHITECTURE LAYER:** Observability (cross-cutting).
- **RESPONSIBILITY:** one logging configuration for the whole process;
  redaction layered on top of structlog processors.
- **DEPENDENCIES:** structlog, stdlib logging, pathlib.
- **CONNECTS TO:** every module (get_logger), tests (test_logging,
  test_log_autopsy_fixes).
- **KEY CONCEPTS:** `_set_state` / prune throttle bound log-dir growth;
  entropy redaction hides high-entropy strings (tokens) while keeping
  normal text; severity-based subdirectories (error/warn/info/debug).
- **EDGE CASES & PITFALLS:** configure_logging must be idempotent;
  the structlog default PrintLoggerFactory trap (tests must call
  configure_logging before attaching capture handlers) is documented in
  the skill; raising handlers (rich ConsoleRenderer re-raising exc_info)
  must be controlled via logging.raiseExceptions.