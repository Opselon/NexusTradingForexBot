"""Central HTML formatting for the NSE Telegram CI/CD observability layer.

Every CI/release notification is rendered through THIS module so that:

* all dynamic values are HTML-escaped before insertion (Telegram-safe)
* a consistent visual design system is used everywhere
* long reports are split into logically grouped, HTML-valid chunks
* Persian/Unicode content and RTL/LTR mixing stays readable

Telegram HTML supports only: <b> <i> <u> <s> <a href> <code> <pre> <blockquote>.

All formatters return str (Telegram HTML). Values that are None render as
"n/a"; every dynamic value passes through esc() first.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any

# ---------------------------------------------------------------------------
# Escaping — EVERY dynamic value must pass through esc() before insertion
# ---------------------------------------------------------------------------


def esc(value: Any) -> str:
    """HTML-escape for Telegram parse_mode=HTML (None -> 'n/a')."""
    if value is None:
        return "n/a"
    return html.escape(str(value))


def esc_short(value: Any, limit: int = 200) -> str:
    """HTML-escape and trim long dynamic strings (messages, logs, titles)."""
    if value is None:
        return "n/a"
    text = str(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text)


def code(value: Any) -> str:
    """<code>...</code> for technical values (SHA, paths, names)."""
    return f"<code>{esc(value)}</code>"


def code_short(value: Any, limit: int = 200) -> str:
    return f"<code>{esc_short(value, limit)}</code>"


def link(url: str, label: str | None = None) -> str:
    """Telegram HTML link. A non-http(s)/@/tg:// URL renders as plain text."""
    if not url:
        return ""
    if not re.match(r"^(https?://|tg://|@)", url):
        return esc(url)
    return f'<a href="{esc(url)}">{esc(label or url)}</a>'


def _sha_short(sha: str | None, limit: int = 8) -> str:
    if not sha:
        return "n/a"
    return sha[:limit]


# ---------------------------------------------------------------------------
# Design-system tokens (section 18 of the spec)
# ---------------------------------------------------------------------------

OK_EMOJI = "✅"
FAIL_EMOJI = "❌"
WARN_EMOJI = "⚠️"
INFO_EMOJI = "🛈"
RUN_EMOJI = "🔄"
RELEASE_EMOJI = "🚀"
RETRY_EMOJI = "🔁"
TIMEOUT_EMOJI = "⏱️"
ARTIFACT_EMOJI = "📦"
TEST_EMOJI = "🧪"
SECURITY_EMOJI = "🛡️"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━"


def _head(emoji: str, title: str) -> str:
    return f"{emoji} <b>{esc(title.upper())}</b>"


def _kv(key: str, value: Any) -> str:
    return f"<b>{esc(key)}:</b> {value}"


def _kvk(key: str, value: Any) -> str:
    """key: <code>value</code> — for technical/to-copy values."""
    return f"<b>{esc(key)}:</b> {code(value)}"


def _section(title: str, lines: Iterable[str]) -> list[str]:
    out = [f"<b>{esc(title)}</b>"]
    out.extend(lines)
    return out


def _blockquote(lines: Iterable[str]) -> str:
    """Wrap a block of lines in <blockquote>…</blockquote> (Telegram supports it)."""
    return "<blockquote>\n" + "\n".join(lines) + "\n</blockquote>"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h {s % 3600 // 60}m {s % 60}s"
    if s >= 60:
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Event context (correlation block) — section 12
# ---------------------------------------------------------------------------


class CIContext:
    """Correlation context shared by every message of one run/release."""

    def __init__(
        self,
        *,
        repository: str = "",
        workflow: str = "",
        run_id: str = "",
        run_number: str = "",
        job: str = "",
        attempt: str = "",
        branch: str = "",
        sha: str = "",
        pr_number: str = "",
        release_version: str = "",
        server_url: str = "https://github.com",
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.run_id = run_id
        self.run_number = run_number
        self.job = job
        self.attempt = attempt
        self.branch = branch
        self.sha = sha
        self.pr_number = pr_number
        self.release_version = release_version
        self.server_url = server_url

    @property
    def correlation_id(self) -> str:
        """NEXUS-CI-<run>-<sha4> — stable across messages of one run."""
        run = self.run_number or self.run_id or "0"
        return f"NEXUS-CI-{run}-{_sha_short(self.sha, 4).upper() if self.sha else '----'}"

    def repo_url(self) -> str:
        return f"{self.server_url}/{self.repository}" if self.repository else ""

    def run_url(self) -> str:
        if not self.repository or not self.run_id:
            return ""
        return f"{self.server_url}/{self.repository}/actions/runs/{self.run_id}"

    def commit_url(self) -> str:
        if not self.repository or not self.sha:
            return ""
        return f"{self.server_url}/{self.repository}/commit/{self.sha}"

    def pr_url(self) -> str:
        if not self.repository or not self.pr_number:
            return ""
        return f"{self.server_url}/{self.repository}/pull/{self.pr_number}"

    def with_pr(self, pr_number: str) -> CIContext:
        """Return a copy carrying the PR number (for PR event reports)."""
        clone = CIContext(
            repository=self.repository,
            workflow=self.workflow,
            run_id=self.run_id,
            run_number=self.run_number,
            job=self.job,
            attempt=self.attempt,
            branch=self.branch,
            sha=self.sha,
            pr_number=pr_number or self.pr_number,
            release_version=self.release_version,
            server_url=self.server_url,
        )
        return clone

    def release_url(self, tag: str = "") -> str:
        if not self.repository:
            return ""
        t = tag or self.release_version
        if not t:
            return ""
        return f"{self.server_url}/{self.repository}/releases/tag/{t}"

    def context_lines(self, *, include_job: bool = True) -> list[str]:
        lines = [
            _kv("Repository", code(self.repository)),
            _kv("Workflow", code(self.workflow)),
        ]
        if include_job and self.job:
            lines.append(_kv("Job", code(self.job)))
        lines.append(_kv("Branch", code(self.branch)))
        lines.append(_kv("Commit", code(_sha_short(self.sha))))
        if self.run_number or self.run_id:
            lines.append(_kv("Run", code(f"#{self.run_number or self.run_id}")))
        if self.attempt:
            lines.append(_kv("Attempt", code(self.attempt)))
        if self.pr_number:
            lines.append(_kv("PR", code(f"#{self.pr_number}")))
        if self.release_version:
            lines.append(_kv("Version", code(self.release_version)))
        lines.append(_kv("Correlation", code(self.correlation_id)))
        return lines

    # ------------------------------------------------------------------
    # Links section
    # ------------------------------------------------------------------

    def links(self) -> str:
        out = []
        if self.run_url():
            out.append(link(self.run_url(), "Open Run"))
        if self.commit_url():
            out.append(link(self.commit_url(), "Open Commit"))
        if self.pr_url():
            out.append(link(self.pr_url(), "Open PR"))
        if self.release_url():
            out.append(link(self.release_url(), "Open Release"))
        return "  ".join(out) if out else ""


# ---------------------------------------------------------------------------
# CI run formatters
# ---------------------------------------------------------------------------


def format_run_started(ctx: CIContext, *, jobs: str = "") -> str:
    """Workflow queued/started."""
    lines = [
        _head(RUN_EMOJI, "CI RUNNING"),
        DIVIDER,
        *ctx.context_lines(),
    ]
    if jobs:
        lines.append(_kv("Jobs", esc(jobs)))
    lines.append("")
    lines.append("<i>Workflow started — see linked run for live progress.</i>")
    return _blockquote(lines)


def format_run_success(
    ctx: CIContext,
    *,
    duration_sec: float | None = None,
    tests: int | None = None,
    failed: int | None = None,
    skipped: int | None = None,
    coverage: float | None = None,
    checks: dict[str, str] | None = None,
) -> str:
    lines = [
        _head(OK_EMOJI, "CI SUCCESS"),
        DIVIDER,
        *ctx.context_lines(),
    ]
    if duration_sec is not None:
        lines.append(_kv("Duration", code(_duration(duration_sec))))
    lines.append("")
    if checks:
        lines.extend(_checks_block(checks))
    if tests is not None or coverage is not None:
        lines.append("")
        lines.append("<b>Tests</b>")
        if tests is not None:
            lines.append(f"Total: {code(tests)}")
        if failed is not None:
            lines.append(f"Failed: {code(failed)}")
        if skipped is not None:
            lines.append(f"Skipped: {code(skipped)}")
        if coverage is not None:
            lines.append(f"Coverage: {code(f'{coverage:.1f}%')}")
    lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


def format_run_failure(
    ctx: CIContext,
    *,
    duration_sec: float | None = None,
    tests: int | None = None,
    passed: int | None = None,
    failed: int | None = None,
    skipped: int | None = None,
    coverage: float | None = None,
    checks: dict[str, str] | None = None,
    failures: list[str] | None = None,
    error_short: str = "",
    next_action: str = "",
) -> str:
    lines = [
        _head(FAIL_EMOJI, "CI FAILED"),
        DIVIDER,
        *ctx.context_lines(),
    ]
    if duration_sec is not None:
        lines.append(_kv("Duration", code(_duration(duration_sec))))
    lines.append("")
    if tests is not None:
        lines.append("<b>Tests</b>")
        lines.append(f"Total: {code(tests)}  Passed: {code(passed or 0)}")
        lines.append(f"Failed: {code(failed or 0)}  Skipped: {code(skipped or 0)}")
        if coverage is not None:
            lines.append(f"Coverage: {code(f'{coverage:.1f}%')}")
        lines.append("")
    if checks:
        lines.extend(_checks_block(checks))
        lines.append("")
    if failures:
        lines.append("<b>Failure</b>")
        lines.append(esc_short(failures[0], 200))
        if len(failures) > 1:
            lines.append(f"… and {len(failures) - 1} more")
    elif error_short:
        lines.append("<b>Error</b>")
        lines.append(esc_short(error_short, 400))
    if next_action:
        lines.append("")
        lines.append(_kv("Next", esc(next_action)))
    lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


def format_run_cancelled(ctx: CIContext, *, reason: str = "") -> str:
    lines = [
        _head(WARN_EMOJI, "CI CANCELLED"),
        DIVIDER,
        *ctx.context_lines(),
    ]
    if reason:
        lines.append(_kv("Reason", esc_short(reason, 200)))
    return _blockquote(lines)


def _checks_block(checks: dict[str, str]) -> list[str]:
    """checks: {name: status} where status in passed|failed|errored|skipped|cancelled."""
    out = ["<b>Checks</b>"]
    for name, status in checks.items():
        mark = {
            "passed": OK_EMOJI,
            "success": OK_EMOJI,
            "failed": FAIL_EMOJI,
            "errored": FAIL_EMOJI,
            "cancelled": WARN_EMOJI,
            "skipped": INFO_EMOJI,
        }.get(str(status).lower(), INFO_EMOJI)
        out.append(f"{mark} {esc(name)}: <code>{esc(status).upper()}</code>")
    return out


def format_test_summary(
    *,
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    coverage: float | None = None,
    duration_sec: float | None = None,
    failed_names: list[str] | None = None,
) -> str:
    lines = [
        _head(TEST_EMOJI, "TEST RESULTS"),
        DIVIDER,
        f"Total: {code(total)}  Passed: {code(passed)}",
        f"Failed: {code(failed)}  Skipped: {code(skipped)}",
    ]
    if coverage is not None:
        lines.append(f"Coverage: {code(f'{coverage:.1f}%')}")
    if duration_sec is not None:
        lines.append(f"Duration: {code(_duration(duration_sec))}")
    if failed_names:
        lines.append("")
        lines.append("<b>Failed tests</b>")
        lines.extend(f"• {code_short(n, 160)}" for n in failed_names[:10])
        if len(failed_names) > 10:
            lines.append(f"… and {len(failed_names) - 10} more")
    return _blockquote(lines)


# ---------------------------------------------------------------------------
# Release formatters
# ---------------------------------------------------------------------------


def format_release_started(ctx: CIContext, *, tag: str = "", phase: str = "") -> str:
    lines = [
        _head(RELEASE_EMOJI, "RELEASE STARTED"),
        DIVIDER,
        *ctx.context_lines(),
    ]
    if tag:
        lines.append(_kv("Tag", code(tag)))
    if phase:
        lines.append(_kv("Phase", esc(phase)))
    return _blockquote(lines)


def format_release_success(
    ctx: CIContext,
    *,
    tag: str = "",
    gates: dict[str, str] | None = None,
    tests: dict[str, int] | None = None,
    coverage: float | None = None,
    artifacts: list[str] | None = None,
    verification: list[str] | None = None,
) -> str:
    lines = [
        _head(RELEASE_EMOJI, "RELEASE PUBLISHED"),
        DIVIDER,
        _kv("Version", code(ctx.release_version or tag)),
    ]
    if tag:
        lines.append(_kv("Tag", code(tag)))
    lines.append(_kv("Commit", code(_sha_short(ctx.sha))))
    lines.append("")
    if gates:
        lines.extend(_checks_block(gates))
        lines.append("")
    if tests:
        lines.append("<b>Tests</b>")
        lines.append(
            f"Total: {code(tests.get('total', 0))}  Passed: {code(tests.get('passed', 0))}"
        )
        lines.append(
            f"Failed: {code(tests.get('failed', 0))}  Skipped: {code(tests.get('skipped', 0))}"
        )
        if coverage is not None:
            lines.append(f"Coverage: {code(f'{coverage:.1f}%')}")
        lines.append("")
    if artifacts:
        lines.append("<b>Artifacts</b>")
        lines.extend(f"📦 {code_short(a, 160)}" for a in artifacts)
        lines.append("")
    if verification:
        lines.append("<b>Verification</b>")
        lines.extend(f"✅ {esc(v)}" for v in verification)
        lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


def format_release_failure(
    ctx: CIContext,
    *,
    tag: str = "",
    failed_phase: str = "",
    failed_job: str = "",
    duration_sec: float | None = None,
    retry_count: int = 0,
    error_class: str = "",
    error_detail: str = "",
    affected_artifact: str = "",
    recovery_status: str = "",
    next_action: str = "",
) -> str:
    lines = [
        _head(FAIL_EMOJI, "RELEASE FAILED"),
        DIVIDER,
        _kv("Version", code(ctx.release_version or tag)),
        _kv("Commit", code(_sha_short(ctx.sha))),
    ]
    if failed_phase:
        lines.append(_kv("Failed phase", code(failed_phase)))
    if failed_job:
        lines.append(_kv("Failed job", code(failed_job)))
    if duration_sec is not None:
        lines.append(_kv("Duration", code(_duration(duration_sec))))
    lines.append(_kv("Retries", code(retry_count)))
    if error_class:
        lines.append(_kv("Error class", code(error_class)))
    if error_detail:
        lines.append(_kv("Error", code_short(error_detail, 400)))
    if affected_artifact:
        lines.append(_kv("Affected artifact", code_short(affected_artifact, 160)))
    if recovery_status:
        lines.append(_kv("Recovery", esc(recovery_status)))
    if next_action:
        lines.append(_kv("Next", esc(next_action)))
    lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


# ---------------------------------------------------------------------------
# Push / PR formatters
# ---------------------------------------------------------------------------


def format_push_event(
    ctx: CIContext,
    *,
    author: str = "",
    commit_count: int = 0,
    latest_sha: str = "",
    messages: list[str] | None = None,
    additions: int | None = None,
    deletions: int | None = None,
    ci_status: str = "",
) -> str:
    lines = [
        _head(INFO_EMOJI, "PUSH"),
        DIVIDER,
        _kv("Branch", code(ctx.branch)),
        _kv("Author", esc(author)),
        _kv("Commits", code(commit_count)),
        _kv("Latest", code(_sha_short(latest_sha or ctx.sha))),
    ]
    if additions is not None or deletions is not None:
        lines.append(_kv("Changes", code(f"+{additions or 0} -{deletions or 0}")))
    if messages:
        lines.append("")
        lines.append("<b>Commits</b>")
        lines.extend(f"• {esc_short(m, 120)}" for m in messages[:5])
        if len(messages) > 5:
            lines.append(f"… and {len(messages) - 5} more")
    if ci_status:
        lines.append(_kv("CI", esc(ci_status)))
    lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


def format_pr_event(
    ctx: CIContext,
    *,
    action: str = "updated",
    title: str = "",
    author: str = "",
    changed_files: int | None = None,
    additions: int | None = None,
    deletions: int | None = None,
    ci_status: str = "",
) -> str:
    action_emoji = {
        "opened": OK_EMOJI,
        "reopened": OK_EMOJI,
        "merged": RELEASE_EMOJI,
        "closed": WARN_EMOJI,
    }.get(action.lower(), INFO_EMOJI)
    lines = [
        _head(action_emoji, f"PR {esc(action.upper())}"),
        DIVIDER,
        _kv("PR", code(f"#{ctx.pr_number}")),
        _kv("Title", esc_short(title, 160)),
        _kv("Author", esc(author)),
    ]
    if changed_files is not None:
        lines.append(_kv("Changed files", code(changed_files)))
    if additions is not None or deletions is not None:
        lines.append(_kv("Changes", code(f"+{additions or 0} -{deletions or 0}")))
    if ci_status:
        lines.append(_kv("CI", esc(ci_status)))
    lines.append("")
    if ctx.links():
        lines.append(ctx.links())
    return _blockquote(lines)


def format_security_event(ctx: CIContext, *, scan: str, status: str, detail: str = "") -> str:
    mark = OK_EMOJI if str(status).lower() in ("passed", "success", "clean") else FAIL_EMOJI
    lines = [
        _head(SECURITY_EMOJI, f"SECURITY {esc(status.upper())}"),
        DIVIDER,
        _kv("Scan", code(scan)),
        *ctx.context_lines(),
    ]
    if detail:
        lines.append(_kv("Detail", esc_short(detail, 300)))
    lines.append(f"{mark} Status: <code>{esc(status).upper()}</code>")
    return _blockquote(lines)


def format_retry(
    ctx: CIContext,
    *,
    operation: str,
    attempt: int,
    max_attempts: int,
    reason: str = "",
    backoff_sec: float | None = None,
    deadline_remaining_sec: float | None = None,
) -> str:
    lines = [
        _head(RETRY_EMOJI, "RETRYING"),
        DIVIDER,
        _kv("Operation", code(operation)),
        _kv("Attempt", code(f"{attempt} / {max_attempts}")),
    ]
    if reason:
        lines.append(_kv("Reason", esc_short(reason, 160)))
    if backoff_sec is not None:
        lines.append(_kv("Backoff", code(f"{backoff_sec:.1f}s")))
    if deadline_remaining_sec is not None:
        lines.append(_kv("Deadline remaining", code(_duration(deadline_remaining_sec))))
    lines.append(_kv("Correlation", code(ctx.correlation_id)))
    return _blockquote(lines)


def format_timeout(ctx: CIContext, *, operation: str, timeout_sec: float, attempts: int = 0) -> str:
    lines = [
        _head(TIMEOUT_EMOJI, "TIMEOUT"),
        DIVIDER,
        _kv("Operation", code(operation)),
        _kv("Timeout", code(f"{timeout_sec:.0f}s")),
        _kv("Attempts", code(attempts)),
        _kv("Correlation", code(ctx.correlation_id)),
    ]
    return _blockquote(lines)


def _split_preserving(text: str, limit: int) -> list[str]:
    """Split escaped text into <=limit chunks on blank-line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
        if len(line) > limit and len(current) >= limit:
            if current == line:
                current = ""
            chunks.extend(line[:limit])
    if current:
        chunks.append(current)
    return chunks


def format_error_details(title: str, error_text: str, *, limit: int = 1500) -> list[str]:
    """Split a long error dump into logical <pre> chunks, HTML-escaped."""
    text = esc(error_text)
    chunks = _split_preserving(text, limit)
    out = [_head(FAIL_EMOJI, title)]
    out.extend(f"<pre>{c}</pre>" for c in chunks)
    return out


def format_artifact_summary(
    ctx: CIContext,
    *,
    artifacts: list[str],
    verified: list[str] | None = None,
    retention_days: int | None = None,
) -> str:
    lines = [
        _head(ARTIFACT_EMOJI, "ARTIFACTS"),
        DIVIDER,
        *ctx.context_lines(),
        "",
        "<b>Files</b>",
    ]
    lines.extend(f"📦 {code_short(a, 160)}" for a in artifacts)
    if verified:
        lines.append("")
        lines.append("<b>Verified</b>")
        lines.extend(f"✅ {esc(v)}" for v in verified)
    if retention_days:
        lines.append(_kv("Retention", code(f"{retention_days}d")))
    return _blockquote(lines)


# ---------------------------------------------------------------------------
# Smart message splitting — always HTML-valid, never splits mid-tag
# ---------------------------------------------------------------------------

#: Telegram soft cap; we stay safely under it.
MAX_MESSAGE_CHARS = 4000

_TAG_RE = re.compile(r"<(/?){0,1}(b|i|u|s|a|code|pre|blockquote)(\s[^>]*)?>")


def split_html_message(text: str, max_chars: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a long HTML message into Telegram-safe chunks.

    Splits on paragraph/blank-line boundaries where possible; never splits
    inside a tag; carries open-block tags (code/pre/blockquote) onto the next
    chunk and closes them so every chunk is standalone-valid HTML.

    Returns a list of 1..N chunk strings, each <= max_chars.
    """
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
        # hard guard: a single line longer than max_chars must be split too
        if len(line) > max_chars and len(current) >= max_chars:
            pieces = _split_line_html_safe(line, max_chars)
            if current == line:
                current = ""
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
    if current:
        chunks.append(current)
    # normalize: close open tags per chunk
    return [_close_open_tags(c) for c in chunks]


def _split_line_html_safe(line: str, max_chars: int) -> list[str]:
    """Split one oversized line at tag boundaries, preserving tag integrity.

    Plain text runs are hard-chunked at max_chars too, so a single line with
    no tags (e.g. a long Persian string) is still split into valid pieces.
    """
    parts: list[str] = []
    buf = ""
    for raw_token in _tokenize_html(line):
        is_tag = _TAG_RE.fullmatch(raw_token) is not None
        if is_tag:
            buf += raw_token
            continue
        # text run: hard-chunk if it alone exceeds max_chars
        token = raw_token
        while len(token) > max_chars:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(token[:max_chars])
            token = token[max_chars:]
        if len(buf) + len(token) > max_chars and buf:
            parts.append(buf)
            buf = token
        else:
            buf += token
    if buf:
        parts.append(buf)
    return parts or [line[:max_chars]]


def _tokenize_html(line: str) -> list[str]:
    """Tokenize a line into (tag | text-run) tokens."""
    tokens: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(line):
        if m.start() > pos:
            tokens.append(line[pos : m.start()])
        tokens.append(m.group(0))
        pos = m.end()
    if pos < len(line):
        tokens.append(line[pos:])
    return tokens or [line]


_OPEN_TAGS: dict[str, str] = {
    "b": "</b>",
    "i": "</i>",
    "u": "</u>",
    "s": "</s>",
    "code": "</code>",
    "pre": "</pre>",
    "blockquote": "</blockquote>",
}


def _close_open_tags(chunk: str) -> str:
    """Balance a chunk so it is standalone-valid Telegram HTML.

    - drops leading orphan closing tags (their opener lives in a previous chunk)
    - closes any tags left open at the end
    """
    # 1) strip leading orphan closers: </tag> that appears before any opener
    cleaned = list(chunk)
    changed = True
    while changed:
        changed = False
        for m in _TAG_RE.finditer("".join(cleaned)):
            if m.group(1) == "/":
                # orphan if no same-name opener before it in this chunk
                prefix = "".join(cleaned[: m.start()])
                opener = f"<{m.group(2)}"
                if f"<{m.group(2)}>" not in prefix and opener not in prefix:
                    del cleaned[m.start() : m.end()]
                    changed = True
                    break
    chunk = "".join(cleaned)
    # 2) close trailing open tags
    stack: list[str] = []
    for m in _TAG_RE.finditer(chunk):
        closing = m.group(1) == "/"
        name = m.group(2)
        if not closing:
            stack.append(name)
        elif name in stack:
            stack = stack[: stack.index(name)]
    for name in reversed(stack):
        chunk += _OPEN_TAGS.get(name, "")
    return chunk


__all__ = [
    "DIVIDER",
    "FAIL_EMOJI",
    "MAX_MESSAGE_CHARS",
    "OK_EMOJI",
    "WARN_EMOJI",
    "CIContext",
    "code",
    "code_short",
    "esc",
    "esc_short",
    "format_artifact_summary",
    "format_error_details",
    "format_pr_event",
    "format_push_event",
    "format_release_failure",
    "format_release_started",
    "format_release_success",
    "format_retry",
    "format_run_cancelled",
    "format_run_failure",
    "format_run_started",
    "format_run_success",
    "format_security_event",
    "format_test_summary",
    "format_timeout",
    "link",
    "split_html_message",
]
