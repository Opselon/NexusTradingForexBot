"""The `nexus start --daemon` child command must be entry-point aware.

The daemon child re-enters `start` in a fresh process. Source/dev builds
re-enter through `python -m nexus_scalp.cli.main`; a frozen (PyInstaller)
build must re-enter through the executable's own argv convention because
the frozen entry point IS the Typer app — `-m nexus_scalp.cli.main` was
parsed as an unknown option and the child died instantly with
"No such option: -m" while the parent reported the spawn as OK. Every
packaged release shipped with a silently broken `start --daemon`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexus_scalp.cli.engine_boot import _daemon_command


def test_daemon_command_source_run_uses_module_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd = _daemon_command("shadow", None, False, False)
    assert cmd == [sys.executable, "-m", "nexus_scalp.cli.main", "start", "--mode", "shadow"]


def test_daemon_command_frozen_run_uses_exe_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = _daemon_command("shadow", None, False, False)
    assert cmd == [sys.executable, "start", "--mode", "shadow"]
    # The exact regression: the frozen command must never carry the `-m`
    # module form — the frozen Typer app rejects it ("No such option: -m").
    assert "-m" not in cmd


def test_daemon_command_propagates_flags_in_both_layouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Path("C:/tmp/user.yaml")
    for frozen in (False, True):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        cmd = _daemon_command("live", cfg, True, True)
        assert "--config" in cmd and str(cfg) in cmd
        assert "--gateway" in cmd
        assert "--no-browser" in cmd
        assert cmd[-1] == "--no-browser"
