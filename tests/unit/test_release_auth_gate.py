"""Finding 3 — release authorization gate (main-CI evidence binding).

The release workflow's FIRST job queries the GitHub Checks API for the exact
release SHA and refuses to authorize unless every REQUIRED MAIN-CI check
succeeded on that same SHA. These tests pin the fail-closed decision matrix
offline (no network): the gate's `authorize()` + CLI argument handling are
exercised against realistic check-run payloads.

Decision matrix pinned:
  all success                    -> authorized
  one failure                    -> blocked
  one pending/in_progress        -> blocked
  one missing                    -> blocked
  skipped/cancelled/timed_out    -> blocked (NOT equivalent to success)
  duplicate names (re-runs)      -> pending record must not hide a failure
  SHA mismatch (short SHA)       -> CLI refuses (exit 1)
  API error                      -> CLI refuses (exit 1)
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "release" / "release_auth_gate.py"

_spec = importlib.util.spec_from_file_location(
    "release_auth_gate", REPO / "scripts" / "release" / "release_auth_gate.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
REQUIRED_MAIN_CI_CHECKS = _mod.REQUIRED_MAIN_CI_CHECKS
authorize = _mod.authorize


def _run(name: str, conclusion: str | None, status: str = "completed") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def _all_success() -> list[dict]:
    return [_run(n, "success") for n in REQUIRED_MAIN_CI_CHECKS]


def test_all_required_success_is_authorized() -> None:
    ok, failures, missing = authorize(_all_success())
    assert ok and not failures and not missing


def test_single_failure_blocks() -> None:
    runs = _all_success()
    runs[3] = _run(runs[3]["name"], "failure")
    ok, failures, _ = authorize(runs)
    assert not ok
    assert any("failure" in f for f in failures)


def test_pending_check_blocks() -> None:
    runs = _all_success()
    runs[0] = _run(runs[0]["name"], None, status="in_progress")
    ok, failures, _ = authorize(runs)
    assert not ok
    assert any("pending" in f for f in failures)


def test_missing_check_blocks() -> None:
    runs = _all_success()[:-1]  # drop one required check
    ok, _, missing = authorize(runs)
    assert not ok
    assert missing == [REQUIRED_MAIN_CI_CHECKS[-1]]


def test_skipped_is_not_success() -> None:
    runs = _all_success()
    runs[2] = _run(runs[2]["name"], "skipped")
    ok, failures, _ = authorize(runs)
    assert not ok
    assert any("skipped" in f for f in failures)


def test_cancelled_and_neutral_are_not_success() -> None:
    for bad in ("cancelled", "neutral", "timed_out", "action_required"):
        runs = _all_success()
        runs[1] = _run(runs[1]["name"], bad)
        ok, failures, _ = authorize(runs)
        assert not ok, f"conclusion {bad} must not authorize a release"
        assert any(bad in f for f in failures)


def test_rerun_pending_does_not_mask_failure() -> None:
    """A re-run queued after a failure must not replace the failure verdict
    while pending (same check name twice)."""
    name = REQUIRED_MAIN_CI_CHECKS[0]
    runs = [_run(name, "failure"), _run(name, None, status="queued")]
    ok, failures, _ = authorize(runs + [_run(n, "success") for n in REQUIRED_MAIN_CI_CHECKS[1:]])
    assert not ok
    assert any(name in f and "failure" in f for f in failures)


def test_completed_overwrites_pending() -> None:
    name = REQUIRED_MAIN_CI_CHECKS[0]
    runs = [_run(name, None, status="in_progress"), _run(name, "success")]
    ok, _, _ = authorize(runs + [_run(n, "success") for n in REQUIRED_MAIN_CI_CHECKS[1:]])
    assert ok


def _cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    py = REPO / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable
    return subprocess.run(
        [exe, str(GATE), *args], capture_output=True, text=True, cwd=REPO, check=False
    )


def test_cli_refuses_short_sha() -> None:
    r = _cli(["--repo", "Opselon/NexusTradingForexBot", "--sha", "65cd4b0"])
    assert r.returncode == 1
    assert "40-hex" in r.stdout + r.stderr


def test_cli_refuses_when_api_unreachable() -> None:
    # unroutable host + short timeout -> fail closed
    r = _cli(
        [
            "--repo",
            "Opselon/NexusTradingForexBot",
            "--sha",
            "a" * 40,
            "--token",
            "",
        ]
    )
    assert r.returncode == 1
    assert "fail closed" in r.stdout + r.stderr


def test_required_list_matches_live_branch_protection() -> None:
    """The gate's required list MUST equal the repo's live required_status_checks
    (verified via API on 2026-09-11). If protection changes, this test forces a
    conscious update of the release authorization boundary. Order-independent:
    the API order is not guaranteed."""
    live = json.loads(
        (REPO / "artifacts" / "forensics" / "branch_protection_required_checks.json").read_text(
            encoding="utf-8"
        )
    )
    assert sorted(live["contexts"]) == sorted(REQUIRED_MAIN_CI_CHECKS)
