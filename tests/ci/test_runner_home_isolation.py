"""Tests for the per-test runner HOME/TMPDIR isolation fixture.

The root conftest's ``_isolate_runner_home_and_tmpdir`` autouse fixture
must make the runner's persistent HOME invisible to every test: a
poisoned ``~/.nexusscalpengine/config/nexus.yaml`` left by an earlier
run must never be seen, and ``tempfile.mkdtemp()`` must never leak a
directory into the runner's real ``/tmp``.

The direct tests assert the fixture's effect in this process. The
poison test re-runs the direct tests in a subprocess whose HOME is a
fake runner home holding a poisoned ``nexus.yaml`` — if the fixture
ever stopped redirecting, the direct tests would see the poison and
fail, so the subprocess run going green proves the isolation end to
end. The subprocess selects ONLY the direct tests by nodeid, so it
cannot recurse into itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THIS_FILE = Path(__file__).resolve()


def test_home_redirected_under_pytest_basetemp(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Path.home() and the user-config path live under pytest's basetemp."""
    basetemp = tmp_path_factory.getbasetemp()
    home = Path.home()
    assert home.is_relative_to(basetemp), (
        f"Path.home()={home} is not under pytest basetemp {basetemp} — "
        "the test sees the runner's real HOME"
    )
    from nexus_scalp.release.paths import app_data_root, get_user_config_path

    assert app_data_root() == home / ".nexusscalpengine"
    assert get_user_config_path().is_relative_to(home)
    # A fresh per-test HOME has no user config: the runner-persistent
    # nexus.yaml (and whatever model path it names) is invisible here.
    assert not get_user_config_path().exists()


def test_tempdir_redirected_under_pytest_basetemp(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """tempfile.* writes land under pytest's basetemp, never in real /tmp."""
    basetemp = tmp_path_factory.getbasetemp()
    assert Path(tempfile.gettempdir()).is_relative_to(basetemp)
    leaked = Path(tempfile.mkdtemp(prefix="area3_probe_"))
    try:
        assert leaked.is_relative_to(basetemp), (
            f"mkdtemp()={leaked} is not under pytest basetemp {basetemp} — "
            "it would leak into the runner's real /tmp"
        )
    finally:
        leaked.rmdir()


def _poisoned_runner_home(tmp_path: Path) -> Path:
    """A fake runner HOME with the exact poison seen on the Dubai box."""
    home = tmp_path / "poisoned-home"
    config_dir = home / ".nexusscalpengine" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "nexus.yaml").write_text(
        "model:\n  model_artifact_path: /tmp/stale-poison/model.pt\n",
        encoding="utf-8",
    )
    stale = tmp_path / "stale-poison"
    stale.mkdir()
    (stale / "model.pt").write_bytes(b"bare legacy model, no sidecars")
    return home


def test_poisoned_runner_home_is_invisible(tmp_path: Path) -> None:
    """Direct tests still pass when HOME holds a poisoned nexus.yaml.

    If the autouse fixture ever stopped redirecting HOME, the nested run
    would resolve get_user_config_path() to the poisoned file and the
    direct tests would fail.
    """
    poison_home = _poisoned_runner_home(tmp_path)
    assert (poison_home / ".nexusscalpengine" / "config" / "nexus.yaml").exists()
    nested_tmp = tmp_path / "nested-tmp"
    nested_tmp.mkdir()
    env = {k: v for k, v in os.environ.items()}
    env["HOME"] = str(poison_home)
    env["USERPROFILE"] = str(poison_home)
    env["LOCALAPPDATA"] = str(poison_home)
    env["APPDATA"] = str(poison_home)
    env["TMPDIR"] = str(nested_tmp)
    env["TEMP"] = str(nested_tmp)
    env["TMP"] = str(nested_tmp)
    nodeids = [
        f"{_THIS_FILE}::test_home_redirected_under_pytest_basetemp",
        f"{_THIS_FILE}::test_tempdir_redirected_under_pytest_basetemp",
    ]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *nodeids,
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "direct isolation tests failed under a poisoned HOME — the "
        f"fixture did not redirect:\n{proc.stdout}\n{proc.stderr}"
    )
