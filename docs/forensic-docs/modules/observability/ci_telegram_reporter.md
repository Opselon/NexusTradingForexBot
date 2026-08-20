# src/nexus_scalp/observability/ci_telegram_reporter.py + telegram_html.py

# ci_telegram_reporter.py
- **PURPOSE:** The CI/CD Telegram reporter orchestrator: consumes the
  ci-results/ tree (scripts/ci/make_ci_results.py output) + GitHub
  Actions environment and emits the correlated (NEXUS-CI-<run>-<sha4>)
  HTML notifications with diagnostic bundles.
- **RESPONSIBILITY:** start/finish/failure notification flow; secret
  redaction; tag-safe split for long payloads; sendDocument uploads;
  failure-isolated (CI never fails on Telegram — exit 0 always at the
  script layer).
- **DEPENDENCIES:** telegram_transport (document transporter), telegram_html
  (rendering), httpx, env.
- **CONNECTS TO:** .github/workflows (ci.yml steps), scripts/ci/
  telegram_notify.py, tests (test_ci_telegram_reporter).

# telegram_html.py
- **PURPOSE:** Central HTML formatting for the CI observability layer —
  every CI/release notification is rendered through THIS module so all
  dynamic values are HTML-escaped before insertion (render-safe HTML
  invariant).
- **RESPONSIBILITY:** esc/esc_short/code/code_short/link/_head/_kv/_kvk/
  _section/_sha_short building blocks; deterministic rendering.
- **DEPENDENCIES:** none beyond stdlib.
- **CONNECTS TO:** telegram_notifier templates + ci_telegram_reporter;
  tests (test_telegram_html — escaping/splitting/Persian/redaction).

# KEY CONCEPTS (both)
- Escaping covers quotes (attribute contexts), not just tags;
  splitting respects tag boundaries (a split must never orphan an open
  tag); redaction is applied at the LAST layer before send (defense in
  depth with settings masking + logging entropy redaction).
- **EDGE CASES & PITFALLS:** Persian/RTL text must pass through splitting
  unchanged (byte-exact segments); emoji counts toward Telegram's 4096
  char limit as characters — the splitter must count chars, not bytes.