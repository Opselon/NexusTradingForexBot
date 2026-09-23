"""ML-QA-006 — duplicate-canonical-row regression guard (BUG-309).

Companion battery to tests/unit/test_merge_marker_residue.py. ML-QA-005
closed the diff3 marker leak; this pins the *duplicate-row class* the leaked
markers exposed: three contradictory `ML-CI-002` rows in one SSOT table
(one DONE / one BLOCKED / one DONE with the wrong owner) survived every
gate for weeks because no gate inspected table structure.

Coverage:
  * the real repo tables are clean at HEAD (the guard's job at commit time)
  * a duplicated canonical row is detected (negative control, exact
    ML-CI-002 shape)
  * backticked and plain ids are the same identity
  * dependency-cell mentions are NOT counted as canonical rows (the false
    positive that would make the gate useless — the ledger's dependency
    column legitimately restates other ids on every row)
  * the migration-map section (which legitimately pairs legacy + new ids)
    is scoped OUT by the section filter
  * --json payload parses as JSON and is not Rich-wrapped (BUG-300)
  * the gate is stdlib-only and importable from any interpreter (no
    torch/polars/fastapi pulled at import)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "ci" / "check_duplicate_task_rows.py"

_FAKE_TABLE = """\
# Board

## 2. Master Backlog Table

| Task ID | Stream | Priority | Title | Status | Agent | Human | Deps | Class |
|---|---|---|---|---|---|---|---|---|
| `ML-DATA-001` | A | P0 | Ingest | **DONE** | AGENT-DATA | NO | None | PARALLEL_SAFE |
| `ML-VAL-002` | G | P2 | Calibration | **DONE** | AGENT-VAL | NO | `ML-EXP-002` | PARALLEL_SAFE |
| `ML-CI-002` | L | P3 | Contract drift gate | **DONE** (PR #346) | `AGENT-QA` | NO | `ML-ARCH-001` | PARALLEL_SAFE |

## 3. Dependency DAG

| Legacy | New |
|---|---|
| `TASK-005` | `ML-VAL-001` |
"""

_FAKE_TASK_BOARD = """\
# TASKBOARD

| TASK-ID | Owner | Status |
|---|---|---|
| TASK-SAMPLE-1 | agent | DONE |
"""


def _make_fixture(tmp_path: Path) -> None:
    """Create BOTH SSOT files the gate declares — a missing file is itself
    a defect, so every fixture must satisfy the full table set."""
    ledger = tmp_path / "docs" / "ml-system" / "06_TASK_LEDGER.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(_FAKE_TABLE, encoding="utf-8")
    board = tmp_path / "docs" / "ml-system" / "TASK_BOARD.md"
    board.write_text(_FAKE_TASK_BOARD, encoding="utf-8")


def _run_gate(
    cwd: Path, *args: str, gate_cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_GATE), *args],
        check=False,
        cwd=str(gate_cwd if gate_cwd is not None else cwd),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "."])},
    )


class TestRealRepoTablesAreClean:
    """The committed SSOT tables must have exactly one canonical row per id."""

    def test_main_backlog_has_no_duplicate_rows(self):
        r = _run_gate(_REPO_ROOT)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "DUPLICATE_TASK_ROWS_OK" in r.stdout

    def test_json_payload_parses_and_reports_clean(self):
        r = _run_gate(_REPO_ROOT, "--json")
        assert r.returncode == 0, r.stdout + r.stderr
        payload = json.loads(r.stdout)  # BUG-300: unwrapped stdout
        assert payload["gate"] == "duplicate_task_rows"
        assert payload["status"] == "CLEAN"
        assert payload["duplicates_found"] == 0


class TestDuplicateDetection:
    """The exact ML-CI-002 negative-control shape must be caught."""

    def test_duplicate_canonical_row_is_detected(self, tmp_path):
        _make_fixture(tmp_path)
        board = tmp_path / "docs" / "ml-system" / "06_TASK_LEDGER.md"
        text = board.read_text()
        lines = text.splitlines()
        dup = (
            "| `ML-CI-002` | L: CI/CD | P3 | leaked duplicate | **BLOCKED** "
            "| `AGENT-GIT` | NO | `ML-ARCH-001` | SERIAL_ONLY |"
        )
        target = next(i for i, l in enumerate(lines) if l.startswith("| `ML-CI-002`"))
        lines.insert(target + 1, dup)
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

        r = _run_gate(tmp_path)
        assert r.returncode == 1, r.stdout
        assert "DUPLICATE_TASK_ROWS_DEFECT" in r.stdout
        # JSON mode too
        rj = _run_gate(tmp_path, "--json")
        assert rj.returncode == 1
        payload = json.loads(rj.stdout)
        assert payload["status"] == "DEFECT"
        assert payload["duplicates_found"] >= 1
        assert any(d["task_id"] == "ML-CI-002" for d in payload["findings"][0]["duplicates"])

    def test_three_contradictory_rows_are_all_reported(self, tmp_path):
        """The PR #347 shape: DONE / BLOCKED / DONE-wrong-owner for one id."""
        _make_fixture(tmp_path)
        board = tmp_path / "docs" / "ml-system" / "06_TASK_LEDGER.md"
        rows = [
            "| `ML-OBS-003` | L | P3 | a | **DONE** (PR #346) | `AGENT-QA` | NO | None | P |",
            "| `ML-OBS-003` | L | P3 | b | **BLOCKED** | `AGENT-QA` | NO | None | P |",
            "| `ML-OBS-003` | L | P3 | c | **DONE** | `AGENT-GIT` | NO | None | P |",
        ]
        lines = board.read_text().splitlines()
        dag = lines.index("## 3. Dependency DAG")
        lines[dag:dag] = rows
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = _run_gate(tmp_path)
        assert r.returncode == 1
        assert "ML-OBS-003" in r.stdout
        rj = json.loads(_run_gate(tmp_path, "--json").stdout)
        dupe = rj["findings"][0]["duplicates"][0]
        assert dupe["task_id"] == "ML-OBS-003"
        assert len(dupe["lines"]) == 3


class TestNoFalsePositives:
    """Dependency-column mentions must NOT count as canonical rows."""

    def test_dependency_mentions_are_not_duplicates(self, tmp_path):
        _make_fixture(tmp_path)
        # ML-VAL-002's dependency cell names ML-EXP-002; ML-EXP-002 has no
        # canonical row of its own, so this must stay clean.
        r = _run_gate(tmp_path)
        assert r.returncode == 0, r.stdout
        assert "DUPLICATE_TASK_ROWS_OK" in r.stdout

    def test_migration_map_section_is_scoped_out(self, tmp_path):
        """Section §1 pairs legacy+new ids on one row and must be excluded."""
        _make_fixture(tmp_path)
        board = tmp_path / "docs" / "ml-system" / "06_TASK_LEDGER.md"
        lines = board.read_text().splitlines()
        # prepend a §1-style migration table naming every id once per row
        mig = [
            "## 1. Historical Migration Map (Old 15 Tasks → New 30 Tasks)",
            "",
            "| Legacy ID | New Task ID |",
            "|---|---|",
            "| `TASK-001` | `ML-DATA-001` |",
            "| `TASK-002` | `ML-DATA-001` |",
            "| `TASK-013` | `ML-CI-002` |",
            "",
        ]
        lines[0:1] = ["# Board", "", *mig]
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = _run_gate(tmp_path)
        assert r.returncode == 0, r.stdout
        # the section filter matched (proves we did not scan by accident)
        rj = json.loads(_run_gate(tmp_path, "--json").stdout)
        assert rj["status"] == "CLEAN"

    def test_unbackticked_and_backticked_ids_are_same_identity(self, tmp_path):
        _make_fixture(tmp_path)
        board = tmp_path / "docs" / "ml-system" / "06_TASK_LEDGER.md"
        lines = board.read_text().splitlines()
        rows = [
            "| ML-FEAT-009 | B | P0 | plain | **DONE** | AGENT | NO | None | P |",
            "| `ML-FEAT-009` | B | P0 | backticked | **BLOCKED** | AGENT | NO | None | P |",
        ]
        dag = lines.index("## 3. Dependency DAG")
        lines[dag:dag] = rows
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = _run_gate(tmp_path)
        assert r.returncode == 1, r.stdout
        assert "ML-FEAT-009" in r.stdout


class TestGateShape:
    """The gate must stay stdlib-only and JSON-pure (BUG-300/CHG-0049)."""

    def test_no_heavy_imports(self):
        import importlib
        import sys

        before = set(sys.modules)
        # run as a module from the repo root with the repo on sys.path
        sys.path.insert(0, str(_REPO_ROOT))
        try:
            importlib.import_module("scripts.ci.check_duplicate_task_rows")
        finally:
            sys.path.remove(str(_REPO_ROOT))
        added = set(sys.modules) - before
        heavy = {
            m for m in added if m.split(".")[0] in {"torch", "polars", "numpy", "pandas", "fastapi"}
        }
        assert heavy == set(), f"gate pulled heavy deps: {heavy}"

    def test_missing_root_is_config_error(self, tmp_path):
        missing = tmp_path / "nope"
        r = _run_gate(_REPO_ROOT, "--root", str(missing))
        assert r.returncode == 2, r.stdout

    def test_json_stdout_is_one_line(self, tmp_path):
        """A single-line JSON payload cannot be Rich-wrapped (BUG-300)."""
        _make_fixture(tmp_path)
        r = _run_gate(tmp_path, "--json")
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        assert len(lines) == 1, f"json payload must be one line, got {len(lines)}"
        assert json.loads(lines[0])["gate"] == "duplicate_task_rows"
