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
    "runtime_deps",
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


def test_ruff_lint_diagnostic_extraction_and_annotations(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "ruff_lint", "1", "violations found")

    ruff_dir = root / "ruff"
    ruff_dir.mkdir(parents=True, exist_ok=True)
    lint_payload = [
        {
            "filename": "src/nexus_scalp/risk/risk_engine.py",
            "location": {"row": 42, "column": 5},
            "code": "F401",
            "message": "'sys' imported but unused",
        }
    ]
    (ruff_dir / "lint.json").write_text(json.dumps(lint_payload), encoding="utf-8")

    r = _run_gate(root)
    assert r.returncode == 1
    assert "src/nexus_scalp/risk/risk_engine.py" in r.stdout
    assert "42" in r.stdout
    assert "F401" in r.stdout
    assert "imported but unused" in r.stdout
    # Emits GitHub Actions annotation with file and line
    assert (
        "::error file=src/nexus_scalp/risk/risk_engine.py,line=42,col=5,title=ruff_lint (F401)"
        in r.stdout
    )


def test_ruff_format_diagnostic_extraction(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "ruff_format", "1", "files would be reformatted")

    fmt_dir = root / "format"
    fmt_dir.mkdir(parents=True, exist_ok=True)
    (fmt_dir / "format.txt").write_text(
        "Would reformat: src/nexus_scalp/core.py\n", encoding="utf-8"
    )

    r = _run_gate(root)
    assert r.returncode == 1
    assert "src/nexus_scalp/core.py" in r.stdout
    assert "::error file=src/nexus_scalp/core.py,title=ruff_format (FormatViolation)" in r.stdout


def test_mypy_diagnostic_extraction(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "mypy", "1", "type errors found")

    mypy_dir = root / "mypy"
    mypy_dir.mkdir(parents=True, exist_ok=True)
    (mypy_dir / "mypy.txt").write_text(
        "src/nexus_scalp/order.py:108:12: error: Incompatible types in assignment [assignment]\n",
        encoding="utf-8",
    )

    r = _run_gate(root)
    assert r.returncode == 1
    assert "src/nexus_scalp/order.py" in r.stdout
    assert "108" in r.stdout
    assert "Mypy[assignment]" in r.stdout
    assert (
        "::error file=src/nexus_scalp/order.py,line=108,col=12,title=mypy (Mypy[assignment])"
        in r.stdout
    )


def test_pytest_diagnostic_extraction_with_traceback(tmp_path: Path) -> None:
    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "pytest", "1", "see pytest/junit.xml")

    pt_dir = root / "pytest"
    pt_dir.mkdir(parents=True, exist_ok=True)
    junit_xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1">
    <testcase classname="tests.unit.test_trade" name="test_order_dispatch" file="tests/unit/test_trade.py" line="88">
      <failure message="assert status == 'FILLED'" type="AssertionError">Traceback (most recent call last):
  File "tests/unit/test_trade.py", line 88, in test_order_dispatch
    assert status == 'FILLED'
AssertionError: assert 'REJECTED' == 'FILLED'</failure>
    </testcase>
  </testsuite>
</testsuites>"""
    (pt_dir / "junit.xml").write_text(junit_xml, encoding="utf-8")

    summary_file = tmp_path / "step_summary.md"
    r = _run_gate(root, env_extra={"GITHUB_STEP_SUMMARY": str(summary_file)})
    assert r.returncode == 1
    assert "tests/unit/test_trade.py" in r.stdout
    assert "88" in r.stdout
    assert "AssertionError" in r.stdout
    assert "Traceback" in r.stdout or "test_order_dispatch" in r.stdout
    assert "::error file=tests/unit/test_trade.py,line=88,title=pytest (AssertionError)" in r.stdout
    # Verify GITHUB_STEP_SUMMARY markdown was written with traceback
    assert summary_file.is_file()
    md = summary_file.read_text(encoding="utf-8")
    assert "CI Gate Diagnostics Report" in md
    assert "tests/unit/test_trade.py" in md
    assert "AssertionError" in md


def test_classify_gate_covers_all_downstream_checks(tmp_path: Path) -> None:
    """When ruff_format fails, all gated downstream checks must become BLOCKED, none missing."""
    root = _init_tree(tmp_path)
    _record(root, "ruff_lint", "0")
    _record(root, "ruff_format", "1", "files would be reformatted")
    subprocess.run(
        [_py(), str(RESULTS), "classify-gate", str(root), "--root-failure", "ruff_format"],
        capture_output=True,
        text=True,
        check=True,
    )
    r = _run_gate(root)
    assert r.returncode == 1
    assert "ruff_format=failed" in r.stdout
    # All downstream checks are blocked:
    assert "mypy=blocked" in r.stdout
    assert "pytest=blocked" in r.stdout
    assert "smoke=blocked" in r.stdout
    assert "coverage=blocked" in r.stdout
    assert "critical_coverage=blocked" in r.stdout
    assert "runtime_gate=blocked" in r.stdout
    assert "runtime_deps=blocked" in r.stdout
    assert "layered_smoke=blocked" in r.stdout
    # Crucially, zero missing results!
    assert "MISSING RESULTS" not in r.stdout


def test_rich_report_renders_under_non_utf8_output(tmp_path: Path) -> None:
    """The rich gate report must survive a cp1252 (windows-latest) pipe, both ends.

    Regression for the OS-Matrix red on every main push. Two distinct failures
    on the windows-latest runner, whose process locale is cp1252:

    1. the write side — rich's U+1F534 rule banner raised UnicodeEncodeError,
       which truncated stdout before the diagnostic traces were emitted and
       broke all four diagnostic-extraction tests;
    2. the read side — once the write side was fixed, the emoji's UTF-8 bytes
       (0x90 trailing byte) still raised UnicodeDecodeError when the test
       parent decoded the pipe with its own charmap locale, so ``r.stdout``
       came back None.

    The console is now wrapped in an ASCII/backslashreplace text layer with
    ASCII stand-ins for the glyphs, so the report is 7-bit clean and decodable
    by any single-byte codec. Markdown output keeps its full glyph set.
    """

    root = _init_tree(tmp_path)
    for check in CHECKS:
        _record(root, check, "0")
    _record(root, "ruff_lint", "1", "violations found")
    lint_payload = [
        {
            "filename": "src/nexus_scalp/risk/risk_engine.py",
            "location": {"row": 42, "column": 5},
            "code": "F401",
            "message": "'sys' imported but unused",
        }
    ]
    ruff_dir = root / "ruff"
    ruff_dir.mkdir(exist_ok=True)
    (ruff_dir / "lint.json").write_text(json.dumps(lint_payload), encoding="utf-8")

    r = _run_gate(root, env_extra={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})

    assert r.returncode == 1, "a failing ruff_lint must fail the gate"
    assert r.stdout is not None, (
        "stdout was None — the parent hit UnicodeDecodeError decoding the pipe "
        "(the read-side half of this bug)"
    )
    assert "FAILING CHECKS: ruff_lint" in r.stdout
    # The diagnostic trace that the UnicodeEncodeError used to eat:
    assert "src/nexus_scalp/risk/risk_engine.py" in r.stdout
    assert "F401" in r.stdout
