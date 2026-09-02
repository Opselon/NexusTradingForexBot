"""CHG-0052 regression tests: Ruff CI auto-repair + blocked-state taxonomy.

Run #685 class failure: `ruff format --check` fails on the CI checkout, the
job cancels downstream steps, and missing result JSONs were misclassified as
"errored". These tests pin BOTH halves:

  * ci_ruff_repair.py — REAL subprocess behavior (actual `ruff` binary on a
    disposable tree): dirty tree -> repaired + verified + patch/report
    artifacts; clean tree -> untouched; foreign files never modified.
  * make_ci_results.py — missing downstream JSON + root failure => status
    "blocked" (BLOCKED_BY_<ROOT>), never "errored"; no root failure =>
    "skipped"; existing results untouched; `check --blocked` records
    blocked even with rc=0.

The repair tests locate the repo toolchain's ruff module and drive it
through the repair module's own helpers so local and CI stay parity-pinned.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REPAIR = REPO / "scripts" / "ci" / "ci_ruff_repair.py"
RESULTS = REPO / "scripts" / "ci" / "make_ci_results.py"
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import ci_ruff_repair  # noqa: E402


def _repo_python() -> str:
    for candidate in (REPO / ".venv" / "Scripts" / "python.exe", sys.executable):
        if Path(candidate).exists():
            return str(candidate)
    pytest.skip("no usable python toolchain for subprocess tests")


def _py() -> str:
    return _repo_python()


def _has_ruff() -> bool:
    probe = subprocess.run(
        [_py(), "-m", "ruff", "--version"], capture_output=True, text=True, check=False
    )
    return probe.returncode == 0


RUFF_REQUIRED = pytest.mark.skipif(not _has_ruff(), reason="ruff not installed in toolchain")


def _write_pkg(tmp: Path) -> None:
    """Minimal pyproject + dirty package so ruff's formatter has real config."""
    (tmp / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\ntarget-version = 'py311'\n", encoding="utf-8"
    )
    # ci_ruff_repair looks for the toolchain in <cwd>/.venv first; the venv
    # shim python.exe must actually BE a working interpreter (a copied exe
    # loses its venv DLL/home on Windows), so use the hosting interpreter.
    scripts = tmp / ".venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "python.exe").write_bytes(Path(sys.executable).read_bytes())
    pkg = tmp / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("X = 1\n", encoding="utf-8")


def _dirty_file(tmp: Path) -> None:
    # deliberately unformatted: too-long call the formatter must wrap
    (tmp / "pkg" / "dirty.py").write_text(
        "def f(a, b, c, d, e, g, h, i, j, k, l, m, n, o, p, q, r, s, t, u, v, w, x, y, z):\n"
        "    return a + b + c + d + e + g + h + i + j + k + l + m + n + o + p + q + r + s + t"
        " + u + v + w + x + y + z\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# ci_ruff_repair — real subprocess behavior on disposable trees
# ---------------------------------------------------------------------------


@RUFF_REQUIRED
def test_repair_fixes_dirty_tree_and_writes_artifacts(tmp_path: Path) -> None:
    _write_pkg(tmp_path)
    _dirty_file(tmp_path)
    results = tmp_path / "ci-results"
    rc = ci_ruff_repair.repair(tmp_path, results)
    assert rc == 0
    report = json.loads((results / "ruff-repair-report.json").read_text(encoding="utf-8"))
    assert report["source_tree_was_clean"] is False
    assert report["repaired"] is True
    assert report["repair_status"] == "repaired"
    assert report["post_repair_format_exit_code"] == 0
    assert report["post_repair_lint_exit_code"] == 0
    assert any("dirty.py" in f for f in report["files_changed"])
    assert report["patch_file"] == "ruff-repair.patch"
    assert report["diff_hash"]
    # the patch must contain the repair and apply cleanly to the original tree
    patch = (results / "ruff-repair.patch").read_text(encoding="utf-8")
    assert "dirty.py" in patch
    # tree is now clean per the real formatter
    check = subprocess.run(
        [*_ruff_cmd(tmp_path), "format", "--check", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0


def _ruff_cmd(cwd: Path) -> list[str]:
    cmd = ci_ruff_repair._ruff_cmd(cwd)
    assert cmd, "ruff must be reachable for this test"
    return cmd


@RUFF_REQUIRED
def test_clean_tree_is_reported_not_needed(tmp_path: Path) -> None:
    _write_pkg(tmp_path)
    results = tmp_path / "ci-results"
    rc = ci_ruff_repair.repair(tmp_path, results)
    assert rc == 0
    report = json.loads((results / "ruff-repair-report.json").read_text(encoding="utf-8"))
    assert report["source_tree_was_clean"] is True
    assert report["repair_status"] == "not_needed"
    assert report["repaired"] is False
    assert not (results / "ruff-repair.patch").exists()


@RUFF_REQUIRED
def test_repair_touches_only_offending_files(tmp_path: Path) -> None:
    """The formatter must never become a mechanism for absorbing WIP:
    a clean foreign file must survive byte-identical."""
    _write_pkg(tmp_path)
    _dirty_file(tmp_path)
    foreign = tmp_path / "pkg" / "foreign_clean.py"
    foreign.write_text("Y = 2\n", encoding="utf-8")
    before = foreign.read_bytes()
    results = tmp_path / "ci-results"
    rc = ci_ruff_repair.repair(tmp_path, results)
    assert rc == 0
    assert foreign.read_bytes() == before
    report = json.loads((results / "ruff-repair-report.json").read_text(encoding="utf-8"))
    assert all("foreign_clean" not in f for f in report["files_changed"])


@RUFF_REQUIRED
def test_format_check_files_parses_offender_list(tmp_path: Path) -> None:
    _write_pkg(tmp_path)
    _dirty_file(tmp_path)
    offenders = ci_ruff_repair._format_check_files(tmp_path, _ruff_cmd(tmp_path))
    # ruff prints paths RELATIVE to cwd (OS separators); the contract is that
    # each entry resolves to the offending file under the scanned root.
    assert offenders, "dirty file must be detected"
    assert all(
        Path(tmp_path / p).resolve() == (tmp_path / "pkg" / "dirty.py").resolve() for p in offenders
    )


# ---------------------------------------------------------------------------
# make_ci_results blocked taxonomy (run #685 misclassification fix)
# ---------------------------------------------------------------------------


def _init_results(tmp_path: Path) -> Path:
    root = tmp_path / "ci-results"
    subprocess.run(
        [_py(), str(RESULTS), "init", str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    return root


def _read_status(root: Path, check: str) -> dict:
    return json.loads((root / "run-info" / f"{check}.json").read_text(encoding="utf-8"))


def test_missing_downstream_json_with_root_failure_is_blocked(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    r = subprocess.run(
        [_py(), str(RESULTS), "classify-gate", str(root), "--root-failure", "ruff_format"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    assert r.returncode == 0
    for check in ("mypy", "pytest", "coverage"):
        data = _read_status(root, check)
        assert data["status"] == "blocked"
        assert "BLOCKED_BY_RUFF_FORMAT" in data["detail"]


def test_missing_downstream_json_without_root_failure_is_skipped(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    r = subprocess.run(
        [_py(), str(RESULTS), "classify-gate", str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    assert r.returncode == 0
    for check in ("mypy", "pytest", "coverage"):
        assert _read_status(root, check)["status"] == "skipped"


def test_existing_results_are_never_overwritten_by_classifier(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    # a REAL failed mypy (rc=1) must survive classification untouched
    subprocess.run(
        [_py(), str(RESULTS), "check", str(root), "mypy", "1", "type errors found"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    subprocess.run(
        [_py(), str(RESULTS), "classify-gate", str(root), "--root-failure", "ruff_format"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    mypy = _read_status(root, "mypy")
    assert mypy["status"] == "failed"  # real failure keeps its own status
    assert _read_status(root, "pytest")["status"] == "blocked"


def test_check_blocked_flag_records_blocked_even_with_zero_rc(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    subprocess.run(
        [
            _py(),
            str(RESULTS),
            "check",
            str(root),
            "runtime_gate",
            "0",
            "BLOCKED_BY_RUFF_FORMAT",
            "--blocked",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    data = _read_status(root, "runtime_gate")
    assert data["status"] == "blocked"
    assert data["detail"] == "BLOCKED_BY_RUFF_FORMAT"


def test_summary_renders_blocked_state_not_fake_failures(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    subprocess.run(
        [_py(), str(RESULTS), "check", str(root), "ruff_format", "1", "files would be reformatted"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    subprocess.run(
        [_py(), str(RESULTS), "classify-gate", str(root), "--root-failure", "ruff_format"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    subprocess.run(
        [_py(), str(RESULTS), "summary", str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    md = (root / "run-info" / "summary.md").read_text(encoding="utf-8")
    assert "BLOCKED BY ROOT FAILURE" in md
    # mypy/pytest rows show BLOCKED, never ERRORED
    table = md.split("## Test Statistics")[0]
    assert "| Mypy | BLOCKED |" in table
    assert "| Pytest | BLOCKED |" in table
    assert "ERRORED |" not in table


def test_repair_report_renders_source_state_block_in_summary(tmp_path: Path) -> None:
    root = _init_results(tmp_path)
    (root / "ruff-repair-report.json").write_text(
        json.dumps(
            {
                "source_tree_was_clean": False,
                "repair_status": "repaired",
                "post_repair_format_exit_code": 0,
                "patch_file": "ruff-repair.patch",
                "files_offending": ["src/x.py"],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [_py(), str(RESULTS), "summary", str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    )
    md = (root / "run-info" / "summary.md").read_text(encoding="utf-8")
    assert "DIRTY — committed tree was malformed" in md
    assert "AUTO-REPAIR: REPAIRED" in md
    assert "NOT COMMITTED" in md
