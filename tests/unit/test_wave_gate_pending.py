"""
Regression tests for the Wave-1 in-flight CI bug.

The Wave-1 gate classified a check-run whose conclusion was empty or UNKNOWN
as a FAILURE. Right after a push the rollup reports `conclusion: ""` while
the run is QUEUED or IN_PROGRESS, so a healthy wave read RED and the loop
aborted instead of waiting. These pin the corrected behavior.
"""

from __future__ import annotations

from nexus_scalp.wave_gate import wave_status

from .test_wave_gate import REPO, _check_run, _client, _script_pr


def _lane_board(checks):
    res = _script_pr(1, checks=checks)
    return wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])


def test_queued_with_empty_conclusion_is_pending():
    """status=QUEUED, conclusion="" must read PENDING, never RED."""
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", status="QUEUED", conclusion=""),
    ]
    board = _lane_board(checks)
    assert board.lanes[0].state == "PENDING"
    assert "Py Tests (windows-latest)" in board.lanes[0].pending_required
    assert "Py Tests (windows-latest)" not in board.lanes[0].failed_required
    assert board.state == "IN_PROGRESS"


def test_in_progress_with_empty_conclusion_is_pending():
    """status=IN_PROGRESS, conclusion="" must read PENDING, never RED."""
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", status="IN_PROGRESS", conclusion=""),
    ]
    board = _lane_board(checks)
    assert board.lanes[0].state == "PENDING"
    assert board.lanes[0].failed_required == ()


def test_queued_with_null_conclusion_is_pending():
    """status=QUEUED, conclusion=None must read PENDING (the Wave-1 shape)."""
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", status="QUEUED", conclusion=None),
    ]
    board = _lane_board(checks)
    assert board.lanes[0].state == "PENDING"
    assert "Py Tests (windows-latest)" in board.lanes[0].pending_required


def test_completed_with_unknown_conclusion_is_pending():
    """A COMPLETED run whose conclusion is UNKNOWN must not be a failure."""
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "UNKNOWN", status="COMPLETED"),
    ]
    board = _lane_board(checks)
    assert "Py Tests (windows-latest)" in board.lanes[0].pending_required
    assert "Py Tests (windows-latest)" not in board.lanes[0].failed_required


def test_a_real_failure_still_reads_red():
    """The fix must not swallow genuine failures (NEUTRAL/SKIPPED stay ok)."""
    checks = [
        _check_run("Code Quality & Tests", "FAILURE"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    board = _lane_board(checks)
    assert board.lanes[0].state == "RED"
    assert "Code Quality & Tests" in board.lanes[0].failed_required
    assert board.state == "BLOCKED"


def test_neutral_and_skipped_remain_green():
    """NEUTRAL/SKIPPED are non-failures and must stay green-equivalent."""
    checks = [
        _check_run("Code Quality & Tests", "NEUTRAL"),
        _check_run("Py Tests (windows-latest)", "SKIPPED"),
    ]
    board = _lane_board(checks)
    assert board.lanes[0].state == "GREEN"
    assert board.lanes[0].ready is True
