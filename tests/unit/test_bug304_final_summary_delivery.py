"""BUG-304 regression: AI analysis and Telegram delivery must be distinct truths."""

from __future__ import annotations

import json

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
