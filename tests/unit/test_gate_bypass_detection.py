"""Bypass / drift detection for the local quality gate (steer §4 + §8).

Proves that gate bypass and contract drift are DETECTABLE and FAIL the gate:

  bypass class                      detection
  --------------------------------  ------------------------------------------
  push without local gate           --prepush derives the push scope from git
                                    state itself (untracked + staged + delta vs
                                    origin/main) — an agent cannot shrink the
                                    scope below the true push surface
  --fast when full required         gate-integrity files in the diff FORCE
                                    full-tree + mypy; envelope records
                                    mypy_omitted + mypy_omission_justified
  narrower scope than changes need  prepush scope == push surface (union),
                                    never cwd-only
  gate config modified              GATE_INTEGRITY_FILES force full validation
  critical-suite manifest modified  same + manifest parity re-checked by
                                    scripts/ci/gate_parity.py in CI
  pyproject modified                same (config source of truth for BOTH sides)
  changed CI workflow               same (.github/workflows/ci.yml is integrity-
                                    protected; gate_parity.py re-derives the CI
                                    contract from the workflow itself)

Run under pytest; every test materializes an isolated HEAD copy so the real
tree is never touched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable) if (sys := __import__("sys")) else PY


def _materialize_head(tmp: Path) -> Path:
    out = tmp / "tree"
    out.mkdir()
    tar = tmp / "h.tar"
    subprocess.run(
        ["git", "archive", "HEAD", "-o", str(tar)], cwd=REPO, capture_output=True, check=True
    )
    with tarfile.open(tar) as tf:
        tf.extractall(out)
    gd = out / "scripts" / "ci"
    gd.mkdir(parents=True, exist_ok=True)
    for f in ("check_local.py", "verify_critical_suite_manifest.py", "gate_parity.py"):
        shutil.copy(REPO / "scripts" / "ci" / f, gd / f)
    subprocess.run(["git", "init", "-q"], cwd=out, capture_output=True, check=False)
    subprocess.run(["git", "add", "-A"], cwd=out, capture_output=True, check=False)
    subprocess.run(
        ["git", "-c", "user.email=g@t", "-c", "user.name=g", "commit", "-qm", "base"],
        cwd=out,
        capture_output=True,
        check=False,
    )
    return out


@pytest.fixture(scope="module")
def tree_factory(tmp_path_factory):
    counter = {"n": 0}

    def make() -> Path:
        counter["n"] += 1
        return _materialize_head(tmp_path_factory.mktemp(f"bypass{counter['n']}"))

    return make


def _gate(tree: Path, *args: str, timeout: int = 420) -> tuple[int, dict]:
    r = subprocess.run(
        [str(PY), str(tree / "scripts/ci/check_local.py"), "--json", *args],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    return r.returncode, json.loads(r.stdout)


def _write(tree: Path, rel: str, content: str) -> Path:
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")
    return p


# ---------------------------------------------------------------------------
# Bypass: modified gate / manifest / pyproject / CI workflow
# ---------------------------------------------------------------------------
def test_modified_gate_file_forces_full_tree(tree_factory) -> None:
    tree = tree_factory()
    gate = tree / "scripts" / "ci" / "check_local.py"
    text = gate.read_text(encoding="utf-8")
    gate.write_text(text + "\n# agent tweak\n", encoding="utf-8", newline="\n")
    rc, data = _gate(tree, "--prepush", "--json")
    gi = data["gate_integrity"]
    assert gi["full_tree_required"] is True
    assert gi["full_tree_honored"] is True
    assert "scripts/ci/check_local.py" in gi["integrity_files_touched"]


def test_modified_manifest_forces_full_tree(tree_factory) -> None:
    tree = tree_factory()
    man = tree / "tests" / "critical_suite.txt"
    man.write_text(
        man.read_text(encoding="utf-8") + "\ntests/unit/test_ok.py\n",
        encoding="utf-8",
        newline="\n",
    )
    rc, data = _gate(tree, "--prepush", "--json")
    assert data["gate_integrity"]["full_tree_required"] is True
    assert "tests/critical_suite.txt" in data["gate_integrity"]["integrity_files_touched"]


def test_modified_pyproject_forces_full_tree(tree_factory) -> None:
    tree = tree_factory()
    pp = tree / "pyproject.toml"
    pp.write_text(pp.read_text(encoding="utf-8") + "\n# probe\n", encoding="utf-8", newline="\n")
    rc, data = _gate(tree, "--prepush", "--json")
    assert data["gate_integrity"]["full_tree_required"] is True


def test_modified_ci_workflow_forces_full_tree(tree_factory) -> None:
    tree = tree_factory()
    wf = tree / ".github" / "workflows" / "ci.yml"
    wf.write_text(wf.read_text(encoding="utf-8") + "\n# probe\n", encoding="utf-8", newline="\n")
    rc, data = _gate(tree, "--prepush", "--json")
    assert data["gate_integrity"]["full_tree_required"] is True


# ---------------------------------------------------------------------------
# Bypass: --fast is only legal on a docs-only push surface
# ---------------------------------------------------------------------------
def test_prepush_disallows_fast_for_python_changes(tree_factory) -> None:
    tree = tree_factory()
    _write(tree, "src/nexus_scalp/gate_probe_mod.py", "X = 1\n")
    rc, data = _gate(tree, "--prepush", "--json")
    assert data["fast_mode"] is False, "mypy must NOT be skipped when python changed"
    assert data["gate_integrity"]["mypy_omission_justified"] is False


def test_prepush_allows_fast_only_for_docs_only_surface(tree_factory) -> None:
    tree = tree_factory()
    _write(tree, "docs/probe_note.md", "# note\n")
    rc, data = _gate(tree, "--prepush", "--json")
    assert data["fast_mode"] is True
    assert data["gate_integrity"]["mypy_omission_justified"] is True


# ---------------------------------------------------------------------------
# Bypass: narrower scope than the changed files require
# ---------------------------------------------------------------------------
def test_prepush_scope_covers_untracked_and_staged(tree_factory) -> None:
    tree = tree_factory()
    untracked = _write(tree, "src/nexus_scalp/gate_untracked_probe.py", "Y = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tree, capture_output=True, check=False)
    staged = _write(tree, "tests/unit/gate_staged_probe.py", "Z = 3\n")
    rc, data = _gate(tree, "--prepush", "--json")
    files = data["scope"]["files"]
    assert untracked.relative_to(tree).as_posix() in files
    assert staged.relative_to(tree).as_posix() in files


def test_deleted_py_file_reported_not_linted(tree_factory) -> None:
    tree = tree_factory()
    victim = tree / "scripts" / "ci" / "classify_changes.py"
    victim.unlink()
    rc, data = _gate(tree, "--prepush", "--json")
    assert "scripts/ci/classify_changes.py" in data["scope"]["deleted_py_files"]
    lint = next(r for r in data["results"] if r["name"] == "ruff_lint")
    # the deleted file must not appear as a lint target (argv tail check)
    assert "classify_changes.py" not in " ".join(lint["command"])


# ---------------------------------------------------------------------------
# Foreign WIP safety (steer §7)
# ---------------------------------------------------------------------------
def test_foreign_wip_untouched_by_fix(tree_factory) -> None:
    tree = tree_factory()
    foreign = _write(tree, "scratch/gate_foreign.py", "q = 1\nw = 2\n")
    before = foreign.read_text(encoding="utf-8")
    staged_foreign = _write(tree, "scratch/gate_foreign_staged.py", "a = 1\nb  =2\n")
    before_staged = staged_foreign.read_text(encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tree, capture_output=True, check=False)
    _gate(tree, "--prepush", "--fix", "--json")
    assert foreign.read_text(encoding="utf-8") == before
    assert staged_foreign.read_text(encoding="utf-8") == before_staged
    # nothing was staged/committed by the gate
    st = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree, capture_output=True, text=True, check=False
    )
    assert "A " not in st.stdout and "M " not in st.stdout.replace(" M", "")


# ---------------------------------------------------------------------------
# Drift: gate_parity fails when contracts diverge
# ---------------------------------------------------------------------------
def test_gate_parity_detects_taxonomy_drift(tree_factory) -> None:
    tree = tree_factory()
    gate = tree / "scripts" / "ci" / "check_local.py"
    text = gate.read_text(encoding="utf-8")
    # remove the configuration_error status from the gate taxonomy
    mutated = text.replace(
        "passed | failed | errored | skipped | configuration_error",
        "passed | failed | errored | skipped",
    )
    gate.write_text(mutated, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [str(PY), str(tree / "scripts/ci/gate_parity.py"), "--json"],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    payload = json.loads(r.stdout)
    assert payload["gate_parity"] == "FAIL"
    assert any(rec["check"] == "status_taxonomy" and not rec["ok"] for rec in payload["records"])


def test_gate_parity_detects_missing_format_check(tree_factory) -> None:
    tree = tree_factory()
    gate = tree / "scripts" / "ci" / "check_local.py"
    text = gate.read_text(encoding="utf-8")
    mutated = text.replace('["format", "--check"]', '["format"]')
    gate.write_text(mutated, encoding="utf-8", newline="\n")
    r = subprocess.run(
        [str(PY), str(tree / "scripts/ci/gate_parity.py"), "--json"],
        cwd=tree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    payload = json.loads(r.stdout)
    assert payload["gate_parity"] == "FAIL"
    assert any(
        rec["check"] == "no_ci_only_lint_class_omitted" and not rec["ok"]
        for rec in payload["records"]
    )
