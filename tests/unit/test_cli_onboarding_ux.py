"""Onboarding/CLI-experience regression suite (2026-09-02 UX pass).

Real-subprocess tests against the actual ``nexus`` console surface (the
acceptance oracle). Offline; foreign-CWD safe (all runs anchored to the repo
root, which is the packaged CLI's normal habitat).

Invariants:
  UX1  `nexus doctor` (human mode) never crashes with UnboundLocalError and
       always ends with the actionable OVERALL/AUTO-FIXABLE/NEXT summary panel.
  UX2  `nexus doctor --json` stdout is pure JSON with `overall` + `checks`.
  UX3  `nexus version` human table reports Commit + Commit Source, and
       `--plain`/`--json` agree on the commit value (commit identity truth).
  UX4  `nexus update check` human output carries the update-awareness block
       (Current commit / distance / up-to-date line) and stays RC=0.
  UX5  `nexus update check --json` stdout is pure JSON (status field present).
  UX6  release_status.commit distance semantics: behind/ahead not swapped
       (regression for the left-right mapping bug fixed in this pass).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not PY.exists(), reason="Windows + repo venv required"
)


def nexus(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "nexus_scalp.cli.main", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class TestDoctorUX:
    def test_human_mode_no_crash_and_summary_present(self):
        """UX1: doctor human mode must not crash and must end with the
        actionable summary (regression: UnboundLocalError on `entries`)."""
        r = nexus("doctor")
        assert r.returncode in (0, 1), r.stdout[-400:] + r.stderr[-400:]
        assert "UnboundLocalError" not in (r.stdout + r.stderr)
        assert "Traceback" not in (r.stdout + r.stderr)
        assert "OVERALL:" in r.stdout
        assert "AUTO-FIXABLE:" in r.stdout
        assert "NEXT:" in r.stdout

    def test_json_mode_pure_json(self):
        """UX2: doctor --json stdout parses as JSON with required keys."""
        r = nexus("doctor", "--json")
        assert r.returncode in (0, 1)
        payload = json.loads(r.stdout.strip())
        assert "overall" in payload and "checks" in payload
        assert isinstance(payload["checks"], list) and payload["checks"]

    def test_json_mode_with_fix_pure_json(self):
        r = nexus("doctor", "--fix", "--yes", "--json")
        assert r.returncode in (0, 1)
        payload = json.loads(r.stdout.strip())
        assert "overall" in payload


class TestVersionIdentity:
    def test_human_version_shows_commit_and_source(self):
        """UX3: commit identity is displayed with its source."""
        r = nexus("version")
        assert r.returncode == 0
        assert "Commit" in r.stdout
        assert "Commit Source" in r.stdout

    def test_plain_and_json_agree_on_commit(self):
        plain = nexus("version", "--plain")
        js = nexus("version", "--json")
        assert plain.returncode == 0 and js.returncode == 0
        payload = json.loads(js.stdout.strip())
        commit = payload.get("commit")
        if commit:  # repo checkouts always resolve; frozen bundles carry stamp
            assert str(commit) in plain.stdout, "plain output must show the same commit"

    def test_json_pure(self):
        r = nexus("version", "--json")
        assert r.returncode == 0
        payload = json.loads(r.stdout.strip())
        assert payload.get("version")


class TestUpdateAwareness:
    def test_human_check_has_awareness_block(self):
        """UX4: update check carries current commit / distance / verdict.
        Depending on the local-vs-remote state the block renders as an
        up-to-date line, a distance row, or a revision-ahead notice."""
        r = nexus("update", "check")
        assert r.returncode == 0
        out = r.stdout
        has_awareness = (
            "Current commit" in out
            or "Commit distance" in out
            or "up to date" in out
            or "ahead of origin" in out
        )
        assert has_awareness, out[-600:]
        assert "Last checked:" in out

    def test_json_check_pure_json(self):
        """UX5: update check --json is pure JSON with a status field."""
        r = nexus("update", "check", "--json")
        assert r.returncode == 0
        payload = json.loads(r.stdout.strip())
        assert "status" in payload


class TestCommitDistanceSemantics:
    def test_counts_not_swapped(self):
        """UX6: left-right mapping — a local tree strictly ahead of
        origin/main must report ahead>0, behind==0 (regression for the
        swapped left/right mapping bug)."""
        from nexus_scalp.release.release_status import _git_counts

        behind, ahead = _git_counts()
        if behind is None:
            pytest.skip("no origin/main ref locally")
        # Cross-check against git directly for THIS tree state.
        out = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            pytest.skip("git rev-list unavailable")
        left, right = (int(x) for x in out.stdout.split())
        assert behind == right and ahead == left
        # The invariant the bug violated:
        assert (behind == 0) == (right == 0)
