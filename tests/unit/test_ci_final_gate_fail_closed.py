"""Fail-closed contract for the ci.yml quality-job final gate.

Regression net for the silent-skip class reproduced against the real
make_ci_results pipeline (Agent-10 CI/CD gate-integrity audit, 2026-09-11):

The pre-fix final gate (inline heredoc in .github/workflows/ci.yml) read every
run-info/<check>.json and treated a MISSING file as status="skipped", then
failed the job only on failed/errored. Consequence: if mypy/pytest/coverage/
runtime_gate/layered_smoke never ran (lost GITHUB_ENV write behind the
`if: env.RUFF_FORMAT_RC == '0' || ...` gate, runner crash after ruff, a
step-skipped misfire) the run reported "ALL CHECKS PASSED" with exit 0 while
NOT ONE test had executed. Green CI no longer proved anything about the code.

scripts/ci/ci_final_gate.py (extracted from the workflow) is FAIL-CLOSED:
a check with no recorded result fails the job. The only legitimate "never
ran" state is an explicit status=blocked record (per-step fallback or
`make_ci_results.py classify-gate`), which always leaves a JSON file behind.

These tests run the real script against a real ci-results tree in tmp.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "ci" / "ci_final_gate.py"
RESULTS = REPO / "scripts" / "ci" / "make_ci_results.py"

CHECKS = (
    "ruff_lint",
    "ruff_format",
    "mypy",
    "pytest",
    "smoke",
    "coverage",
    "critical_coverage",
    "runtime_gate",
    "layered_smoke",
)


def _py() -> str:
    venv = REPO / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def _run_gate(
    root: Path, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, CI_RESULTS_DIR=str(root))
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [_py(), str(GATE)], env=env, capture_output=True, text=True, cwd=REPO, check=False
    )


def _init_tree(tmp_path: Path) -> Path:
    root = tmp_path / "ci-results"
    subprocess.run(
        [_py(), str(RESULTS), "init", str(root)], capture_output=True, text=True, check=True
    )
    return root


def _record(root: Path, check: str, rc: str, *args: str) -> None:
    subprocess.run(
        [_py(), str(RESULTS), "check", str(root), check, rc, *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_all_checks_recorded_passing_is_green(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    r = _run_gate(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL CHECKS PASSED" in r.stdout


def test_failed_check_fails_gate(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "pytest", "1", "see pytest/junit.xml")
    r = _run_gate(root)
    assert r.returncode == 1
    assert "pytest=failed" in r.stdout
    assert "FAILING CHECKS: pytest" in r.stdout


def test_missing_result_files_fail_the_gate(tmp_path: Path) -> None:
    """THE regression: pre-fix, missing JSONs were 'skipped' and the run went
    green while the checks never executed."""
    root = _init_tree(tmp_path)
    _record(root, "ruff_lint", "0")  # the only check that ran
    # mypy/pytest/coverage/... never executed and were never classified
    r = _run_gate(root)
    assert r.returncode == 1, "missing results MUST fail the gate (fail-closed)"
    assert "pytest=missing" in r.stdout
    assert "mypy=missing" in r.stdout
    assert "ALL CHECKS PASSED" not in r.stdout


def test_blocked_records_are_legitimate_non_runs(tmp_path: Path) -> None:
    """The CHG-0052 blocked path (format failure cancels downstream) must stay
    a coherent, non-green-but-clearly-root-caused verdict, not a silent skip:
    blocked records exist as files and the ROOT failure (ruff_format) fails
    the run on its own."""
    root = _init_tree(tmp_path)
    _record(root, "ruff_lint", "0")
    _record(root, "ruff_format", "1", "files would be reformatted")
    # classify-gate writes explicit blocked records for the downstream checks
    subprocess.run(
        [
            _py(),
            str(RESULTS),
            "classify-gate",
            str(root),
            "--root-failure",
            "ruff_format",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # smoke/runtime_gate/layered_smoke/critical_coverage also gate on the same
    # if: and also never ran; the per-step fallback records runtime_gate, the
    # classifier covers mypy/pytest/coverage, but any check STILL missing a
    # record keeps the gate red (never green).
    r = _run_gate(root)
    assert r.returncode == 1
    assert "ruff_format=failed" in r.stdout
    assert "BLOCKED" in r.stdout
    assert "ALL CHECKS PASSED" not in r.stdout


def test_unreadable_json_is_errored_not_green(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    (root / "run-info" / "pytest.json").write_text("{corrupt", encoding="utf-8")
    r = _run_gate(root)
    assert r.returncode == 1
    assert "pytest=errored" in r.stdout


def test_gate_script_checks_stay_in_sync_with_workflow(tmp_path: Path) -> None:
    """Every run-info writer in ci.yml must be covered by the final gate's
    CHECKS tuple — a new gate step added to the workflow without updating
    the gate is exactly how the silent-skip class was born."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    import re

    recorded = set(re.findall(r"make_ci_results\.py check [^\n]*?\"?([a-z_]+)\"? \",", ci))
    # fallback: the check lines look like `check "$CI_RESULTS_DIR" <name> "$rc"`
    recorded |= set(re.findall(r'check "\$CI_RESULTS_DIR" ([a-z_]+)', ci))
    src = GATE.read_text(encoding="utf-8")
    gate_checks = set(re.findall(r'"([a-z_]+)",', src.split("CHECKS = (")[1].split(")")[0]))
    assert recorded, "could not parse any check names from ci.yml"
    not_gated = recorded - gate_checks
    assert not not_gated, (
        f"ci.yml records {sorted(not_gated)} but the final gate does not verdict them"
    )
