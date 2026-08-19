# CI/CD Telegram Operations — NexusTradingForexBot

Enterprise-grade CI/CD observability: every important CI/release event is
reported to Telegram as structured, HTML-formatted, deduplicated,
rate-limited, retryable, secret-safe notifications — with file/artifact
attachments when something fails.

## 1. Architecture

```
GitHub Actions  ── ci-results/ (make_ci_results.py)
GitHub events   ── GITHUB_* env
        │
        ▼
scripts/ci/telegram_notify.py   (workflow entry point, exit 0 always)
        │
        ▼
src/nexus_scalp/observability/ci_telegram_reporter.py   (orchestrator)
        │  ├─ reads ci-results/ (run-info/*.json, junit.xml, coverage.xml)
        │  ├─ resolves chat id (TELEGRAM_CHAT_ID > NEXUS_TELEGRAM_ADMIN_ID)
        │  ├─ formats via telegram_html.py (central HTML renderer)
        │  ├─ redacts secrets (telegram_transport.redact_secrets)
        │  └─ splits long messages (split_html_message, HTML-valid)
        ├─ TelegramNotifier        (existing: queue, retry, 429, health)
        └─ TelegramDocumentTransporter (NEW: sendDocument upload + captions)
```

Separation of concerns (spec §20):

- **Event generation** — workflow steps call `telegram_notify.py <cmd>`
- **Normalization** — reporter reads real ci-results data (never guessed)
- **Policy** — severity gating, dedup window, cooldown (existing notifier)
- **Redaction** — `redact_secrets()` masks known secret shapes before text
  or file content reaches Telegram
- **HTML rendering** — `telegram_html.py` (only place that builds markup)
- **Splitting** — `split_html_message()` (tag-aware, logical groups)
- **Transport** — notifier (text) + document transporter (files)
- **Retry/timeout** — bounded, exponential backoff, 429 Retry-After
- **Audit** — structured logs + health_state() on the notifier

## 2. Configuration (chat id)

Required secrets (repository scope: **Settings > Secrets and variables >
Actions > Secrets**):

| Secret | Purpose |
|--------|---------|
| `TELEGRAM_BOT_TOKEN` | The bot token (from @BotFather). |
| `TELEGRAM_CHAT_ID` | Numeric destination chat id (see below). |

How to obtain the chat id:

1. Message your bot once (`/start`).
2. `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and read
   `result[0].message.chat.id` in the response.
3. Add that number as `TELEGRAM_CHAT_ID`.

Resolution order in the reporter: explicit `--chat-id` > `TELEGRAM_CHAT_ID`
env > `USER_ID` env > `NEXUS_TELEGRAM_ADMIN_ID` env. `USER_ID` is the
existing repository secret (numeric Telegram destination) and serves as the
fallback destination when `TELEGRAM_CHAT_ID` is not configured. When none is
set, notifications are skipped (reported as `TELEGRAM_CONFIG_ERROR`) and CI
never fails.

Optional richer routing (add only when needed):

- `TELEGRAM_ALERT_CHAT_ID` — alerts/incidents
- `TELEGRAM_RELEASE_CHAT_ID` — release-only feed

## 3. Events covered

| Event | Command | Message |
|-------|---------|---------|
| Workflow started | `run-started` | `CI RUNNING` + context + correlation |
| Workflow finished | `run-finished` | `CI SUCCESS` / `CI FAILED` / `CI CANCELLED` + tests + coverage + failures + Next action; on failure uploads diagnostics |
| Tests | `test-summary` | `TEST RESULTS` + totals + failed names + coverage |
| Artifacts | `artifacts` | `ARTIFACTS` + file list + verification + retention |
| Release started | `release-started --tag` | `RELEASE STARTED` + tag + phase |
| Release gates | `test-summary` (gates job) | `TEST RESULTS` + totals + coverage |
| Release success | `release-success --tag` | `RELEASE PUBLISHED` + gates + tests + coverage + artifacts + verification |
| Release failure | `release-failed` | `RELEASE FAILED` + phase/job/error class/retries/recovery/Next + diagnostics |
| Post-release verified | (verify step in release job) | API re-fetch: tag match + assets present |
| Push | `push` | `PUSH` + author/branch/commits/latest/changes/CI |
| PR | `pr --action` | `PR OPENED/UPDATED/MERGED/CLOSED` + title/author/changes/CI |
| Security scan | `security --scan --status` | `SECURITY PASSED/FAILED` + scan name + context |

## 4. Message format (HTML design system)

- Only Telegram-supported HTML tags: `<b> <i> <u> <s> <a href> <code> <pre> <blockquote>`
- Every dynamic value passes `esc()` (HTML-escaped) — dynamic strings can
  never break the markup
- Vocabulary: ✅ SUCCESS · ❌ FAILURE · ⚠️ WARNING · 🛈 INFO · 🔄 RUNNING ·
  🚀 RELEASE · 🔁 RETRY · ⏱️ TIMEOUT · 📦 ARTIFACT · 🧪 TEST · 🛡️ SECURITY
- Every message carries the correlation id `NEXUS-CI-<run>-<sha4>` and
  context (repository/workflow/job/branch/commit/run)

Long reports are split by `split_html_message()` into logical chunks
(SUMMARY → FAILED TESTS → ERROR DETAILS → ARTIFACTS → LOGS), each chunk
standalone-valid HTML (tags never split; Persian/Unicode preserved).

## 5. File / artifact handling

On failure the reporter:

1. builds `nexus-ci-diagnostic.zip` (summary.json, failed-tests.txt,
   junit.xml, coverage.xml, pytest.txt, manifest, SHA256SUMS; all redacted)
2. uploads it via `sendDocument` with a caption (file/job/run/commit/reason)
3. additionally uploads junit.xml, coverage.xml, summary.md individually

File size cap: 20 MB (Telegram hard cap 50 MB). Missing/oversized files
return structured failures, never exceptions.

## 6. Retry / timeout policy

- Text sends: existing notifier — max 3 attempts, exponential backoff
  (base 2s), 429 honors Retry-After, 5xx retried, 4xx non-retryable
- Document uploads: transport — max 2 retries, backoff 2s * 2^attempt,
  Retry-After honored, 5xx/429 retried, auth/config not retried
- Timeouts: HTTP timeout 30s (uploads) / 4s (text); on timeout the send is
  retried per the policy above
- Aggregation: no per-microscopic-retry messages — only final outcomes
  surface (success/failure/exhausted)

## 7. Secret-redaction policy

`redact_secrets()` masks before anything reaches Telegram:

- Telegram bot tokens (`\d{8,10}:[A-Za-z0-9_-]{25,}`)
- GitHub tokens (`ghp_…`, `github_pat_…`)
- OpenAI-style `sk-…`, Slack `xoxb-…`, AWS `AKIA…`
- PEM private key markers (BEGIN/END)
- generic `key=value` shapes for password/secret/token/api_key/auth

Applied to: all message text, captions, and file contents included in the
diagnostic bundle. GitHub's own masking remains a second layer; we never
rely on it alone.

## 8. Failure isolation

- `telegram_notify.py` always exits 0 — CI never fails on Telegram errors
- Missing config → `TELEGRAM_CONFIG_ERROR` result, no exception
- Transport failures → structured `{ok, category, retryable}` results
- The notifier's `health_state()` reports READY/DEGRADED/STOPPED

## 9. Testing

`tests/unit/test_telegram_html.py` (36 tests) covers escaping, splitting
(tag-safe, oversized lines, Persian), redaction, correlation ids, and every
formatter. `tests/unit/test_ci_telegram_reporter.py` (19 tests) covers
real-ci-results reads (junit wrapper, coverage), chat-id resolution, dispatch
(success/failure/cancelled), diagnostic bundle + redacted content, transport
isolation (missing/oversized files), and multipart construction.

## 10. Operations notes

- Normal pushes: quality job sends `run-started` + `run-finished` +
  `artifacts` (3 messages per run max — bounded, deduplicated)
- Heavy CI (ci-tests/dispatch): per-arm and aggregate messages follow the
  same paths via the aggregate job's ci-results tree
- Releases: `release-started` / `release-success` / `release-failed` from
  release.yml steps
- Diagnostics uploads happen ONLY on failure (or release failure) — success
  never floods the feed with files