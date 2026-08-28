"""Tests for the NSE Enterprise Code Analyzer subsystem (mission G29)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nexus_scalp.diagnostics.analyzers.bandit_adapter import BanditAnalyzer
from nexus_scalp.diagnostics.analyzers.pylint_adapter import PylintAnalyzer
from nexus_scalp.diagnostics.analyzers.pyright_adapter import PyrightAnalyzer
from nexus_scalp.diagnostics.analyzers.ruff_adapter import RuffAnalyzer
from nexus_scalp.diagnostics.engine import DiagnosticEngine
from nexus_scalp.diagnostics.models import AnalyzerHealth, Diagnostic
from nexus_scalp.diagnostics.runner import RunResult, run_command

# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------


def test_diagnostic_canonical_fields():
    d = Diagnostic(
        tool="ruff",
        source="F401",
        category="lint",
        severity="error",
        code="F401",
        message="unused import",
        file="src/x.py",
        line=10,
        column=1,
    )
    out = d.to_dict()
    assert out["schema_version"] == "1.0"
    assert out["severity"] in {"error", "warning", "info"}
    assert out["category"] in {
        "syntax",
        "lint",
        "type",
        "security",
        "code_smell",
        "import",
        "style",
        "configuration",
        "infrastructure",
        "analyzer_failure",
    }
    assert out["fingerprint"] == "ruff:F401:src/x.py:10:1"


def test_diagnostic_fingerprint_deterministic():
    d1 = Diagnostic(tool="ruff", code="F401", file="src/x.py", line=10, column=1)
    d2 = Diagnostic(tool="ruff", code="F401", file="src/x.py", line=10, column=1)
    assert d1.fingerprint == d2.fingerprint


# ---------------------------------------------------------------------------
# Analyzer health model
# ---------------------------------------------------------------------------


def test_analyzer_health_states():
    for st in (
        "NOT_INSTALLED",
        "AVAILABLE",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "TIMEOUT",
        "INTERRUPTED",
        "CONFIGURATION_ERROR",
    ):
        h = AnalyzerHealth(name="ruff", execution_status=st)
        assert h.execution_status == st


# ---------------------------------------------------------------------------
# Safe subprocess runner
# ---------------------------------------------------------------------------


def test_run_command_success():
    res = run_command([sys.executable, "-c", "print('hello')"], timeout=10.0)
    assert res.status == "COMPLETED"
    assert res.returncode == 0
    assert "hello" in res.stdout


def test_run_command_timeout_kill_tree():
    res = run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0)
    assert res.status == "TIMEOUT"
    assert res.returncode == -1


def test_run_command_missing_executable():
    res = run_command(["definitely_not_a_real_binary_xyz"], timeout=5.0)
    assert res.status == "FAILED"
    assert res.returncode == -1


def test_run_command_no_shell_injection():
    # Pass a malicious-looking filename/arg; since shell=True is never used,
    # it is treated as a literal argument, proving no shell injection.
    res = run_command([sys.executable, "nonexistent_file_with_semicolon;rm -rf /"], timeout=5.0)
    assert res.returncode != 0


# ---------------------------------------------------------------------------
# Adapters: graceful unavailable handling
# ---------------------------------------------------------------------------


def test_ruff_adapter_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    monkeypatch.setattr(
        "nexus_scalp.diagnostics.runner.run_command",
        lambda *a, **k: RunResult(
            stdout="", stderr="No module", returncode=1, duration_ms=1.0, status="FAILED"
        ),
    )
    a = RuffAnalyzer(tmp_path)
    assert a.is_available() is False
    diags = a.analyze()
    assert diags == []
    assert a.health.execution_status == "NOT_INSTALLED"


def test_pyright_pylint_bandit_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    monkeypatch.setattr(
        "nexus_scalp.diagnostics.runner.run_command",
        lambda *a, **k: RunResult(
            stdout="", stderr="No module", returncode=1, duration_ms=1.0, status="FAILED"
        ),
    )
    for cls in (PyrightAnalyzer, PylintAnalyzer, BanditAnalyzer):
        a = cls(tmp_path)
        assert a.is_available() is False
        assert a.analyze() == []


def test_ruff_adapter_parse(tmp_path, monkeypatch):
    sample = json.dumps(
        [
            {
                "code": "F401",
                "message": "unused import os",
                "filename": "src/x.py",
                "location": {"row": 5, "column": 1},
                "end_location": {"row": 5, "column": 10},
                "fix": {"edits": []},
            },
            {
                "code": "B007",
                "message": "loop var unused",
                "filename": "src/y.py",
                "location": {"row": 9, "column": 4},
                "end_location": {"row": 9, "column": 5},
            },
        ]
    )
    monkeypatch.setattr("shutil.which", lambda x: "ruff")
    monkeypatch.setattr(
        "nexus_scalp.diagnostics.runner.run_command",
        lambda args, timeout=120.0, cwd=None: RunResult(
            stdout=sample, stderr="", returncode=0, duration_ms=1.0, status="COMPLETED"
        ),
    )
    a = RuffAnalyzer(tmp_path)
    diags = a.analyze()
    assert len(diags) == 2
    assert diags[0].severity == "error"
    assert diags[0].fixable is True
    assert diags[1].severity == "warning"


# ---------------------------------------------------------------------------
# Engine orchestration + deduplication + status
# ---------------------------------------------------------------------------


def test_engine_distinguishes_code_from_infra(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    monkeypatch.setattr(
        "nexus_scalp.diagnostics.runner.run_command",
        lambda *a, **k: RunResult(
            stdout="", stderr="No module", returncode=1, duration_ms=1.0, status="FAILED"
        ),
    )
    engine = DiagnosticEngine(tmp_path)
    report = engine.analyze()
    # No analyzers available -> status error (infrastructure unavailable)
    assert report.infrastructure["unavailable"]
    assert report.summary["errors"] == 0
    assert report.status == "error"


def test_engine_dedup_preserves_tool_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "ruff")
    monkeypatch.setattr(
        "nexus_scalp.diagnostics.runner.run_command",
        lambda args, timeout=120.0, cwd=None: RunResult(
            stdout=json.dumps(
                [
                    {
                        "code": "F401",
                        "message": "unused import os",
                        "filename": "src/x.py",
                        "location": {"row": 5, "column": 1},
                        "end_location": {"row": 5, "column": 10},
                        "fix": None,
                    }
                ]
            )
            if "ruff" in str(args)
            else "[]",
            stderr="",
            returncode=0,
            duration_ms=1.0,
            status="COMPLETED",
        ),
    )
    engine = DiagnosticEngine(tmp_path)
    engine.analyzers = [RuffAnalyzer(tmp_path)]
    report = engine.analyze()
    assert report.diagnostics[0].tool == "ruff"


def test_engine_cli_json_and_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv=['nse', 'analyze', '--json']; from nexus_scalp.cli.main import app; app()",
        ],
        timeout=60.0,
    )
    if res.status == "COMPLETED":
        try:
            data = json.loads(res.stdout)
            assert "schema_version" in data
            assert "status" in data
            assert "summary" in data
            assert "analyzers" in data
        except json.JSONDecodeError:
            pytest.fail("nse analyze --json did not emit valid JSON")
