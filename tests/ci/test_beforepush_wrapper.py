"""Exercise the real shell entrypoint, not a copied gate implementation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "beforePush.sh"
pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX executable wrapper; Windows uses beforePush.ps1",
)


def test_wrapper_is_valid_bash() -> None:
    result = subprocess.run(
        ["bash", "-n", str(WRAPPER)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr


def test_real_executable_reaches_gate_help(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(WRAPPER), "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--prepush" in result.stdout
    assert "--staged" in result.stdout
    assert "usage:" in result.stdout


@pytest.mark.parametrize("exit_code", [0, 1, 2, 7])
def test_wrapper_forwards_arguments_and_exit_status(tmp_path: Path, exit_code: int) -> None:
    root = tmp_path / "repository with spaces"
    gate = root / "scripts" / "ci" / "check_local.py"
    gate.parent.mkdir(parents=True)
    # Spy only on the delegation boundary; do not mirror gate logic.
    gate.write_text(
        f"import json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    wrapper = root / "beforePush.sh"
    shutil.copy2(WRAPPER, wrapper)
    result = subprocess.run(
        [str(wrapper), "--staged", "argument with spaces"],
        cwd=tmp_path,
        env={**os.environ, "PYTHON_BIN": sys.executable},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == [
        "--prepush",
        "--staged",
        "argument with spaces",
    ]
