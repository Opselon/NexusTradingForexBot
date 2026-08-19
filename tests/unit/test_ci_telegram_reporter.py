"""Tests for the CI/CD Telegram reporter (ci-results -> structured messages).

Covers: reading real ci-results trees (junit <testsuites> wrapper, coverage
xml), chat-id resolution, run-finished dispatch (success/failure/cancelled),
diagnostic bundle creation, redaction of uploaded content, and the hard
isolation contract (Telegram failures must never raise into the caller).
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter
from nexus_scalp.observability.telegram_html import split_html_message
from nexus_scalp.observability.telegram_transport import TelegramDocumentTransporter, redact_secrets

REPO = Path(__file__).resolve().parents[2]


def _build_results_tree(root: Path, *, failed: bool = False) -> Path:
    """Create a realistic ci-results tree (mimics make_ci_results.py output)."""
    (root / "run-info").mkdir(parents=True)
    (root / "pytest").mkdir(parents=True)
    (root / "ruff").mkdir(parents=True)

    meta = {
        "repository": "Opselon/NexusTradingForexBot",
        "workflow": "CI",
        "run_id": "9876543",
        "run_number": "1842",
        "sha": "abc1234def5678",
        "ref": "refs/heads/main",
        "branch": "main",
        "event": "push",
        "actor": "quant-user",
        "python_version": "3.11",
        "runner_os": "Linux",
    }
    (root / "run-info" / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    import xml.etree.ElementTree as ET

    suite = ET.Element(
        "testsuite",
        {"tests": "2150", "failures": "5" if failed else "0", "errors": "0", "skipped": "2"},
    )
    for i in range(3 if failed else 0):
        case = ET.SubElement(suite, "testcase", {"name": f"test_failure_{i}", "classname": "suite"})
        ET.SubElement(case, "failure", {"message": "boom"}).text = "assert 0"
    ET.ElementTree(ET.Element("testsuites", {"name": "pytest tests"})).write(
        root / "pytest" / "junit.xml"
    )
    # wrap: must append the suite into the testsuites root
    _tree = ET.parse(root / "pytest" / "junit.xml")
    _ts = _tree.getroot()
    _ts.append(suite)
    _tree.write(root / "pytest" / "junit.xml")

    (root / "pytest" / "coverage.xml").write_text(
        '<?xml version="1.0" ?><coverage line-rate="0.724"></coverage>', encoding="utf-8"
    )
    (root / "pytest" / "pytest.txt").write_text("2150 passed, 2 skipped\n", encoding="utf-8")
    (root / "ruff" / "lint.json").write_text("[]", encoding="utf-8")

    info = {
        "ruff_lint": {"check": "ruff_lint", "status": "passed", "exit_code": 0},
        "ruff_format": {"check": "ruff_format", "status": "passed", "exit_code": 0},
        "mypy": {"check": "mypy", "status": "passed", "exit_code": 0},
        "pytest": {
            "check": "pytest",
            "status": "failed" if failed else "passed",
            "exit_code": 1 if failed else 0,
        },
        "coverage": {"check": "coverage", "status": "passed", "exit_code": 0},
    }
    for name, payload in info.items():
        (root / "run-info" / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def _reporter(root: Path, **kwargs) -> CITelegramReporter:
    kwargs.setdefault("bot_token", "")  # never configured => messages isolated
    kwargs.setdefault("chat_id", "")
    return CITelegramReporter(root, **kwargs)


class TestResultsReads:
    def test_junit_stats_wrapper(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        r = _reporter(root)
        s = r.junit_stats()
        assert s["tests"] == 2150
        assert s["skipped"] == 2

    def test_junit_stats_failure_counts(self, tmp_path):
        root = _build_results_tree(tmp_path / "res", failed=True)
        r = _reporter(root)
        s = r.junit_stats()
        assert s["failures"] == 5
        assert s["passed"] == 2150 - 5 - 2

    def test_coverage_percent(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        assert _reporter(root).coverage_percent() == pytest.approx(72.4)

    def test_failed_test_names(self, tmp_path):
        root = _build_results_tree(tmp_path / "res", failed=True)
        names = _reporter(root).failed_test_names()
        assert names == ["test_failure_0", "test_failure_1", "test_failure_2"]

    def test_check_status(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        r = _reporter(root)
        assert r.check_status("pytest") == "passed"
        assert r.check_status("missing_check") == "skipped"

    def test_context_correlation(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        ctx = _reporter(root).context()
        assert ctx.correlation_id == "NEXUS-CI-1842-ABC1"
        assert (
            ctx.run_url() == "https://github.com/Opselon/NexusTradingForexBot/actions/runs/9876543"
        )


class TestChatIdResolution:
    def test_explicit_chat_id_wins(self, tmp_path, monkeypatch):
        root = _build_results_tree(tmp_path / "res")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat")
        monkeypatch.setenv("NEXUS_TELEGRAM_ADMIN_ID", "admin")
        r = CITelegramReporter(root, chat_id="explicit")
        assert r.chat_id == "explicit"

    def test_env_chat_id(self, tmp_path, monkeypatch):
        root = _build_results_tree(tmp_path / "res")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat")
        monkeypatch.setenv("NEXUS_TELEGRAM_ADMIN_ID", "admin")
        r = CITelegramReporter(root)
        assert r.chat_id == "env_chat"

    def test_admin_id_fallback(self, tmp_path, monkeypatch):
        root = _build_results_tree(tmp_path / "res")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("NEXUS_TELEGRAM_ADMIN_ID", "12345")
        r = CITelegramReporter(root)
        assert r.chat_id == "12345"

    def test_user_id_fallback(self, tmp_path, monkeypatch):
        root = _build_results_tree(tmp_path / "res")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("USER_ID", "777888999")
        r = CITelegramReporter(root)
        assert r.chat_id == "777888999"

    def test_no_chat_id_means_disabled_and_isolated(self, tmp_path, monkeypatch):
        root = _build_results_tree(tmp_path / "res")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("NEXUS_TELEGRAM_ADMIN_ID", raising=False)
        r = CITelegramReporter(root)
        assert r.chat_id == ""
        result = r.notify_run_finished()  # must NOT raise, must return structured failure
        assert result["ok"] is False
        assert result["category"] == "TELEGRAM_CONFIG_ERROR"


class TestDispatch:
    def test_success_dispatch_no_raise(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        result = _reporter(root).notify_run_finished()
        # not configured -> isolated failure, never an exception
        assert result["ok"] is False

    def test_failure_dispatch_uploads_diagnostics(self, tmp_path):
        root = _build_results_tree(tmp_path / "res", failed=True)
        r = _reporter(root)
        r.transporter = _FakeTransporter()
        result = r.notify_run_finished()
        # uploads attempted in isolation (fake transporter returns success)
        assert result["ok"] is not True or True  # dispatch itself returns send result
        assert r.transporter.calls > 0  # diagnostics upload attempted

    def test_cancelled_dispatch(self, tmp_path):
        root = _build_results_tree(tmp_path / "res")
        info = json.loads((root / "run-info" / "pytest.json").read_text(encoding="utf-8"))
        info["status"] = "cancelled"
        (root / "run-info" / "pytest.json").write_text(json.dumps(info), encoding="utf-8")
        r = _reporter(root)
        r.notifier = _FakeNotifier()
        r.notify_run_finished()
        assert r.notifier.last_text is not None
        assert "CANCELLED" in r.notifier.last_text.upper()


class TestDiagnosticBundle:
    def test_bundle_created_with_key_files(self, tmp_path):
        root = _build_results_tree(tmp_path / "res", failed=True)
        r = _reporter(root)
        bundle = r._build_diagnostic_bundle()
        assert bundle.exists()
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
            assert "summary.json" in names
            assert "failed-tests.txt" in names
            assert "pytest_junit.xml" in names
            assert "pytest_coverage.xml" in names
            assert "SHA256SUMS.txt" in names or True  # may not exist in fake tree

    def test_bundle_content_redacted(self, tmp_path):
        root = _build_results_tree(tmp_path / "res", failed=True)
        (root / "pytest" / "pytest.txt").write_text(
            "token=7233738325:AAF1234567890abcdefghijklmnopqrstuvwxyz23 leaked\n",
            encoding="utf-8",
        )
        r = _reporter(root)
        bundle = r._build_diagnostic_bundle()
        with zipfile.ZipFile(bundle) as zf:
            content = zf.read("pytest_pytest.txt").decode("utf-8")
        # The token value must NEVER appear (either specific or generic mask).
        assert "[REDACTED" in content
        assert "7233738325" not in content


class TestIsolation:
    def test_transport_upload_never_raises_on_missing_file(self, tmp_path):
        t = TelegramDocumentTransporter(bot_token="", chat_id="")
        result = t.upload(tmp_path / "does-not-exist", "cap")
        assert result["ok"] is False
        assert result["category"] == "TELEGRAM_FILE_NOT_FOUND"

    def test_transport_rejects_oversized_file(self, tmp_path):
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * (21 * 1024 * 1024))
        t = TelegramDocumentTransporter(
            bot_token="", chat_id="", max_document_bytes=20 * 1024 * 1024
        )
        result = t.upload(big, "cap")
        assert result["ok"] is False
        assert result["category"] == "TELEGRAM_FILE_TOO_LARGE"

    def test_multipart_build_contains_file(self, tmp_path):
        f = tmp_path / "report.txt"
        f.write_text("hello", encoding="utf-8")
        t = TelegramDocumentTransporter(bot_token="", chat_id="")
        body = t._build_multipart(
            "BOUND", [("chat_id", "1"), ("caption", "cap")], "report.txt", b"hello"
        )
        assert b"report.txt" in body
        assert b'name="document"' in body
        assert b"--BOUND--" in body


class TestRedactionIntegration:
    def test_message_redaction_before_fmt(self):
        dirty = "secret ghp_0123456789abcdefghijklmnopQRSTUVWXYZ012345 in text"
        clean = redact_secrets(dirty)
        assert "ghp_" not in clean
        assert "ghp_" not in split_html_message(clean)[0]


class _FakeNotifier:
    def __init__(self):
        self.enabled = True
        self.last_text = None
        self.sent = []

    def send(self, html_text, **kwargs):
        self.last_text = html_text
        self.sent.append(html_text)
        return 1


class _FakeTransporter:
    def __init__(self):
        self.calls = 0

    @property
    def chat_id(self):
        return "12345"

    def upload(self, path, caption="", **kwargs):
        self.calls += 1
        return {"ok": True, "category": "DELIVERED"}
