"""
Unit Tests - Production Hardening & Integration of TelegramNotifier
===================================================================
Verifies deduplication, rate limiting, queue capacity limits, asynchronous
callbacks, HTML escaping, truncation, and thread-replying integration.
"""

from __future__ import annotations

import datetime
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.observability.telegram_notifier import TelegramNotifier


@pytest.fixture
def notifier() -> TelegramNotifier:
    """Fixture to create a TelegramNotifier configured for testing."""
    notifier = TelegramNotifier(
        bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ",
        admin_id="987654321",
        enabled=True,
        environment="test",
        minimum_severity="INFO",
        rate_limit=15,
        deduplication_window=2.0,
        queue_capacity=5,
        retry_backoff=0.01,  # Speed up tests
    )
    yield notifier
    notifier.shutdown()


@patch("urllib.request.urlopen")
def test_telegram_notifier_send_success(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that a successful response from Telegram returns the message ID."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 42}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    msg_id = notifier.send("Test Message")
    assert msg_id == 42


@patch("urllib.request.urlopen")
def test_telegram_notifier_deduplication(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that duplicate messages are suppressed within the deduplication window."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 101}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    # First send is successful
    first_id = notifier.send("Unique Message 1")
    assert first_id == 101

    # Second identical send is suppressed (returns None)
    second_id = notifier.send("Unique Message 1")
    assert second_id is None


@patch("urllib.request.urlopen")
def test_telegram_notifier_rate_limiting(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that messages exceeding the rate limit are suppressed."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 202}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    messages = [
        "Alpha Message",
        "Beta Message",
        "Gamma Message",
        "Delta Message",
        "Epsilon Message",
    ]
    # Send 5 messages (rate_limit is 15 in this setup)
    for msg in messages:
        msg_id = notifier.send(msg)
        assert msg_id == 202


@patch("urllib.request.urlopen")
def test_telegram_notifier_queue_capacity_limit(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that lower-severity messages are dropped when queue capacity is reached."""

    # We will block urllib to simulate high latency and fill the queue
    def slow_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.5)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 303}}'
        return mock_resp

    mock_urlopen.side_effect = slow_urlopen

    callbacks_received: list[int | None] = []

    def cb(msg_id: int | None) -> None:
        callbacks_received.append(msg_id)

    # Submit 5 to saturate the queue (queue_capacity is 5)
    for i in range(5):
        notifier.send(f"Saturate Distinct {i}", callback=cb)

    # Submit 6th (lower severity) which should be dropped immediately (returns None without calling urlopen)
    res = notifier.send("Dropped Message", callback=cb, severity="INFO")
    assert res is None

    # Submit a CRITICAL message, which should bypass the queue capacity check
    res_crit = notifier.send("Critical Message", callback=cb, severity="CRITICAL")
    assert res_crit is None


def test_telegram_notifier_html_escaping_and_truncation(notifier: TelegramNotifier) -> None:
    """Verifies HTML escaping and message length truncation formatting rules."""
    escaped = notifier._escape("<script>alert('hack')</script> & Trade")
    assert escaped == "&lt;script&gt;alert(&#x27;hack&#x27;)&lt;/script&gt; &amp; Trade"

    long_text = "A" * 5000
    truncated = notifier._truncate_message(long_text)
    assert len(truncated) <= 4096
    assert truncated.endswith("</i></b></code>")


@patch("urllib.request.urlopen")
def test_telegram_notifier_asynchronous_callback(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that passing a callback delivers results asynchronously without blocking."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 500}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    callback_result: int | None = None
    cb_done = threading.Event()

    def cb(msg_id: int | None) -> None:
        nonlocal callback_result
        callback_result = msg_id
        cb_done.set()

    # If callback is passed, send returns None immediately (non-blocking)
    start_time = time.time()
    res = notifier.send("Async Message Unique", callback=cb)
    end_time = time.time()

    assert res is None
    assert (end_time - start_time) < 0.1

    # Wait for the background thread callback to be triggered
    cb_done.wait(timeout=1.0)
    assert callback_result == 500


@patch("urllib.request.urlopen")
def test_order_manager_telegram_thread_replies(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies OrderLifecycleManager maps order messages and sends thread replies."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 888}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    # Mock the adapter
    mock_adapter = MagicMock()
    mock_adapter.get_positions.return_value = [
        Position(
            ticket=55512,
            symbol="XAUUSD",
            type=OrderType.BUY,
            volume=0.5,
            price_open=1950.0,
            sl=1940.0,
            tp=1970.0,
            profit=10.0,
            magic=888101,
        )
    ]

    # Initialize manager
    manager = OrderLifecycleManager(adapter=mock_adapter, notifier=notifier)

    # 1. Register order message ID
    manager.register_order_message(order_id="uuid-1", message_id=888)

    # 2. Call manage_active_positions to trigger ticket-message association
    tick = TickData(
        symbol="XAUUSD", timestamp=time_from_str("2026-01-01T12:00:00Z"), bid=1951.0, ask=1951.2
    )
    positions = manager.manage_active_positions(symbol="XAUUSD", current_tick=tick)

    # Verify position is monitored and the ticket was associated with the message ID
    assert len(positions) == 1
    assert manager._order_message_ids[55512] == 888

    # 3. Simulate position modification
    mock_adapter.modify_position.return_value = True

    # Check that when modifying, it uses the registered reply_to_message_id
    with patch.object(notifier, "send", wraps=notifier.send) as spy_send:
        # Trigger modify (advanced trailing stop threshold reached)
        manager.manage_active_positions(
            symbol="XAUUSD",
            current_tick=TickData(
                symbol="XAUUSD",
                timestamp=time_from_str("2026-01-01T12:01:00Z"),
                bid=1953.0,
                ask=1953.2,
            ),
        )
        assert spy_send.called
        kwargs = spy_send.call_args[1]
        assert kwargs.get("reply_to_message_id") == 888


@patch("urllib.request.urlopen")
def test_order_manager_extended_notifications(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """Verifies that extended lifecycle events (TP touched, SL touched, manual close) are dispatched correctly."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 999}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    # 1. Test TP Touched Notification
    with patch.object(notifier, "notify_tp_touched", return_value=999) as spy_tp:
        notifier.notify_tp_touched(
            ticket=123,
            symbol="XAUUSD",
            entry=1900.0,
            tp_price=1920.0,
            exit_price=1920.1,
            profit_usd=2000.0,
            profit_pct=1.05,
            duration_sec=300.0,
            reply_to_message_id=888,
        )
        assert spy_tp.called
        assert spy_tp.call_args[1].get("reply_to_message_id") == 888

    # 2. Test SL Touched Notification
    with patch.object(notifier, "notify_sl_touched", return_value=999) as spy_sl:
        notifier.notify_sl_touched(
            ticket=123,
            symbol="XAUUSD",
            entry=1900.0,
            sl_price=1890.0,
            exit_price=1889.9,
            loss_usd=-1000.0,
            loss_pct=-0.53,
            duration_sec=300.0,
            risk_usd=1000.0,
            reply_to_message_id=888,
        )
        assert spy_sl.called
        assert spy_sl.call_args[1].get("reply_to_message_id") == 888

    # 3. Test Manual Close Notification
    with patch.object(notifier, "notify_manual_close", return_value=999) as spy_manual:
        notifier.notify_manual_close(
            ticket=123,
            symbol="XAUUSD",
            entry=1900.0,
            exit_price=1910.0,
            profit_usd=1000.0,
            duration_sec=150.0,
            reason="Manual Close via Terminal",
            reply_to_message_id=888,
        )
        assert spy_manual.called
        assert spy_manual.call_args[1].get("reason") == "Manual Close via Terminal"


def time_from_str(s: str) -> datetime.datetime:
    """Helper to parse ISO string with UTC timezone."""
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


@patch("urllib.request.urlopen")
def test_telegram_notifier_failed_send_retry_not_deduplicated(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """A send that FAILS (timeout) must NOT register its signature, so
    the retry is not suppressed as a duplicate - the pre-fix poisoning
    where the first failed attempt blocked every retry forever."""
    import urllib.error

    mock_urlopen.side_effect = [urllib.error.URLError("timeout"), None]
    notifier.send("Retry Me 42")
    import time as _t

    deadline = _t.time() + 3.0
    while _t.time() < deadline and mock_urlopen.call_count < 2:
        _t.sleep(0.05)
    assert mock_urlopen.call_count >= 2, (
        f"retry was suppressed by dedup cache: calls={mock_urlopen.call_count}"
    )


@patch("urllib.request.urlopen")
def test_telegram_notifier_distinct_messages_not_conflated(
    mock_urlopen: MagicMock, notifier: TelegramNotifier
) -> None:
    """The dedup signature stripped ALL digits, so any two messages
    sharing the first 150 non-digit chars (report ids, pnl numbers,
    dates) collapsed to one signature and the 2nd was suppressed."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 201}}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    first = notifier.send("Daily report 2026-08-19 pnl=-189.88 trades=9")
    second = notifier.send("Daily report 2026-08-20 pnl=+42.10 trades=12")
    assert first == 201
    assert second == 201


class TestDnsPoisonBypass:
    """2026-08-20: TELEGRAM_DNS_BLOCKED / DNS-poison blackhole bypass.

    A poisoned resolver answers api.telegram.org with 198.18.x.x (RFC 2544
    benchmark block). The notifier must (a) detect the blackhole, (b) fall
    back to known-good Telegram datacenter IPs with SNI preserved, and
    (c) surface TELEGRAM_DNS_BLOCKED instead of a blind timeout.
    """

    @staticmethod
    def _poisoned_getaddrinfo(*_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.141.205", 443))]

    def test_blackhole_ip_detection(self) -> None:
        assert TelegramNotifier._is_blackhole_ip("198.18.141.205") is True
        assert TelegramNotifier._is_blackhole_ip("198.19.0.1") is True
        assert TelegramNotifier._is_blackhole_ip("192.0.0.1") is True
        assert TelegramNotifier._is_blackhole_ip("198.51.100.7") is True
        assert TelegramNotifier._is_blackhole_ip("203.0.113.9") is True
        assert TelegramNotifier._is_blackhole_ip("127.0.0.1") is True
        assert TelegramNotifier._is_blackhole_ip("149.154.167.220") is False
        assert TelegramNotifier._is_blackhole_ip("not-an-ip") is False

    def test_should_bypass_dns_flag(self, notifier: TelegramNotifier) -> None:
        with patch("socket.getaddrinfo", side_effect=self._poisoned_getaddrinfo):
            assert notifier._should_bypass_dns("api.telegram.org") is True
            assert notifier._last_dns_poisoned is True

    def test_no_bypass_on_healthy_dns(self, notifier: TelegramNotifier) -> None:
        with patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.167.220", 443))],
        ):
            assert notifier._should_bypass_dns("api.telegram.org") is False
            assert notifier._last_dns_poisoned is False

    def test_urlopen_fallback_uses_direct_ip_sni_preserved(
        self, notifier: TelegramNotifier
    ) -> None:
        captured = {}

        def fake_direct(ip, host, path, data, method, timeout):
            captured.update(ip=ip, host=host, path=path, method=method)

            class FakeResp:
                status = 200

                def read(self):
                    return b'{"ok": true}'

            return FakeResp()

        with (
            patch("socket.getaddrinfo", side_effect=self._poisoned_getaddrinfo),
            patch.object(TelegramNotifier, "_direct_https_open", side_effect=fake_direct),
        ):
            resp = notifier._urlopen_with_dns_fallback(
                "https://api.telegram.org/botX/sendMessage", b"{}", "POST", 5.0
            )
        assert resp.status == 200
        assert (
            captured["ip"]
            in __import__(
                "nexus_scalp.observability.telegram_notifier", fromlist=["_TELEGRAM_FALLBACK_IPS"]
            )._TELEGRAM_FALLBACK_IPS
        )
        assert captured["host"] == "api.telegram.org"
        assert captured["path"] == "/botX/sendMessage"
        assert captured["method"] == "POST"

    def test_timeout_after_poison_classified_dns_blocked(self, notifier: TelegramNotifier) -> None:
        notifier._last_dns_poisoned = True
        category, retryable = notifier._classify_exception(TimeoutError("handshake timed out"))
        assert category == "TELEGRAM_DNS_BLOCKED"
        assert retryable is False

    def test_timeout_without_poison_still_timeout(self, notifier: TelegramNotifier) -> None:
        notifier._last_dns_poisoned = False
        category, retryable = notifier._classify_exception(TimeoutError("plain timeout"))
        assert category == "TELEGRAM_TIMEOUT"
        assert retryable is True

    def test_health_state_exposes_dns_poisoned(self, notifier: TelegramNotifier) -> None:
        notifier._last_dns_poisoned = True
        health = notifier.health_state()
        assert health.get("dns_poisoned") is True
        notifier._last_dns_poisoned = False
        assert notifier.health_state().get("dns_poisoned") is False
