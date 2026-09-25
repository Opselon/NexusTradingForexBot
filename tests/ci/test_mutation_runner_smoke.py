"""MUTATION RUNNER SMOKE TESTS (QA hardening mission, P0).

The runner (scripts/qa/run_mutations.py) is itself part of the safety
net: a BROKEN runner must never be able to report a green mutation
campaign. These smoke tests verify the runner's contract WITHOUT
running an expensive mutation campaign:

  * repository root is derived from __file__ (no developer paths)
  * python executable resolution: repo venv or sys.executable, never
    a hardcoded interpreter path
  * mutation targets + batteries exist and anchors are unique
  * --list-targets JSON introspection works from ANY cwd
  * exit semantics: --list-targets exits 0

Cross-platform: paths are exercised through the runner's own resolver
on the host OS; no POSIX-only or Windows-only path literals.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "qa" / "run_mutations.py"


def _load_runner_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_mutations", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Resolver contract
# ---------------------------------------------------------------------------
def test_repo_root_resolves_to_this_repository() -> None:
    rm = _load_runner_module()
    assert rm.REPO == REPO_ROOT, "runner must derive repo root from __file__"
    assert (rm.REPO / "pyproject.toml").exists(), "repo root misidentified"


def test_python_executable_is_venv_or_running_interpreter() -> None:
    rm = _load_runner_module()
    py = Path(rm._repo_python())
    venv_win = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    venv_posix = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_win.exists():
        assert py == venv_win
    elif venv_posix.exists():
        assert py == venv_posix
    else:
        # no venv on disk (CI job interpreter): must fall back to the
        # interpreter RUNNING the runner — never "python"/"python3" literals.
        assert rm._repo_python() == sys.executable


def test_no_developer_absolute_paths_in_runner_source() -> None:
    import ast as _ast

    tree = _ast.parse(RUNNER.read_text(encoding="utf-8"))
    docstrings = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant):
                docstrings.add(id(body[0].value))
    bad = []
    for node in _ast.walk(tree):
        if (
            isinstance(node, _ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            if "C:/Users" in node.value or "C:\\Users" in node.value:
                bad.append(node.value[:60])
    assert not bad, f"runner hardcodes a developer path: {bad}"


# ---------------------------------------------------------------------------
# Catalog integrity (cheap: file reads only, no batteries)
# ---------------------------------------------------------------------------
def test_catalog_targets_and_batteries_exist_with_unique_anchors() -> None:
    rm = _load_runner_module()
    catalog = [*rm.MUTATIONS, *rm.CONTRACT_MUTATIONS]
    assert len(catalog) >= 9, "mutation catalog unexpectedly shrank"
    for m in catalog:
        target = REPO_ROOT / m["target"]
        battery = REPO_ROOT / m["battery"]
        assert target.exists(), f"{m['id']}: target missing: {m['target']}"
        assert battery.exists(), f"{m['id']}: battery missing: {m['battery']}"
        disk = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert disk.count(m["anchor"].replace("\r\n", "\n")) == 1, (
            f"{m['id']}: anchor not unique on current disk state"
        )


# ---------------------------------------------------------------------------
# --list-targets: subprocess smoke from an ARBITRARY working directory
# ---------------------------------------------------------------------------
def test_list_targets_smoke_from_arbitrary_cwd(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--list-targets"],
        cwd=str(tmp_path),  # NOT the repo root
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"--list-targets failed: {proc.stderr[-400:]}"
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "list-targets"
    assert payload["repo_root_ok"] is True
    assert payload["python_is_running_interpreter"] is True
    assert payload["mutations_total"] >= 9
    for entry in payload["catalog"]:
        assert entry["target_exists"], entry["id"]
        assert entry["battery_exists"], entry["id"]
        assert entry["anchor_unique"], entry["id"]


def test_list_targets_output_is_pure_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--list-targets"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)  # raises if stdout is polluted
    ids = {e["id"] for e in payload["catalog"]}
    assert "MUT-RISK-KILLSWITCH" in ids, "contract mutations missing from catalog"
