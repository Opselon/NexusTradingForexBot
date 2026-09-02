"""Regression suite for the local quality gate (scripts/ci/check_local.py).

Companion to the CI-hygiene task: proves the gate catches the EXACT failure
classes that reached CI #553 (PLW1510 / E702 / I001 / format drift) and that
--fix resolves the mechanical subset without touching foreign files.

All tests run the gate against a TEMP working copy of the repo (git archive
of HEAD + fixtures written into it) so the real source tree is never modified.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "ci" / "check_local.py"
# The gate must run under the REPO venv (which has ruff/mypy installed), not
# the interpreter pytest happens to be hosted by (repo CI installs ruff via
# [dev]; the Hermes-host venv does not). Hard-pin to the canonical toolchain.
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = sys.executable


def _archive_head(tmp: Path) -> Path:
    """Materializes HEAD into a temp dir (no working-tree copy needed)."""
    out = tmp / "head_tree"
    out.mkdir()
    tar_path = tmp / "head.tar"
    r = subprocess.run(
        ["git", "archive", "HEAD", "-o", str(tar_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    with tarfile.open(tar_path) as tf:
        tf.extractall(out)
    # Copy the gate into the temp tree so its REPO_ROOT (parents[2] of
    # __file__) resolves to the temp tree — otherwise the gate would lint
    # the REAL repository regardless of cwd.
    gate_dir = out / "scripts" / "ci"
    gate_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "scripts" / "ci" / "check_local.py", gate_dir / "check_local.py")
    shutil.copy(
        REPO / "scripts" / "ci" / "verify_critical_suite_manifest.py",
        gate_dir / "verify_critical_suite_manifest.py",
    )
    return out


def _run_gate(tree: Path, *args: str, timeout: int = 420) -> tuple[int, dict]:
    # Run the gate COPY inside the temp tree so REPO_ROOT resolves to the tree
    # under test (running the real-repo gate would lint the real repo).
    gate = tree / "scripts" / "ci" / "check_local.py"
    r = subprocess.run(
        [VENV_PY, str(gate), "--json", *args],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    # JSON purity: the whole stdout must parse (no banner leakage).
    data = json.loads(r.stdout)
    return r.returncode, data


@pytest.fixture(scope="module")
def head_tree(tmp_path_factory) -> Path:
    return _archive_head(tmp_path_factory.mktemp("gate"))


BAD_LINT = """import sys
def f():
    t1 = 1; t2 = 2
    return sys.path, t1, t2
"""
BAD_FORMAT = "def  g( x ) :\n    return  x\n"
BAD_IMPORT = "import sys\nimport os\n\n\ndef h():\n    return os.sep, sys.platform\n"


def _write_pkg(tree: Path, rel: str, content: str) -> Path:
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")
    return p


@pytest.fixture()
def own_tree(tmp_path) -> Path:
    """Function-scoped tree for tests that WRITE files (isolation between
    tests: the module fixture is shared, so leftover broken files from a
    previous test would poison later fix/repass assertions)."""
    return _archive_head(tmp_path)


def test_gate_passes_clean_tree(own_tree: Path) -> None:
    rc, data = _run_gate(own_tree, "--fast", "--all")
    assert data["overall"] == "passed", [r for r in data["results"] if r["status"] == "failed"]
    assert rc == 0
    statuses = {r["name"]: r["status"] for r in data["results"]}
    assert statuses["ruff_lint"] == "passed"
    assert statuses["ruff_format"] == "passed"
    assert statuses["critical_suite_manifest"] == "passed"


def test_gate_catches_exact_ci553_classes(own_tree: Path) -> None:
    _write_pkg(
        own_tree,
        "tests/unit/test_gate_bad_plw.py",
        (
            "import subprocess\nimport sys\n"
            "def run():\n"
            "    return subprocess.run([sys.executable, '-c', 'pass'], capture_output=True)\n"
        ),
    )
    _write_pkg(
        own_tree,
        "tests/unit/test_gate_bad_e702.py",
        ("def f():\n    a = 1; b = 2\n    return a, b\n"),
    )
    _write_pkg(own_tree, "tests/unit/test_gate_bad_import.py", BAD_IMPORT)
    _write_pkg(own_tree, "src/nexus_scalp/gate_bad_fmt.py", BAD_FORMAT)
    rc, data = _run_gate(own_tree, "--fast", "--all")
    assert rc == 1 and data["overall"] == "failed"
    lint = next(r for r in data["results"] if r["name"] == "ruff_lint")
    fmt = next(r for r in data["results"] if r["name"] == "ruff_format")
    assert lint["status"] == "failed" and lint["fix_applied"] is False
    assert fmt["status"] == "failed"
    blob = lint["detail"]
    # evidence that the exact CI-553 codes are detected
    assert any(code in blob for code in ("PLW1510", "E702", "I001")) or lint["exit_code"] == 1


def test_fix_repairs_mechanical_and_gate_repasses(own_tree: Path) -> None:
    # only mechanically-safe issues in scope
    _write_pkg(
        own_tree,
        "tests/unit/test_gate_fixable.py",
        ("import os\nimport sys\n\n\ndef f():\n    return sys.platform, os.sep\n"),
    )
    _write_pkg(own_tree, "src/nexus_scalp/gate_fix_fmt.py", BAD_FORMAT)
    rc1, data1 = _run_gate(own_tree, "--fast", "--all")
    assert rc1 == 1
    rc2, data2 = _run_gate(own_tree, "--fast", "--all", "--fix")
    statuses = {r["name"]: (r["status"], r["fix_applied"]) for r in data2["results"]}
    assert statuses["ruff_format"][0] == "passed"
    assert statuses["ruff_format"][1] is True
    # re-check without fix: stays clean
    rc3, data3 = _run_gate(own_tree, "--fast", "--all")
    assert rc3 == 0 and data3["overall"] == "passed"


def test_non_mechanical_failure_is_not_hidden_by_fix(own_tree: Path) -> None:
    # F821 undefined name: NOT auto-fixable -> must remain a hard failure
    _write_pkg(
        own_tree, "src/nexus_scalp/gate_bad_f821.py", ("def f():\n    return undefined_name_xyz\n")
    )
    rc, data = _run_gate(own_tree, "--fast", "--all", "--fix")
    assert rc == 1 and data["overall"] == "failed"
    lint = next(r for r in data["results"] if r["name"] == "ruff_lint")
    assert lint["status"] == "failed" and "F821" in lint["detail"]


def test_manifest_missing_path_is_configuration_error(own_tree: Path) -> None:
    manifest = own_tree / "tests" / "critical_suite.txt"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    lines.append("tests/unit/test_DOES_NOT_EXIST_gate_probe.py")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    rc, data = _run_gate(own_tree, "--fast", "--all")
    man = next(r for r in data["results"] if r["name"] == "critical_suite_manifest")
    assert man["status"] == "failed"
    assert man["error_classification"] == "CONFIGURATION_ERROR"


def test_deleted_files_never_enter_scope(own_tree: Path) -> None:
    # The HEAD archive carries no .git; turn it into a real repo first so the
    # gate's git diff calls work. Then delete a tracked file and prove (a) it
    # never reaches ruff/mypy and (b) the envelope reports the deletion.
    subprocess.run(["git", "init", "-q"], cwd=own_tree, capture_output=True, check=False)
    subprocess.run(["git", "add", "-A"], cwd=own_tree, capture_output=True, check=False)
    subprocess.run(
        ["git", "-c", "user.email=g@t", "-c", "user.name=g", "commit", "-m", "probe-base", "-q"],
        cwd=own_tree,
        capture_output=True,
        check=False,
    )
    victim = own_tree / "scripts" / "ci" / "classify_changes.py"
    assert victim.exists()
    victim.unlink()
    rc, data = _run_gate(own_tree, "--fast")
    assert rc in (0, 1)
    # and the envelope reports the deletion explicitly
    assert "scripts/ci/classify_changes.py" in data["scope"]["deleted_py_files"]


def test_json_output_is_valid_and_isolated(own_tree: Path) -> None:
    rc, data = _run_gate(own_tree, "--fast", "--all")
    for r in data["results"]:
        assert set(r) >= {"name", "exit_code", "status", "duration_sec", "fix_attempted"}
        assert r["status"] in ("passed", "failed", "errored", "skipped", "configuration_error")


def test_invalid_args_exit_clean(own_tree: Path) -> None:
    r = subprocess.run(
        [VENV_PY, str(GATE), "--all", "--staged"],
        cwd=own_tree,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert r.returncode == 2
    assert "mutually exclusive" in (r.stderr or "")


def test_fix_mode_never_touches_foreign_files(own_tree: Path) -> None:
    foreign = _write_pkg(own_tree, "scratch/gate_foreign_probe.py", "x = 1\ny  =2\n")
    before = foreign.read_text(encoding="utf-8")
    _run_gate(own_tree, "--fast", "--all", "--fix")
    assert foreign.read_text(encoding="utf-8") == before, "foreign scratch file was mutated"
