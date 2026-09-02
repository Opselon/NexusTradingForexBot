# src/nexus_scalp/observability/telegram_html.py

- **PURPOSE:** Central HTML formatting for the NSE Telegram/CI
  observability layer — every notification (notifier templates + CI
  reporter) is rendered through THIS module so all dynamic values are
  HTML-escaped before insertion (render-safe HTML invariant).
- **ARCHITECTURE LAYER:** Observability (rendering).
- **RESPONSIBILITY:** esc/esc_short/code/code_short/link/_head/_kv/_kvk/
  _section/_sha_short building blocks; deterministic rendering; support
  for the tag-safe split (segments never orphan an open tag).
- **DEPENDENCIES:** none beyond stdlib.
- **CONNECTS TO:** telegram_notifier (all templates), ci_telegram_reporter,
  tests (test_telegram_html — escaping/splitting/Persian/redaction).
- **KEY CONCEPTS:** escaping covers quotes (attribute contexts), not just
  tags; Persian/RTL text passes through splitting byte-exact; emoji count
  toward Telegram's 4096-char limit as characters (the splitter counts
  chars, not bytes).
- **EDGE CASES & PITFALLS:** a value containing a full HTML snippet
  (e.g. a log excerpt) must be escaped as TEXT, never inlined as markup
  (the injection guard); truncation helpers (esc_short/code_short) must
  not split surrogate pairs.