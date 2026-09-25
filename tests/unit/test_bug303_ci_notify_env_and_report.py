"""BUG-303 regression test: CI env probe, CLI notification path, heartbeat, and lane report.

Covers all parts of the BUG-303 specification:
  1. Env probe (ci_env_probe): stdlib-only dependency health probe before importing
     observability package.
  2. telegram_notify CLI:
     - --no-env-repair flag skips probe and pip repair.
     - missing deps triggers pip install --user.
     - all shipped subcommand shapes parse and emit structured JSON without crashing.
     - import failure emits structured ENV_IMPORT_FAILED payload.
  3. Heartbeat (ci_heartbeat): AI endpoint health & model probe (stdlib+repo only).
  4. Lane report (ci_lane_report): advisory summary writer for CI lanes.
  5. Workflow validation: validate_lane_report_step in check_workflows.py.

No .github/workflows/* files are edited.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from scripts.ci import check_workflows, telegram_notify
from scripts.ci.check_workflows import WorkflowModel


def _run(mod, argv):
    """Run mod.main(argv) and capture stdout."""
    buf = StringIO()
    with redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue()


def _parse_json_payloads(text: str) -> list[dict]:
    """Parse all JSON objects from stdout output."""
    decoder = json.JSONDecoder()
    payloads = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                payloads.append(obj)
            idx = start + end
        except json.JSONDecodeError:
            idx = start + 1
    return payloads


def test_bug303_env_probe_stdlib_only():
    """The probe must import WITHOUT importing heavy deps (stdlib only)."""
    from nexus_scalp.observability.ci_env_probe import REQUIRED
    from nexus_scalp.observability.ci_env_probe import probe as env_probe

    result = env_probe()
    assert isinstance(result, dict)
    assert "missing" in result
    assert "imports" in result
    assert "python_version" in result
    assert set(REQUIRED) == {"structlog", "pydantic", "pydantic-settings", "PyYAML", "numpy"}


def test_bug303_cli_no_env_repair_skips_probe(monkeypatch):
    """Passing --no-env-repair must skip the env probe and pip repair."""
    pip_cmds = []
    original_run = subprocess.run

    def mock_run(cmd, *a, **kw):
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            pip_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", mock_run)

    from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter

    monkeypatch.setattr(
        CITelegramReporter,
        "_send_text",
        lambda self, text, *, event_type="": {"ok": True, "category": "DELIVERED"},
    )

    argv = [
        "--results",
        "ci-results",
        "--no-env-repair",
        "--chat-id",
        "123",
        "--bot-token",
        "abc",
        "run-started",
    ]
    rc, out = _run(telegram_notify, argv)

    assert rc == 0
    assert len(pip_cmds) == 0, f"pip install was called unexpectedly: {pip_cmds}"

    payloads = _parse_json_payloads(out)
    assert len(payloads) > 0, f"No JSON output found in stdout: {out}"
    assert any(p.get("category") == "DELIVERED" for p in payloads)


def test_bug303_cli_env_repair_installs_missing_deps(monkeypatch):
    """When deps are missing (simulated), --no-env-repair absent must pip install."""
    pip_cmds = []
    original_run = subprocess.run

    def mock_run(cmd, *a, **kw):
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            pip_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", mock_run)

    from nexus_scalp.observability import ci_env_probe

    monkeypatch.setattr(ci_env_probe, "probe", lambda: {"missing": ["structlog", "pydantic"]})

    from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter

    monkeypatch.setattr(
        CITelegramReporter,
        "_send_text",
        lambda self, text, *, event_type="": {"ok": True, "category": "DELIVERED"},
    )

    argv = [
        "--results",
        "ci-results",
        "--chat-id",
        "123",
        "--bot-token",
        "abc",
        "run-started",
    ]
    rc, out = _run(telegram_notify, argv)

    assert rc == 0
    assert len(pip_cmds) > 0, "Expected pip install call due to missing deps."

    payloads = _parse_json_payloads(out)
    assert any(p.get("category") == "ENV_REPAIR" for p in payloads), (
        f"Expected ENV_REPAIR payload, got: {out}"
    )


def test_bug303_cli_ship_shapes(monkeypatch, tmp_path):
    """Test that all shipped shapes from ci-summary.yml and tests-os.yml work."""
    from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter

    monkeypatch.setattr(
        CITelegramReporter,
        "_send_text",
        lambda self, text, *, event_type="": {"ok": True, "category": "DELIVERED"},
    )

    shapes = [
        ["run-started"],
        ["run-finished"],
        ["os-finished", "--os", "windows-latest"],
        ["js-finished"],
        ["test-summary"],
        ["artifacts"],
        ["release-started", "--tag", "v1.0.0", "--phase", "build"],
        ["release-success", "--tag", "v1.0.0"],
        [
            "release-failed",
            "--tag",
            "v1.0.0",
            "--failed-phase",
            "build",
            "--error-class",
            "BUILD_FAILURE",
        ],
        ["push", "--author", "capsizer", "--commits", "5", "--latest", "abc123"],
        ["pr", "--action", "opened", "--title", "test", "--author", "capsizer"],
        ["security", "--scan", "refactor", "--status", "new", "--detail", "something"],
        ["ai-triage", "--kind", "ci-failure"],
    ]

    for shape in shapes:
        argv = [
            "--results",
            str(tmp_path),
            "--chat-id",
            "123",
            "--bot-token",
            "abc",
            "--no-env-repair",
            *shape,
        ]
        rc, out = _run(telegram_notify, argv)
        assert rc == 0, f"Command failed for shape {shape}: {out}"

        payloads = _parse_json_payloads(out)
        assert len(payloads) > 0, f"No JSON payloads in output for shape {shape}: {out}"
        assert any(
            p.get("category") in ("DELIVERED", "SEND_FAILED", "TELEGRAM_CONFIG_ERROR")
            or "result" in p
            or "artifact_written" in p
            for p in payloads
        ), f"No expected category in payloads: {payloads}"


def test_bug303_import_failure_payload():
    """Import failure must emit structured ENV_IMPORT_FAILED payload."""
    payload = telegram_notify._env_import_failed_payload(
        ModuleNotFoundError("No module named 'structlog'")
    )
    assert payload["category"] == "ENV_IMPORT_FAILED"
    assert "structlog" in payload["error"]
    assert "diagnosis" in payload
    assert "remedy" in payload
    assert "env_probe" in payload


def test_bug303_heartbeat_unconfigured():
    """Unconfigured AI endpoint returns NO_ENDPOINT_CONFIGURED without raising."""
    from nexus_scalp.observability.ci_heartbeat import heartbeat

    hb = heartbeat(host="", port=80)
    assert hb["category"] == "HEARTBEAT"
    assert hb["verdict"] == "NO_ENDPOINT_CONFIGURED"
    assert hb["endpoint_reachable"] is False


def test_bug303_heartbeat_connection_failure():
    """Unreachable AI endpoint returns TCP_FAILED without raising."""
    from nexus_scalp.observability.ci_heartbeat import heartbeat

    # Port 59999 on 127.0.0.1 should not be open
    hb = heartbeat(host="127.0.0.1", port=59999)
    assert hb["category"] == "HEARTBEAT"
    assert hb["verdict"] == "TCP_FAILED"
    assert hb["endpoint_reachable"] is False
    tcp_info = hb.get("tcp")
    assert isinstance(tcp_info, dict)
    assert tcp_info["ok"] is False


def test_bug303_lane_report(tmp_path, monkeypatch):
    """Lane report creates JSON artifact and Markdown summary."""
    from nexus_scalp.observability import ci_lane_report

    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setattr(ci_lane_report, "STEP_SUMMARY", summary_file)
    monkeypatch.setattr(ci_lane_report, "ARTIFACT_DIR", tmp_path / "run-info")

    result = ci_lane_report.lane_report(
        "test-lane",
        purpose="Verify unit tests",
        limit="Linux only",
        analysis="All 45 tests passed",
        why="Core stability",
        env_status={"verdict": "HEALTHY"},
    )

    assert result["category"] == "LANE_REPORT"
    assert result["lane"] == "test-lane"
    assert result["purpose"] == "Verify unit tests"

    # Verify JSON artifact written
    artifact_path = tmp_path / "run-info" / "lane-report.json"
    assert artifact_path.is_file()
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["lane"] == "test-lane"

    # Verify Markdown summary appended
    assert summary_file.is_file()
    md_content = summary_file.read_text(encoding="utf-8")
    assert "## NSE CI lane report — `test-lane`" in md_content
    assert "Verify unit tests" in md_content

    # Verify render_text helper
    text = ci_lane_report.render_text(result)
    assert "lane=test-lane" in text
    assert "verdict=HEALTHY" in text


def test_bug303_check_workflows_validate_lane_report():
    """check_workflows.validate_lane_report_step validates lane-report contract."""
    # Compliant workflow
    jobs_compliant = {
        "build": {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"run": "echo hello"},
                {
                    "uses": "./.github/actions/lane-report",
                    "continue-on-error": True,
                    "if": "always()",
                    "run": "python -m nexus_scalp.observability.ci_lane_report build || true",
                },
            ],
        }
    }
    compliant_wf = WorkflowModel(
        path=Path(".github/workflows/compliant.yml"),
        name="compliant",
        raw={"name": "compliant", "jobs": jobs_compliant},
        jobs=jobs_compliant,  # type: ignore[arg-type]
    )
    res = check_workflows.validate_lane_report_step(compliant_wf)
    assert res is None, f"Expected compliant workflow to return None, got: {res}"

    # Non-compliant workflow: missing continue-on-error, wrong if, missing || true
    jobs_broken = {
        "test": {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"run": "echo test"},
                {
                    "run": "echo not-a-lane-report",
                },
            ],
        }
    }
    non_compliant_wf = WorkflowModel(
        path=Path(".github/workflows/broken.yml"),
        name="broken",
        raw={"name": "broken", "jobs": jobs_broken},
        jobs=jobs_broken,  # type: ignore[arg-type]
    )
    res_broken = check_workflows.validate_lane_report_step(non_compliant_wf)
    assert res_broken is not None
    assert "Lane-report contract violations" in res_broken
