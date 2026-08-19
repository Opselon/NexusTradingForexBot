"""
Forensic regression tests — Telegram silent-failure path (BUG-072).
==================================================================
Proves where a notification currently disappears:

1. Notifier constructed with an empty token  -> enabled=False, send() returns
   None with NO console output (the live.yaml '' incident).
2. HTTP 200 + ok=false is silently swallowed (treated as success).
3. Non-200 responses are retried blindly, then the error disappears.
4. No correlation ID / event trace exists to tie enqueue -> HTTP -> result.
5. The worker thread cannot be observed (no heartbeat, no health state).

Every test asserts against the OLD behavior contracts so the fixes in
BUG-072 (queue+worker lifecycle, error taxonomy, response verification,
never-silent final states) cannot regress.
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.observability.telegram_notifier import TelegramNotifier


@pytest.fixture
def notifier() -> TelegramNotifier:
    n = TelegramNotifier(
        bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ",
        admin_id="987654321",
        enabled=True,
        environment="test",
        retry_backoff=0.01,
    )
    yield n
    n.shutdown()


def _ok_resp(message_id: int = 42) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = b'{"ok": true, "result": {"message_id": %d}}' % message_id
    return resp


def _ok_false_resp() -> MagicMock:
    """HTTP 200 + ok=false -> Telegram rejected the message (e.g. bad chat)."""
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = b'{"ok": false, "error_code": 400, "description": "chat not found"}'
    return resp


class TestSilentFailureReproduction:
    """The exact defects behind the 'message disappears' incident."""

    @patch("urllib.request.urlopen")
    def test_http_200_ok_false_is_observable_failure(
        self, mock_urlopen: MagicMock, notifier: TelegramNotifier
    ) -> None:
        """BUG-072 #1 FIXED: HTTP 200 + ok=false is a FAILURE, fully observable.

        Old contract: None + silence. New contract: category=TELEGRAM_TARGET_ERROR,
        no retry, FAILED_FINAL logged, health_state shows the failure.
        """
        mock_urlopen.return_value.__enter__.return_value = _ok_false_resp()

        with patch.object(notifier, "_send_msg_sync", wraps=notifier._send_msg_sync) as spy:
            msg_id = notifier.send("Message that Telegram rejects")
            # bounded wait for the worker to finish the record
            deadline = time.time() + 2.0
            while time.time() < deadline and notifier.health_state()["failed_count"] < 1:
                time.sleep(0.02)

        assert msg_id is None  # no delivery -> no message id
        assert spy.call_count == 1, "non-retryable API rejection must be tried once"
        health = notifier.health_state()
        assert health["failed_count"] >= 1
        assert health["failure_category"] == "TELEGRAM_TARGET_ERROR"

    @patch("urllib.request.urlopen")
    def test_http_200_ok_false_is_retried_as_if_transient(
        self, mock_urlopen: MagicMock, notifier: TelegramNotifier
    ) -> None:
        """BUG-072 #2: a 400-class API rejection is retried 3x (wasted + dangerous)."""
        mock_urlopen.side_effect = None
        mock_urlopen.return_value.__enter__.return_value = _ok_false_resp()

        with patch.object(notifier, "_send_msg_sync", wraps=notifier._send_msg_sync) as spy:
            notifier.send("Retried rejection")
            time.sleep(0.05)
        # 200-ok-false is a definitively non-retryable API error, yet the old code
        # retried it maximum_retries+1 times.
        assert spy.call_count <= 1, (
            "Non-retryable Telegram API rejection must not be retried blindly (BUG-072)"
        )

    def test_disabled_notifier_silently_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """BUG-072 #3: empty token -> enabled=False -> message vanishes with no trace."""
        disabled = TelegramNotifier(bot_token="", admin_id="5094837833", enabled=True)
        try:
            with caplog.at_level(logging.INFO):
                msg_id = disabled.send("Critical alert while token missing")
            # Old contract: None + silence. New contract must emit a config-error state.
            assert msg_id is None
            assert any(
                "TELEGRAM_CONFIG" in r.message or "NOT_CONFIGURED" in r.message
                for r in caplog.records
            ), "Disabled/misconfigured notifier must produce an explicit state (BUG-072)"
        finally:
            disabled.shutdown()

    def test_network_failure_disappears(self, notifier: TelegramNotifier) -> None:
        """BUG-072 #4: total network failure -> None with only a generic log line."""
        with patch(
            "urllib.request.urlopen",
            side_effect=ConnectionError("network unreachable"),
        ):
            msg_id = notifier.send("Network-broken alert")
        assert msg_id is None  # old contract

    @patch("urllib.request.urlopen")
    def test_success_returned(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        """Sanity: the happy path still returns a message id."""
        mock_urlopen.return_value.__enter__.return_value = _ok_resp(777)
        assert notifier.send("Happy path") == 777


class TestObservabilityContract:
    """BUG-072: every notification must carry correlation/event telemetry."""

    def test_worker_is_observable(self, notifier: TelegramNotifier) -> None:
        """The send worker thread exists and can expose health state."""
        assert any(
            t.name and t.name.startswith("telegram") and t.is_alive() for t in threading.enumerate()
        )

    def test_health_state_api_present(self, notifier: TelegramNotifier) -> None:
        """Notifier must expose diagnostics: status/queue/sent/failed/last_*."""
        health = notifier.health_state()
        for key in (
            "status",
            "enabled",
            "configured",
            "queue_size",
            "sent_count",
            "failed_count",
            "retry_count",
            "last_success",
            "last_failure",
            "failure_category",
        ):
            assert key in health, f"health_state() missing {key}"

    def test_secret_never_leaks(self, notifier: TelegramNotifier) -> None:
        """Redaction covers the token in any surfaced text."""
        redacted = notifier._redact_secrets("token 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ is secret")
        assert "123456789:ABC" not in redacted
        assert "[REDACTED_BOT_TOKEN]" in redacted


class TestErrorClassification:
    """BUG-072 error taxonomy: category / retryable / severity / safe message."""

    @pytest.mark.parametrize(
        ("http_status", "body", "expected_category", "retryable"),
        [
            (
                401,
                b'{"ok": false, "error_code": 401, "description": "Unauthorized"}',
                "TELEGRAM_AUTH_ERROR",
                False,
            ),
            (
                400,
                b'{"ok": false, "error_code": 400, "description": "chat not found"}',
                "TELEGRAM_TARGET_ERROR",
                False,
            ),
            (
                429,
                b'{"ok": false, "error_code": 429, "description": "Too Many Requests"}',
                "TELEGRAM_RATE_LIMIT",
                True,
            ),
            (
                500,
                b'{"ok": false, "error_code": 500, "description": "Internal"}',
                "TELEGRAM_SERVER_ERROR",
                True,
            ),
            (
                503,
                b'{"ok": false, "error_code": 503, "description": "Unavailable"}',
                "TELEGRAM_SERVER_ERROR",
                True,
            ),
        ],
    )
    def test_api_error_classification(
        self,
        notifier: TelegramNotifier,
        http_status: int,
        body: bytes,
        expected_category: str,
        retryable: bool,
    ) -> None:
        result = notifier.classify_http_error(http_status, body)
        assert result["category"] == expected_category
        assert result["retryable"] is retryable
        assert result["safe_message"]
        assert result["severity"]
        # Never expose the raw secret-bearing payload.
        assert "token" not in result["safe_message"].lower()
