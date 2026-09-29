"""BUG-304 regression: AI analysis and Telegram delivery must be distinct truths."""

from __future__ import annotations

import json
import threading

from nexus_scalp.observability import ci_ai_triage as triage


def test_notify_triage_marks_delivery_false_when_telegram_fails(tmp_path, monkeypatch):
    results = tmp_path / "ci-results"
    (results / "run-info").mkdir(parents=True)
    evidence = results / "run-info" / "evidence.json"
    evidence.write_text(
        json.dumps({"checks": {"dependency_drift": "failed"}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("AI_HOST", raising=False)
    monkeypatch.delenv("AI_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        def context(self):
            return type(
                "Ctx",
                (),
                {"repository": "r", "workflow": "w", "run_id": "1"},
            )()

        def _send_text(self, *args, **kwargs):
            return {"ok": False, "category": "SEND_FAILED"}

    monkeypatch.setattr(
        "nexus_scalp.observability.ci_telegram_reporter.CITelegramReporter",
        FakeReporter,
    )

    out = triage.notify_triage(
        "ci-failure",
        results,
        context_path=evidence,
        send_telegram=True,
    )

    assert out["telegram"]["ok"] is False
    assert out["telegram"]["category"] == "SEND_FAILED"
    assert out["result"]["delivered"] is False


def test_notify_triage_marks_delivery_true_only_after_success(tmp_path, monkeypatch):
    results = tmp_path / "ci-results"
    (results / "run-info").mkdir(parents=True)
    evidence = results / "run-info" / "evidence.json"
    evidence.write_text(
        json.dumps({"checks": {"dependency_drift": "failed"}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("AI_HOST", raising=False)
    monkeypatch.delenv("AI_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        def context(self):
            return type(
                "Ctx",
                (),
                {"repository": "r", "workflow": "w", "run_id": "1"},
            )()

        def _send_text(self, *args, **kwargs):
            return {"ok": True, "category": "DELIVERED", "message_ids": [1]}

    monkeypatch.setattr(
        "nexus_scalp.observability.ci_telegram_reporter.CITelegramReporter",
        FakeReporter,
    )

    out = triage.notify_triage(
        "ci-failure",
        results,
        context_path=evidence,
        send_telegram=True,
    )

    assert out["telegram"]["ok"] is True
    assert out["result"]["delivered"] is True

def test_telegram_send_and_wait_accepts_delayed_success(monkeypatch):
    from nexus_scalp.observability.telegram_notifier import TelegramNotifier

    notifier = object.__new__(TelegramNotifier)

    def fake_send(*args, **kwargs):
        callback = kwargs["callback"]
        timer = threading.Timer(0.08, callback, args=(123,))
        timer.start()
        return None

    monkeypatch.setattr(notifier, "send", fake_send)
    result = notifier.send_and_wait("payload", wait_timeout_seconds=1.0)

    assert result["ok"] is True
    assert result["category"] == "DELIVERED"
    assert result["message_id"] == 123


def test_telegram_send_and_wait_reports_terminal_failure():
    from nexus_scalp.observability.telegram_notifier import TelegramNotifier

    notifier = object.__new__(TelegramNotifier)

    done = threading.Event()
    callback_result = {"message_id": None}

    def fake_send(*args, **kwargs):
        callback = kwargs["callback"]

        def fail_later():
            callback(callback_result["message_id"])

        threading.Timer(0.01, fail_later).start()
        return None

    notifier.send = fake_send  # type: ignore[method-assign]
    notifier.health_state = lambda: {"failure_category": "TELEGRAM_AUTH_ERROR"}  # type: ignore[method-assign]
    result = notifier.send_and_wait("payload", wait_timeout_seconds=1.0)

    done.set()
    assert result["ok"] is False
    assert result["category"] == "TELEGRAM_AUTH_ERROR"


def test_ci_reporter_uses_terminal_delivery_api(monkeypatch, tmp_path):
    from nexus_scalp.observability import ci_telegram_reporter as reporter_module

    class FakeNotifier:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def send_and_wait(self, *args, **kwargs):
            return {"ok": True, "category": "DELIVERED", "message_id": 7}

    monkeypatch.setattr(reporter_module, "TelegramNotifier", FakeNotifier)
    reporter = reporter_module.CITelegramReporter(
        tmp_path,
        bot_token="bot-test-token",
        chat_id="123",
    )

    result = reporter._send_text("<b>hello</b>", event_type="TEST")

    assert result["ok"] is True
    assert result["message_ids"] == [7]
