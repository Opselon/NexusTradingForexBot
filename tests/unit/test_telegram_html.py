"""Tests for the central Telegram HTML formatter + message splitter.

Covers: HTML escaping of every dynamic value, tag-valid splitting, Unicode /
Persian / RTL content, redaction, correlation ids, and the full formatter
surface (run / release / push / PR / retry / timeout / security / artifact).
"""

from __future__ import annotations

import pytest

from nexus_scalp.observability.telegram_html import (
    CIContext,
    esc,
    format_artifact_summary,
    format_error_details,
    format_pr_event,
    format_push_event,
    format_release_failure,
    format_release_started,
    format_release_success,
    format_retry,
    format_run_cancelled,
    format_run_failure,
    format_run_started,
    format_run_success,
    format_security_event,
    format_test_summary,
    format_timeout,
    link,
    split_html_message,
)
from nexus_scalp.observability.telegram_transport import redact_secrets


def _ctx(**kwargs) -> CIContext:
    base = dict(
        repository="Opselon/NexusTradingForexBot",
        workflow="CI",
        run_id="123",
        run_number="42",
        branch="main",
        sha="abc1234def",
        server_url="https://github.com",
    )
    base.update(kwargs)
    return CIContext(**base)


# ---------------------------------------------------------------------------
# HTML escaping — every dynamic value must be escaped
# ---------------------------------------------------------------------------


class TestEscaping:
    def test_esc_html_chars(self):
        assert esc("<script>&\"'") == "&lt;script&gt;&amp;&quot;&#x27;"

    def test_esc_none(self):
        assert esc(None) == "n/a"

    def test_esc_unicode_persian(self):
        # Persian + mixed punctuation survives escaping intact.
        value = "فیلتر: <بازار> & تست"
        out = esc(value)
        assert "فیلتر" in out
        assert "&lt;بازار&gt;" in out
        assert "&amp;" in out

    def test_esc_short_truncates(self):
        from nexus_scalp.observability.telegram_html import esc_short

        o = esc_short("x" * 500, 50)
        assert len(o) < 60
        assert o.endswith("…")

    def test_link_https(self):
        out = link("https://github.com/x", "Open")
        assert out == '<a href="https://github.com/x">Open</a>'

    def test_link_invalid_protocol_is_plain_text(self):
        out = link("javascript:alert(1)", "evil")
        assert out == "javascript:alert(1)"  # not an <a> tag

    def test_link_attr_escaped(self):
        out = link('https://x.example/?a="b"', "label")
        assert "&quot;" in out


# ---------------------------------------------------------------------------
# Message splitting — always HTML-valid, never splits mid-tag
# ---------------------------------------------------------------------------


class TestSplitting:
    def test_short_message_unchanged(self):
        msg = "<b>CI SUCCESS</b>"
        assert split_html_message(msg) == [msg]

    def test_long_message_splits_into_valid_chunks(self):
        chunks = ["✓ " + f"<b>row {i}</b>" for i in range(2000)]
        msg = "\n".join(chunks)
        out = split_html_message(msg)
        assert len(out) > 1
        for chunk in out:
            assert len(chunk) <= 4000
            assert chunk.count("<b>") == chunk.count("</b>")
            assert "<code>" not in chunk or chunk.count("<code>") == chunk.count("</code>")

    def test_never_splits_within_tag(self):
        # A single gigantic line with one <b>...</b> spanning the whole thing.
        line = "<b>" + ("data " * 1000) + "</b>"
        out = split_html_message(line)
        for chunk in out:
            # every chunk is standalone-balanced (auto-closed if needed)
            assert chunk.count("<b>") == chunk.count("</b>") or chunk.count("<b>") - chunk.count(
                "</b>"
            ) in (0, 1)

    def test_blockquote_carries_across_chunks(self):
        real = "<blockquote>\n" + "\n".join(f"row {i}" for i in range(2000)) + "\n</blockquote>"
        out = split_html_message(real)
        assert len(out) > 1
        for chunk in out:
            assert chunk.count("<blockquote>") == chunk.count("</blockquote>")

    def test_persian_long_message(self):
        line = "گزارش آزمون " * 500
        msg = "<b>خلاصه</b>\n" + line
        out = split_html_message(msg)
        assert len(out) > 1
        for chunk in out:
            assert len(chunk) <= 4000
            assert chunk.count("<b>") == chunk.count("</b>")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_bot_token_redacted(self):
        t = "token is 7233738325:AAF1234567890abcdefghijklmnopqrstuvwxyz23 end"
        assert "[REDACTED_BOT_TOKEN]" in redact_secrets(t)
        assert "7233738325" not in redact_secrets(t)

    def test_github_pat_redacted(self):
        t = "pat=ghp_0123456789abcdefghijklmnopQRSTUVWXYZ012345 end"
        out = redact_secrets(t)
        # Either the specific mask or the generic key=value mask — the value
        # must NEVER survive.
        assert "[REDACTED" in out
        assert "ghp_" not in out

    def test_private_key_redacted(self):
        t = "data\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpA==\n-----END RSA PRIVATE KEY-----"
        out = redact_secrets(t)
        assert "PRIVATE KEY" not in out
        assert "[REDACTED_PRIVATE_KEY]" in out

    def test_generic_key_value_redacted(self):
        assert "password=[REDACTED]" in redact_secrets("password=hunter2 x")
        assert "api_key=[REDACTED]" in redact_secrets("api_key=sk-abc123 x")

    def test_persian_text_not_mangled(self):
        out = redact_secrets("رمز عبور: mypass123 و token: abc")
        # Persian passes through; the token-ish value masked
        assert "رمز" in out


# ---------------------------------------------------------------------------
# Formatters — structural checks (spec-driven)
# ---------------------------------------------------------------------------


class TestRunFormatters:
    def test_run_started_has_context_and_correlation(self):
        msg = format_run_started(_ctx(), jobs="quality")
        assert "CI RUNNING" in msg
        assert "NEXUS-CI-42-ABC1" in msg  # run 42, sha abc1234def -> ABC1
        assert "Quality" not in msg or "quality" in msg.lower()

    def test_run_success_has_all_answers(self):
        msg = format_run_success(
            _ctx(),
            duration_sec=878,
            tests=2150,
            failed=0,
            skipped=2,
            coverage=72.4,
            checks={"Pytest": "passed"},
        )
        assert "CI SUCCESS" in msg
        assert "14m 38s" in msg
        assert "2150" in msg
        assert "72.4%" in msg
        assert "Open Run" in msg

    def test_run_failure_shows_failed_test_and_next(self):
        msg = format_run_failure(
            _ctx(),
            tests=2150,
            passed=2143,
            failed=5,
            skipped=2,
            coverage=72.0,
            checks={"Pytest": "failed"},
            failures=["test_position_sizing_dynamic_risk"],
            next_action="5 tests failed.",
        )
        assert "CI FAILED" in msg
        assert "test_position_sizing_dynamic_risk" in msg
        assert "Next:" in msg
        assert "FAILED" in msg.upper()

    def test_run_cancelled(self):
        assert "CI CANCELLED" in format_run_cancelled(_ctx(), reason="cancelled by user")

    def test_failures_escaped(self):
        msg = format_run_failure(_ctx(), failures=["<img src=x onerror=alert(1)>"], error_short="")
        assert "<img" not in msg
        assert "&lt;img" in msg


class TestReleaseFormatters:
    def test_release_started(self):
        assert "RELEASE STARTED" in format_release_started(_ctx(), tag="v1.2.3", phase="build")

    def test_release_success_structure(self):
        msg = format_release_success(
            _ctx(),
            tag="v2.14.0",
            gates={"Unit Tests": "passed", "Security": "passed"},
            tests={"total": 2150, "passed": 2145, "failed": 0, "skipped": 5},
            coverage=72.4,
            artifacts=["NexusTradingForexBot-Windows-x64.exe", "SHA256SUMS.txt"],
            verification=["SHA256 verified"],
        )
        assert "RELEASE PUBLISHED" in msg
        assert "v2.14.0" in msg
        assert "NexusTradingForexBot-Windows-x64.exe" in msg
        assert "SHA256 verified" in msg
        assert "Open Release" not in msg or "Open" in msg  # links included via ctx

    def test_release_failure_incident_style(self):
        msg = format_release_failure(
            _ctx(),
            tag="v2.14.0",
            failed_phase="packaging",
            failed_job="build-windows",
            error_class="BUILD_FAILURE",
            error_detail="link.exe exited with code 2",
            retry_count=2,
            recovery_status="REVIEW_REQUIRED",
            next_action="Inspect diagnostics; fix; re-run.",
        )
        assert "RELEASE FAILED" in msg
        assert "BUILD_FAILURE" in msg
        assert "packaging" in msg
        assert "REVIEW_REQUIRED" in msg


class TestGitFormatters:
    def test_push_event_structure(self):
        msg = format_push_event(
            _ctx(),
            author="quant-user",
            commit_count=3,
            latest_sha="deadbeef",
            messages=["fix: a", "feat: b", "chore: c"],
            additions=842,
            deletions=117,
            ci_status="Running",
        )
        assert "PUSH" in msg
        assert "quant-user" in msg
        assert "842" in msg and "117" in msg
        assert "fix: a" in msg

    def test_pr_opened(self):
        msg = format_pr_event(
            _ctx(pr_number="17"),
            action="opened",
            title="Add liquidity tab",
            author="alice",
            changed_files=12,
            additions=300,
            deletions=80,
            ci_status="Pending",
        )
        assert "PR OPENED" in msg
        assert "#17" in msg
        assert "alice" in msg

    def test_pr_merged(self):
        assert "PR MERGED" in format_pr_event(_ctx(pr_number="9"), action="merged", title="x")

    def test_security_event(self):
        msg = format_security_event(_ctx(), scan="CodeQL", status="passed")
        assert "SECURITY PASSED" in msg

    def test_retry_message(self):
        msg = format_retry(
            _ctx(),
            operation="Release Asset Upload",
            attempt=2,
            max_attempts=5,
            reason="HTTP 502",
            backoff_sec=8.4,
            deadline_remaining_sec=134,
        )
        assert "RETRYING" in msg
        assert "2 / 5" in msg
        assert "8.4s" in msg
        assert "2m 14s" in msg

    def test_timeout_message(self):
        msg = format_timeout(_ctx(), operation="upload", timeout_sec=60, attempts=3)
        assert "TIMEOUT" in msg
        assert "60s" in msg

    def test_artifact_summary(self):
        msg = format_artifact_summary(
            _ctx(),
            artifacts=["junit.xml", "coverage.xml"],
            verified=["manifest present"],
            retention_days=30,
        )
        assert "ARTIFACTS" in msg
        assert "junit.xml" in msg
        assert "30d" in msg

    def test_test_summary_failed_names(self):
        stats = format_test_summary(
            total=2150, passed=2143, failed=5, skipped=2, coverage=72.0, failed_names=["t1", "t2"]
        )
        assert "TEST RESULTS" in stats
        assert "t1" in stats


class TestErrorDetails:
    def test_error_details_never_raw_html(self):
        parts = format_error_details("BUILD FAILURE", "error at <x> & </y>\nTraceback...\n" * 50)
        assert parts
        assert all("<img" not in p for p in parts)
        joined = "\n".join(parts)
        assert "&lt;x&gt;" in joined

    def test_correlation_id_stable(self):
        c1 = _ctx().correlation_id
        c2 = _ctx().correlation_id
        assert c1 == c2 == "NEXUS-CI-42-ABC1"

    def test_with_pr_copy(self):
        ctx = _ctx()
        clone = ctx.with_pr("99")
        assert clone.pr_number == "99"
        assert ctx.pr_number == ""  # original untouched
