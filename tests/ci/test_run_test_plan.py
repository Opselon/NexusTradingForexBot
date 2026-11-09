"""Regression tests for scripts/ci/run_test_plan.py.

The cold-build path of ensure_native_engine is the one CI actually takes
on a fresh runner, and it is the one local runs never exercise (the
persisted binary under ~/.nse-native verifies and returns early).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import run_test_plan as rtp


def test_ensure_native_engine_builds_from_cold_persist_dir():
    """Regression (2026-11-09): the cmake configure call returned a
    (proc, elapsed) tuple and the code read .returncode off the tuple —
    AttributeError — so the OS lane's SKEW step died before planning.
    It hid locally because the warm persisted binary short-circuits the
    build branch entirely. This test forces the cold path."""
    if shutil.which("cmake") is None:
        pytest.skip("cmake unavailable")
    repo = Path(__file__).resolve().parents[2]
    native_src = repo / "scripts" / "ci" / "native"
    assert (native_src / "CMakeLists.txt").is_file()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # Scratch repo root holding a copy of the native sources only;
        # the persist dir starts empty, so no binary can short-circuit.
        # Exclude build/ + artifacts/: a copied CMakeCache.txt points at
        # the original source dir and cmake refuses to reconfigure.
        shutil.copytree(
            native_src,
            d / "scripts" / "ci" / "native",
            ignore=shutil.ignore_patterns("build", "artifacts"),
        )
        persist = d / "persist"
        binary = rtp.ensure_native_engine(str(d), str(persist))
        assert Path(binary).is_file()
        assert rtp.engine_version(binary) == rtp.ENGINE_VERSION
        assert rtp.selftest_ok(binary)
