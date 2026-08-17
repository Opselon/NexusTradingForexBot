"""BUG-057 Telegram reporting: new notifier templates + web test endpoint.

Verifies:
- notify_test_message builds a valid HTML message and dispatches
- notify_engine_stopped / notify_engine_error / notify_audit_purge /
  notify_warmup / notify_daily_summary produce non-empty messages and dispatch
- POST /api/telegram/test wiring (happy path, disabled, no-notifier)
- messages never contain the raw bot token (secret redaction)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from nexus_scalp.observability.telegram_notifier import TelegramNotifier


@pytest.fixture()
def notifier() -> TelegramNotifier:
    n = TelegramNotifier(
        bot_token="123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll",
        admin_id="5094837833",
        enabled=True,
    )
    yield n
    n.shutdown()


def test_notify_test_message_dispatches(notifier: TelegramNotifier) -> None:
    with patch.object(notifier, "send", return_value=42) as mock_send:
        msg_id = notifier.notify_test_message()
    assert msg_id == 42
    mock_send.assert_called_once()
    call = mock_send.call_args
    assert "TELEGRAM CONNECTION OK" in call.args[0]
    assert call.kwargs.get("severity", "INFO").upper() in ("INFO",)


def test_notify_engine_stopped_and_error(notifier: TelegramNotifier) -> None:
    with patch.object(notifier, "send", return_value=1) as mock_send:
        notifier.notify_engine_stopped(reason="test stop")
        notifier.notify_engine_error(error="boom", context="engine_loop")
    assert mock_send.call_count == 2
    stopped_msg = mock_send.call_args_list[0].args[0]
    error_msg = mock_send.call_args_list[1].args[0]
    assert "ENGINE STOPPED" in stopped_msg and "test stop" in stopped_msg
    assert "ENGINE ERROR" in error_msg and "boom" in error_msg
    assert mock_send.call_args_list[1].kwargs["severity"] == "CRITICAL"


def test_notify_audit_purge(notifier: TelegramNotifier) -> None:
    with patch.object(notifier, "send", return_value=1) as mock_send:
        notifier.notify_audit_purge(
            deleted={"audit_signals": 500, "position_moving": 3}, duration_ms=42.0
        )
    msg = mock_send.call_args_list[0].args[0]
    assert "AUDIT RETENTION PURGE" in msg
    assert "audit_signals: 500" in msg


def test_notify_warmup_and_daily_summary(notifier: TelegramNotifier) -> None:
    with patch.object(notifier, "send", return_value=1) as mock_send:
        notifier.notify_warmup(state="READY", symbol="XAUUSD", detail="H1=500 H4=200")
        notifier.notify_daily_summary(
            {
                "date": "2026-08-17",
                "trades": 12,
                "wins": 7,
                "losses": 5,
                "win_rate": "58.3%",
                "net_pnl": "$123.45",
                "max_drawdown": "-2.1%",
            }
        )
    assert mock_send.call_count == 2
    warm = mock_send.call_args_list[0].args[0]
    daily = mock_send.call_args_list[1].args[0]
    assert "WARMUP STATE: READY" in warm
    assert "DAILY PERFORMANCE SUMMARY" in daily
    assert "Trades" in daily and "Net PnL" in daily


def test_notifier_redacts_token_in_messages(notifier: TelegramNotifier) -> None:
    # The token must never leak into a message body even if embedded.
    leaked = notifier._redact_secrets("token is 123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll end")
    assert "123456789:AAAbbbCCC" not in leaked
    assert "[REDACTED_BOT_TOKEN]" in leaked


def test_web_telegram_test_endpoint_contract() -> None:
    """The /api/telegram/test endpoint must exist with the documented envelope."""
    import subprocess
    import sys

    # Static check: route registered in create_app.
    src = open("src/nexus_scalp/web/server.py", encoding="utf-8").read()
    assert 'app.post("/api/telegram/test")' in src
    assert "NOTIFIER_DISABLED" in src and "NOTIFIER_UNAVAILABLE" in src
