"""Duplicate ADR/DEC identifier guard tests (P3).

Mutation targets:
  * disabling/dumbing-down the checker -> the reintroduction test fails;
  * a second content-bearing DEC-0003 file -> the guard fails (proven live
    by the canary case below).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "ci" / "check_decision_ids.py"
DECISIONS = REPO / "agents" / "decisions"

_spec = importlib.util.spec_from_file_location("check_decision_ids", CHECK)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


def _run() -> subprocess.CompletedProcess[str]:
    # check=False is intentional: the test asserts on returncode itself.
    return subprocess.run(
        [sys.executable, str(CHECK)], capture_output=True, text=True, timeout=30, check=False
    )


def test_current_tree_is_clean():
    """The repo's decision ledger must be collision-free right now."""
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr


def test_reintroduced_duplicate_is_caught():
    """Mutation resistance: a second content-bearing file for an existing ID
    must FAIL the guard (and be cleaned up afterwards)."""
    canary = DECISIONS / "DEC-0003-duplicate-canary.md"
    try:
        canary.write_text("# DEC-0003 — Canary\n\nduplicate content", encoding="utf-8")
        r = _run()
        assert r.returncode == 1, "guard must fail on a reintroduced duplicate"
        assert "DEC-0003" in (r.stdout + r.stderr)
    finally:
        canary.unlink(missing_ok=True)
    # clean again after removal
    assert _run().returncode == 0


def test_tombstone_is_not_flagged():
    """A retired-duplicate tombstone (no H1 decision title) is not a duplicate."""
    tmp = Path(__file__).parent / "_tmp_decisions"
    tmp.mkdir(exist_ok=True)
    try:
        (tmp / "DEC-0001-alpha.md").write_text("# DEC-0001 — Alpha\nbody", encoding="utf-8")
        (tmp / "DEC-0001-alpha-retired.md").write_text(
            "RETIRED DUPLICATE — see DEC-0001-alpha\n(no decision H1)", encoding="utf-8"
        )
        assert _mod.find_duplicate_decision_ids(tmp) == []
        # but a second content-bearing file IS flagged
        (tmp / "DEC-0001-beta.md").write_text("# DEC-0001 — Beta\nbody", encoding="utf-8")
        assert len(_mod.find_duplicate_decision_ids(tmp)) == 1
    finally:
        for f in tmp.glob("*.md"):
            f.unlink()
        tmp.rmdir()
