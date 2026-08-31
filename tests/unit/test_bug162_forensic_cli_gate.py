"""BUG-162 regression: restore the ``nexus forensic`` CLI command + gate-hook fail-safe contract.

Root cause (proven by Hermes-Main, see agents/bugs.md BUG-162): commit 999276c
deleted the ``@app.command("forensic")`` command from
``src/nexus_scalp/cli/main.py``. Both quality-gate hooks
(beforePush.sh step 5 / beforePush.ps1 step 7) call
``python -m nexus_scalp.cli.main forensic --deploy-gate --json``; with the
command gone typer exits 2 (usage error) and the shell hook treated exit 2 as
"REVIEW REQUIRED" and still printed ALL CHECKS PASSED — a fail-open gate. The
deploy-gate contract (src/nexus_scalp/forensics/deploy_gate.py §39) requires
engine-unavailable / unusable gate output to be a FAIL-SAFE BLOCK (exit 3).

Exit-code contract (deploy-gate):
    0 = ALLOW / ALLOW_WITH_WARNING, 1 = BLOCK, 2 = REVIEW_REQUIRED,
    3 = FORENSIC_ENGINE_UNAVAILABLE (fail-safe block).

These tests fail on pre-fix HEAD (command missing → typer usage error) and
pass after restoration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_GATE_EXITS = {0, 1, 2, 3}
VALID_DECISIONS = {
    "ALLOW",
    "ALLOW_WITH_WARNING",
    "BLOCK",
    "REVIEW_REQUIRED",
    "FORENSIC_ENGINE_UNAVAILABLE",
}


def _extract_json(text: str) -> dict:
    """Extract the JSON document from output that may carry preamble noise
    (e.g. structlog/log lines) — first ``{`` that starts a parseable doc."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("{"):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"no JSON document in output: {text[:300]!r}")


# ---------------------------------------------------------------------------
# (c) hook contract: the command must exist in the CLI inventory
# ---------------------------------------------------------------------------


def test_forensic_command_in_cli_inventory() -> None:
    """``forensic`` must be a registered command (hook calls it by name).

    Pre-fix HEAD: typer raises 'No such command' usage error, exit 2.
    """
    res = runner.invoke(app, ["forensic", "--help"])
    out = res.stdout or ""
    assert res.exit_code == 0, (
        f"BUG-162: 'forensic' not a valid CLI command (exit={res.exit_code}): {out[:300]}"
    )
    assert "No such command" not in out
    # hook-contract flags must be advertised
    for flag in ("--snapshot", "--deploy-gate", "--trend", "--gap", "--report", "--json"):
        assert flag in out, f"missing hook-contract option {flag} in forensic --help"


def test_forensic_json_only_does_not_raise_typer_usage_error() -> None:
    """Invoking with --json only must not raise a typer 'No such command'
    (i.e. 'forensic' must be in the CLI inventory)."""
    # direct inventory assertion — deterministic, no stderr dependence
    names = {cmd.name or cmd.callback.__name__ for cmd in app.registered_commands}
    assert "forensic" in names, (
        f"BUG-162 regression: 'forensic' missing from CLI inventory: {sorted(names)[:40]}"
    )
    res = runner.invoke(app, ["forensic", "--json"])
    out = res.stdout or ""
    assert "No such command" not in out, (
        f"BUG-162 regression: forensic command missing: {out[:300]}"
    )
    # default mode runs the read-only dashboard; must honor the contract set,
    # and must NOT be a bare usage error (stderr carries 'No such command' pre-fix)
    assert res.exit_code in {0, 1, 2, 3}
    if res.exit_code == 2:
        # a real contract exit 2 (REVIEW_REQUIRED) emits a JSON payload;
        # a typer usage error emits none — reject the latter
        payload = _extract_json(out)
        assert "decision" in payload or "overall" in payload


# ---------------------------------------------------------------------------
# (a) CliRunner deploy-gate contract
# ---------------------------------------------------------------------------


def test_forensic_deploy_gate_json_contract_clirunner() -> None:
    """``forensic --deploy-gate --json`` → exit in {0,1,2,3} and stdout parses
    as JSON containing 'decision' and 'exit_code' keys."""
    res = runner.invoke(app, ["forensic", "--deploy-gate", "--json"])
    out = res.stdout or ""
    assert res.exit_code in VALID_GATE_EXITS, (
        f"BUG-162: deploy-gate exit {res.exit_code} outside contract {{0,1,2,3}}: {out[:300]}"
    )
    payload = _extract_json(out)
    assert "decision" in payload, f"gate payload missing 'decision': {out[:300]}"
    assert "exit_code" in payload, f"gate payload missing 'exit_code': {out[:300]}"
    assert payload["decision"] in VALID_DECISIONS, payload.get("decision")
    assert payload["exit_code"] == res.exit_code, (
        f"exit_code {payload['exit_code']} != process exit {res.exit_code}"
    )


# ---------------------------------------------------------------------------
# (b) subprocess-level hook-equivalent invocation (bounded)
# ---------------------------------------------------------------------------


def test_forensic_deploy_gate_json_contract_subprocess(tmp_path) -> None:
    """Subprocess: ``python -m nexus_scalp.cli.main forensic --deploy-gate --json``
    → exit in {0,1,2,3}, stdout parses as JSON with decision/exit_code."""
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_scalp.cli.main", "forensic", "--deploy-gate", "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout or ""
    assert proc.returncode in VALID_GATE_EXITS, (
        f"BUG-162: subprocess exit {proc.returncode} outside contract {{0,1,2,3}}: "
        f"{out[:300]}{proc.stderr[:300]}"
    )
    payload = _extract_json(out)
    assert "decision" in payload, f"subprocess payload missing 'decision': {out[:300]}"
    assert "exit_code" in payload, f"subprocess payload missing 'exit_code': {out[:300]}"
    assert payload["decision"] in VALID_DECISIONS, payload.get("decision")
    assert payload["exit_code"] == proc.returncode


# ---------------------------------------------------------------------------
# hook fail-safe hardening: gate artifacts must carry a real decision payload
# (guards the fail-open regression where a typer usage error masqueraded as
# REVIEW_REQUIRED and the hook still printed ALL CHECKS PASSED)
# ---------------------------------------------------------------------------


def test_deploy_gate_result_artifact_has_decision_payload(tmp_path) -> None:
    """The persisted gate artifact (deploy_gate_result.json) must contain a
    '"decision"' field — a typer usage-error panel is a fail-safe BLOCK."""
    artifact = REPO_ROOT / "artifacts" / "forensics" / "deploy_gate_result.json"
    if not artifact.exists():
        pytest.skip("artifacts/forensics/deploy_gate_result.json not present in this checkout")
    raw = artifact.read_text(encoding="utf-8", errors="replace")
    assert '"decision"' in raw, (
        "BUG-162 fail-open signature: gate artifact lacks a decision payload "
        "(typer usage error masqueraded as gate output)"
    )
