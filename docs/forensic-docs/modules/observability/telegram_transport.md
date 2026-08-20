# src/nexus_scalp/observability/telegram_html.py + telegram_transport.py + ci_telegram_reporter.py

- **PURPOSE:** The Telegram HTML rendering + transport + CI reporting
  toolchain (details also summarized in observability/telegram_tooling.md):
  - `telegram_html.py` — esc/esc_short/code/code_short/link/_head/_kv/
    _kvk/_section/_sha_short: render-safe HTML building blocks for every
    notifier template (the escaping discipline — user/source content can
    never inject markup).
  - `telegram_transport.py` — `TelegramDocumentTransporter`: sendDocument
    uploads (diagnostic bundles) with `_classify_document_response` and
    `_retry_after_from_body` (429/Retry-After honored); `redact_secrets` —
    the scrubber applied before payloads leave the process.
  - `ci_telegram_reporter.py` — `CITelegramReporter`: GitHub Actions →
    Telegram (HTML format, tag-safe split, secret redaction, NEXUS-CI-
    <run>-<sha4> correlation, uploads, diagnostic bundle); `esc_ctx` for
    context-key escaping; the exit-0-always rule lives in the script that
    invokes it.
- **ARCHITECTURE LAYER:** Observability (transport/reporting).
- **RESPONSIBILITY:** make every outbound message safe, classified,
  correlated.
- **DEPENDENCIES:** httpx, stdlib.
- **CONNECTS TO:** telegram_notifier (templates), CI workflows
  (scripts/ci/telegram_notify.py), tests (test_telegram_html,
  test_ci_telegram_reporter).
- **KEY CONCEPTS:** classification is explicit per HTTP status + body
  (`ok:false` on 200 is a failure — the ok-field rule); Retry-After
  parsing prevents hammering rate limits; redaction is layered.
- **EDGE CASES & PITFALLS:** uploads over 50MB rejected by Telegram —
  the bundle builder must bound size; HTML escaping must cover quotes
  (attr contexts) not just tags.