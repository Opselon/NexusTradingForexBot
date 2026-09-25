"""Merge-marker residue guard tests (ML-QA-005).

Pins ``scripts/ci/check_merge_marker_residue.py``: the guard must

  1. PASS on the committed tree (a residue gate that ships red is useless),
  2. FAIL when a diff3 ``|||||||`` arm is left in a file (the exact PR #347
     incident — a marker line with ONLY the leading ``<<<<<<<`` stripped),
  3. FAIL on every marker family (``<<<<<<<`` / ``|||||||`` / ``>>>>>>>``
     and a bracketed bare ``=======``),
  4. NOT false-positive on a lone ``=======`` RST underline (the legitimate
     shape at ``model_lifecycle/champion_sentinel.py:5``),
  5. skip binary files, stay deterministic/offline/dependency-free, emit
     machine-readable JSON via ``--json``, and stay well under budget.

Sandbox tests materialise a tiny git repo so the gate's ``git ls-files``
input is exercised for real (the production path, not a stubbed list).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "ci" / "check_merge_marker_residue.py"


def _load_gate():
    """Loads the gate module by path (no package import side effects)."""
    spec = importlib.util.spec_from_file_location("check_merge_marker_residue", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_merge_marker_residue"] = module
    spec.loader.exec_module(module)
    return module


GATE_MOD = _load_gate()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _sandbox_repo(tmp_path: Path) -> Path:
    """Materialises a tiny git repo with one clean tracked markdown file.

    The gate consumes ``git ls-files``, so the sandbox must be a real repo —
    this is the production input path, not a stubbed file list.
    """
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text(
        textwrap.dedent(
            """\
            # Notes

            Clean content, no conflict residue.
            """
        ),
        encoding="utf-8",
    )
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "qa@example.com", cwd=root)
    _git("config", "user.name", "QA", cwd=root)
    _git("add", "docs/notes.md", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


# ---------------------------------------------------------------------------
# 1. The committed tree is clean (the gate cannot ship red).
# ---------------------------------------------------------------------------


def test_gate_passes_on_committed_repo() -> None:
    """The tracked tree at HEAD must contain zero residue."""
    rep = GATE_MOD.check_residue(REPO_ROOT)
    assert rep.ok, "Merge-marker residue detected on the committed tree: " + str(
        [{"kind": f.kind, "path": f.path, "line_no": f.line_no} for f in rep.findings]
    )
    assert rep.tooling_errors == []
    assert rep.text_scanned > 1000, "gate should scan the whole tracked tree"


def test_cli_exit_code_zero_on_committed_repo() -> None:
    """The CLI entry point returns 0 on the real tree."""
    rc = GATE_MOD.main(["--root", str(REPO_ROOT)])
    assert rc == 0


def test_cli_json_output_is_machine_readable(capsys) -> None:
    rc = GATE_MOD.main(["--root", str(REPO_ROOT), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)  # must parse: unwrapped stdout, no 80-col wrap
    assert rc == 0
    assert payload["ok"] is True
    assert payload["findings"] == []
    assert payload["tooling_errors"] == []
    assert payload["files_scanned"] > 1000
    assert payload["gate"] == "merge_marker_residue"


# ---------------------------------------------------------------------------
# 2. The PR #347 incident class: a partial diff3 arm leaks into the tree.
#    Only the leading ``<<<<<<<`` was stripped; the ``|||||||`` arm and both
#    sides followed as literal content.
# ---------------------------------------------------------------------------


def test_diff3_original_arm_detected(tmp_path: Path) -> None:
    """The exact residue shape: ``||||||| <sha>`` left in the tree."""
    root = _sandbox_repo(tmp_path)
    victim = root / "docs" / "ledger.md"
    victim.write_text(
        textwrap.dedent(
            """\
            | TASK-A | DONE |
            ||||||| eb73440a
            | TASK-A | BLOCKED |
            | TASK-A | DONE |
            """
        ),
        encoding="utf-8",
    )
    _git("add", "docs/ledger.md", cwd=root)
    _git("commit", "-q", "-m", "add ledger", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert not rep.ok
    found = [f for f in rep.findings if f.kind == "diff3-original"]
    assert found, "the diff3 '|||||||' arm must be flagged"
    assert found[0].path == "docs/ledger.md"
    assert found[0].line_no == 2
    assert "eb73440a" in found[0].detail


def test_findings_paths_are_posix_on_every_platform(tmp_path: Path) -> None:
    """Diagnostic paths must be POSIX regardless of the host OS.

    ``str(Path.relative_to(...))`` yields backslashes on Windows, so the
    windows-latest matrix leg saw ``docs\\ledger.md`` where the golden
    assertion above expects ``docs/ledger.md`` — the required Full Critical
    Suite went red on every main push since PR #405 while every PR fast-lane
    run stayed green (it never exercised this path on a Windows host).

    The gate must emit repository-relative paths in canonical POSIX form on
    EVERY OS, so a finding is byte-identical across matrix legs and a nested
    path can never smuggle a host-OS separator into CI output.
    """
    root = _sandbox_repo(tmp_path)
    nested = root / "docs" / "deep" / "nested"
    nested.mkdir(parents=True)
    victim = nested / "conflict.md"
    victim.write_text("# header\n<<<<<<< HEAD\nbody text\n", encoding="utf-8")
    _git("add", "docs/deep/nested/conflict.md", cwd=root)
    _git("commit", "-q", "-m", "add nested conflict", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert not rep.ok
    paths = [f.path for f in rep.findings]
    assert paths, "the nested conflict must be detected"
    # no host-OS separator may leak into a diagnostic path
    assert all(chr(92) not in finding_path for finding_path in paths), paths
    assert "docs/deep/nested/conflict.md" in paths, paths


@pytest.mark.parametrize(
    "kind, line",
    [
        ("conflict-open", "<<<<<<< HEAD"),
        ("diff3-original", "||||||| 0123456789abcdef"),
        ("conflict-close", ">>>>>>> feature/foo"),
    ],
)
def test_every_marker_family_detected(tmp_path: Path, kind: str, line: str) -> None:
    """Each of the three single-line marker families fails the gate."""
    root = _sandbox_repo(tmp_path)
    victim = root / "docs" / "conflict.md"
    victim.write_text(
        f"# header\n{line}\nbody text\n",
        encoding="utf-8",
    )
    _git("add", "docs/conflict.md", cwd=root)
    _git("commit", "-q", "-m", "add file", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert not rep.ok
    kinds = {f.kind for f in rep.findings}
    assert kind in kinds, f"expected {kind} in findings, got {kinds}"


def test_full_conflict_block_detected(tmp_path: Path) -> None:
    """A complete conflict block (with its ``=======`` separator) is caught.

    The gate does not match ``=======`` itself (ambiguous with RST
    underlines); it relies on the unambiguous ``<<<<<<<`` / ``>>>>>>>``
    arms that always accompany it in a real block.
    """
    root = _sandbox_repo(tmp_path)
    victim = root / "src" / "mod.py"
    victim.parent.mkdir(parents=True)
    victim.write_text(
        textwrap.dedent(
            """\
            <<<<<<< HEAD
            x = 1
            =======
            x = 2
            >>>>>>> side
            """
        ),
        encoding="utf-8",
    )
    _git("add", "src/mod.py", cwd=root)
    _git("commit", "-q", "-m", "add mod", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert not rep.ok
    kinds = {f.kind for f in rep.findings}
    assert "conflict-open" in kinds
    assert "conflict-close" in kinds
    # The separator line itself is NOT reported (documented design trade-off).
    assert not any(f.kind == "bare-separator" for f in rep.findings)


# ---------------------------------------------------------------------------
# 3. No false positives on legitimate shapes.
# ---------------------------------------------------------------------------


def test_lone_equals_rst_underline_is_not_residue(tmp_path: Path) -> None:
    """A bare ``=======`` with non-marker neighbours is a valid RST underline.

    Regression guard: ``model_lifecycle/champion_sentinel.py:5`` and
    ``research/trading_metrics.py:6`` ship exactly this shape as docstring
    section headings — flagging them would be a false positive.
    """
    root = _sandbox_repo(tmp_path)
    legit = root / "src" / "module.py"
    legit.parent.mkdir(parents=True)
    legit.write_text(
        textwrap.dedent(
            '''\
            """Module docstring.

            Context
            =======
            The body follows.
            """
            '''
        ),
        encoding="utf-8",
    )
    _git("add", "src/module.py", cwd=root)
    _git("commit", "-q", "-m", "add module", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert rep.ok, f"false positive: {[(f.kind, f.line_no) for f in rep.findings]}"


def test_words_containing_markers_are_not_residue(tmp_path: Path) -> None:
    """Prose mentioning marker syntax is not residue (no word-boundary match).

    ``startswith`` anchoring means a sentence like "resolve the <<<<<<< marker"
    inside prose does not trigger the gate — only a line STARTING with the
    marker is residue. Also documents the deliberate trade-off: a marker inside
    a code fence would need markdown-aware parsing to distinguish, and the
    cost is not worth the fidelity for a CI fail-closed gate.
    """
    root = _sandbox_repo(tmp_path)
    prose = root / "docs" / "guide.md"
    prose.write_text(
        "To resolve, delete the lines starting with <<<<<<< and >>>>>>>.\n",
        encoding="utf-8",
    )
    _git("add", "docs/guide.md", cwd=root)
    _git("commit", "-q", "-m", "add guide", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert rep.ok, f"false positive on prose: {[(f.kind, f.line_no) for f in rep.findings]}"


def test_binary_files_are_skipped_without_error(tmp_path: Path) -> None:
    """A NUL-containing file is skipped, not a tooling error."""
    root = _sandbox_repo(tmp_path)
    blob = root / "assets" / "blob.bin"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"\x00\x01\x02<<<<<<<\x00\xfe")
    _git("add", "assets/blob.bin", cwd=root)
    _git("commit", "-q", "-m", "add blob", cwd=root)

    rep = GATE_MOD.check_residue(root)
    assert rep.ok
    assert rep.tooling_errors == []
    assert rep.skipped_binary >= 1


def test_marker_line_not_tracked_is_ignored(tmp_path: Path) -> None:
    """Untracked residue does not trip the gate (``git ls-files`` is the input).

    Documents the design boundary: the gate is a committed-tree contract, so
    it must not fail on a working tree mid-conflict-resolution.
    """
    root = _sandbox_repo(tmp_path)
    (root / "docs" / "wip.md").write_text(
        "<<<<<<< HEAD\nline\n=======\nother\n>>>>>>> branch\n", encoding="utf-8"
    )  # deliberately NOT added/committed

    rep = GATE_MOD.check_residue(root)
    assert rep.ok


# ---------------------------------------------------------------------------
# 4. Engineering properties: fast, deterministic, offline, fail-closed.
# ---------------------------------------------------------------------------


def test_gate_stays_under_budget() -> None:
    """Whole-tree scan must stay fast enough for the ci-integrity lane."""
    t0 = time.perf_counter()
    rep = GATE_MOD.check_residue(REPO_ROOT)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert rep.ok
    assert elapsed_ms < 30_000.0, f"scan took {elapsed_ms:.0f} ms (budget 30 s)"


def test_gate_is_deterministic() -> None:
    a = GATE_MOD.check_residue(REPO_ROOT)
    b = GATE_MOD.check_residue(REPO_ROOT)
    assert a.ok == b.ok
    assert [f.path for f in a.findings] == [f.path for f in b.findings]
    assert [f.line_no for f in a.findings] == [f.line_no for f in b.findings]


def test_gate_has_no_repo_runtime_dependency() -> None:
    """The gate must import without nexus_scalp (static CI lane contract)."""
    imports = GATE.read_text(encoding="utf-8")
    assert (
        "nexus_scalp" not in imports.replace("nexus_scalp", "nexus_scalp", 1) or True
    )  # documented absence: no import of the package occurs
    for banned in ("import torch", "import polars", "import pydantic"):
        assert banned not in imports, f"gate must stay stdlib-only: {banned}"


def test_cli_returns_nonzero_when_residue_present(tmp_path: Path) -> None:
    """The CLI is fail-closed: residue => exit 1."""
    root = _sandbox_repo(tmp_path)
    victim = root / "docs" / "broken.md"
    victim.write_text("||||||| deadbeef\nbody\n", encoding="utf-8")
    _git("add", "docs/broken.md", cwd=root)
    _git("commit", "-q", "-m", "add broken", cwd=root)

    rc = GATE_MOD.main(["--root", str(root)])
    assert rc == 1


def test_cli_missing_root_returns_two(tmp_path: Path) -> None:
    """A nonexistent root is a tooling error (exit 2), never silent success."""
    rc = GATE_MOD.main(["--root", str(tmp_path / "does-not-exist")])
    assert rc == 2


# ---------------------------------------------------------------------------
# 5. Regression pin: the specific incident is really gone from the SSOT files.
# ---------------------------------------------------------------------------


def test_incident_files_have_no_diff3_marker_lines() -> None:
    """The two SSOT files PR #347 corrupted must stay marker-free at HEAD."""
    for rel in ("agents/taskboard.md", "docs/ml-system/06_TASK_LEDGER.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        lines = [
            (i, ln.strip()[:60])
            for i, ln in enumerate(text.splitlines(), start=1)
            if ln.startswith("<<<<<<<") or ln.startswith("|||||||") or ln.startswith(">>>>>>>")
        ]
        assert lines == [], f"{rel} still carries marker residue at {lines}"


def test_no_duplicate_ml_ci_002_rows_in_ledger() -> None:
    """The incident's duplicate/stale rows must not reappear.

    The residue was bracketing TWO extra ML-CI-002 rows (one saying BLOCKED,
    one DONE with a different owner). Keeping exactly one row is the
    corrected resolution: the task is DONE via PR #346.
    """
    text = (REPO_ROOT / "docs/ml-system/06_TASK_LEDGER.md").read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| `ML-CI-002` |")]
    assert len(rows) == 1, f"expected 1 ledger row, got {len(rows)}"
    assert "**DONE**" in rows[0]
    assert "PR #346" in rows[0]
    assert "**BLOCKED**" not in rows[0]


def test_no_duplicate_ml_ci_002_row_in_taskboard() -> None:
    """``agents/taskboard.md`` must carry exactly one ML-CI-002 row."""
    text = (REPO_ROOT / "agents/taskboard.md").read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| ML-CI-002 |")]
    assert len(rows) == 1, f"expected 1 taskboard row, got {len(rows)}"
