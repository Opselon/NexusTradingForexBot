"""
REGRESSION (TELEGRAM-HEALTH-001, 2026-10-22): a coalesced duplicate is NOT a
delivery failure.

Live signature: a busy day showed HEARTBEAT "sent=22 failed=1291" while the
underlying transport had delivered every message that was not a duplicate.
The worker folded the DEDUPLICATED terminal state into ``_failed_count``
(and refreshed ``last_failure``, keeping health_state() on
WORKER_DEGRADED forever). A duplicate is the dedup filter doing its job:
it must be counted as its own telemetry, not as a failure.
"""

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class _TerminalDedup:
    """Send one identical text until it coalesces, assert the counters."""

    @pytest.fixture()
    def notifier(self):
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        n = TelegramNotifier(
            bot_token="123456:TEST",
            admin_id=1234,
            environment="PRODUCTION",
            deduplication_window=30.0,
        )
        return n

    @staticmethod
    def _deliver(mock_urlopen: Any, msg_id: int) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": %d}}' % msg_id
        mock_urlopen.return_value.__enter__.return_value = mock_resp


@patch("urllib.request.urlopen")
def test_duplicate_is_not_counted_as_failure(mock_urlopen) -> None:
    """First send DELIVERS; identical send coalesces; failed_count stays 0
    and the dedup counter increments."""
    from nexus_scalp.observability.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        bot_token="123456:TEST",
        admin_id=1234,
        environment="PRODUCTION",
        deduplication_window=30.0,
    )
    _deliver_resp = MagicMock()
    _deliver_resp.status = 200
    _deliver_resp.read.return_value = b'{"ok": true, "result": {"message_id": 301}}'
    mock_urlopen.return_value.__enter__.return_value = _deliver_resp

    # send() acknowledges within a bounded 50ms window by design (hot callers
    # must never block on network I/O). On a loaded CI runner the worker thread
    # may not have delivered inside that window, which made this assertion a
    # timing race rather than a behavior check. Give it a real window.
    first = notifier.send("Repeated alert text ALPHA 001", wait_timeout=5.0)
    assert first == 301

    second = notifier.send("Repeated alert text ALPHA 001")
    assert second is None  # coalesced

    deadline = time.time() + 3.0
    while time.time() < deadline and notifier._dedup_count == 0:
        time.sleep(0.02)
    assert notifier._dedup_count >= 1, "duplicate was never counted as deduped"

    health = notifier.health_state()
    assert health["failed_count"] == 0, health
    assert health["sent_count"] == 1, health
    assert health["status"] == "READY", health


@patch("urllib.request.urlopen")
def test_real_failure_is_still_counted(mock_urlopen) -> None:
    """A genuinely failed delivery keeps counting against failed_count."""
    import urllib.error

    from nexus_scalp.observability.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ",
        admin_id="987654321",
        enabled=True,
        environment="test",
        deduplication_window=2.0,
        retry_backoff=0.01,
        maximum_retries=2,
        minimum_severity="INFO",
        rate_limit=60,
    )
    mock_urlopen.side_effect = urllib.error.URLError("connection refused")
    notifier.send("Failure path probe 777")
    deadline = time.time() + 8.0
    while time.time() < deadline and notifier._failed_count == 0:
        time.sleep(0.02)
    assert notifier._failed_count >= 1
    health = notifier.health_state()
    assert health["status"] == "DEGRADED", health
    notifier.shutdown()
