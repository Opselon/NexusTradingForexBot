"""Tests for the multi-PR wave gate (wave-1).

Network-free: every GitHub response is scripted with the package's own
``FakeTransport`` (same pattern as the pr_evidence tests), so the wave
decisions are tested against the exact payload shapes the API returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.models import UNKNOWN
from nexus_scalp.wave_gate import (
    DriftReport,
    LaneSpec,
    LaneState,
    WaveBoard,
    drift_report,
    wave_status,
)

REPO = "Opselon/NexusTradingForexBot"


def _client(responses: dict[str, object]) -> GitHubClient:
    return GitHubClient(REPO, FakeTransport(responses=responses))


def _pr_payload(number: int, *, mergeable=True, mergeable_state="CLEAN", merged=False):
    return {
        "number": number,
        "title": f"lane {number}",
        "state": "open" if not merged else "closed",
        "merged": merged,
        "head": {"sha": f"head{number}", "ref": f"agent/lane{number}"},
        "base": {"sha": "basesha", "ref": "main"},
        "user": {"login": "agent"},
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
        "draft": False,
    }


def _protection(*required):
    return {
        "required_status_checks": {
            "contexts": list(required),
            "strict": False,
        },
        "required_pull_request_reviews": {},
        "restrictions": None,
        "allow_force_pushes": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }


def _check_run(name, conclusion="SUCCESS", status="COMPLETED"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://example/c",
        "head_sha": "headsha",
        "started_at": "2026-09-29T00:00:00Z",
        "completed_at": "2026-09-29T00:01:00Z",
        "output": {"annotations_count": 0, "annotations_url": ""},
        "workflow": {"name": "CI"},
        "check_suite": {"id": 1},
        "app": {"slug": "github-actions"},
        "annotations": [],
    }


def _common(responses: dict, prs=None):
    responses.setdefault(
        "Opselon/NexusTradingForexBot/branches/main/protection",
        _protection("Code Quality & Tests", "Py Tests (windows-latest)"),
    )
    responses.setdefault(
        "Opselon/NexusTradingForexBot/branches/main",
        {"commit": {"sha": "mainsha"}},
    )
    responses.setdefault(
        "Opselon/NexusTradingForexBot/commits/headsha/check-runs",
        {"check_runs": []},
    )
    return responses


def _script_pr(number, responses=None, *, checks=(), **pr_kw):
    """Script one PR: its own checks keyed by its own head sha."""
    payload = _pr_payload(number, **pr_kw)
    head = payload["head"]["sha"]
    out = dict(responses or {})
    _common(out)
    out[f"Opselon/NexusTradingForexBot/pulls/{number}"] = payload
    out[
        f"Opselon/NexusTradingForexBot/commits/{head}/check-runs"
    ] = {"check_runs": list(checks)}
    return out


# ── wave_status: classification ────────────────────────────────────────


def test_all_green_wave_is_ready():
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    res = _script_pr(1, checks=checks)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.state == "READY"
    assert board.lanes[0].state == "GREEN"
    assert board.lanes[0].ready is True
    assert board.lanes[0].mergeable is True
    assert board.required_checks == (
        "Code Quality & Tests",
        "Py Tests (windows-latest)",
    )


def test_one_red_lane_blocks_whole_wave():
    checks = [
        _check_run("Code Quality & Tests", "FAILURE"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    res = _script_pr(1, checks=checks)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.state == "BLOCKED"
    assert board.lanes[0].state == "RED"
    assert "Code Quality & Tests" in board.lanes[0].failed_required
    assert any("Code Quality & Tests" in b for b in board.blockers)


def test_pending_check_is_in_progress_not_blocked():
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", status="IN_PROGRESS", conclusion=None),
    ]
    res = _script_pr(1, checks=checks)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.state == "IN_PROGRESS"
    assert board.lanes[0].state == "PENDING"
    assert "Py Tests (windows-latest)" in board.lanes[0].pending_required


def test_missing_required_context_is_red_not_pending():
    checks = [_check_run("Code Quality & Tests", "SUCCESS")]
    res = _script_pr(1, checks=checks)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    # Py Tests (windows-latest) required by protection but never reported
    assert board.lanes[0].state == "RED"
    assert "Py Tests (windows-latest)" in board.lanes[0].missing_required
    assert board.state == "BLOCKED"


def test_mergeable_false_is_conflict():
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    res = _script_pr(1, checks=checks, mergeable=False, mergeable_state="DIRTY")
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.lanes[0].state == "CONFLICT"
    assert board.state == "BLOCKED"
    assert any("not mergeable" in b for b in board.blockers)


def test_two_lanes_one_fails_wave():
    ok = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    bad = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "FAILURE"),
    ]
    res = _script_pr(1, checks=ok)
    res2 = _script_pr(2, res, checks=bad)
    board = wave_status(
        _client(res2), repo=REPO,
        lanes=[{"name": "a", "pr": 1}, {"name": "b", "pr": 2}],
    )
    assert board.state == "BLOCKED"
    states = {l.name: l.state for l in board.lanes}
    assert states == {"a": "GREEN", "b": "RED"}


def test_merged_lanes_make_merged_wave():
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
    ]
    res = _script_pr(1, checks=checks, merged=True)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.lanes[0].state == "MERGED"
    assert board.state == "MERGED"


def test_evidence_failure_is_unknown_lane_not_crash():
    # PR lookup returns nothing usable
    res = {"x": 1}
    board = wave_status(
        GitHubClient(REPO, FakeTransport(responses=res)),
        repo=REPO, lanes=[{"name": "a", "pr": 1}],
    )
    assert board.lanes[0].state == "UNKNOWN"
    assert board.lanes[0].blockers


def test_empty_wave_is_unknown():
    board = wave_status(_client({"x": 1}), repo=REPO, lanes=[])
    assert board.state == "UNKNOWN"


def test_non_required_failure_is_informational_not_red():
    checks = [
        _check_run("Code Quality & Tests", "SUCCESS"),
        _check_run("Py Tests (windows-latest)", "SUCCESS"),
        _check_run("Unrequired check", "FAILURE"),
    ]
    res = _script_pr(1, checks=checks)
    board = wave_status(_client(res), repo=REPO, lanes=[{"name": "a", "pr": 1}])
    assert board.lanes[0].state == "GREEN"
    assert "Unrequired check" in board.lanes[0].failed_other
    assert board.state == "READY"


# ── drift_report ───────────────────────────────────────────────────────


def test_no_drift_when_base_matches():
    res = _script_pr(1)
    report = drift_report(_client(res), repo=REPO, lane={"name": "a", "pr": 1})
    assert isinstance(report, DriftReport)
    assert report.base_recorded == "basesha"
    assert report.base_current == "mainsha"
    assert report.stale is True  # basesha != mainsha by construction
    assert report.lane == "a"


def test_drift_ahead_count():
    res = _script_pr(1)
    res[
        "Opselon/NexusTradingForexBot/compare/basesha...main"
    ] = {"ahead_by": 3}
    report = drift_report(_client(res), repo=REPO, lane={"name": "a", "pr": 1})
    assert report.ahead_of_base == 3


def test_drift_unreadable_pr_is_unknown_not_raise():
    client = GitHubClient(REPO, FakeTransport(responses={"x": 1}))
    report = drift_report(client, repo=REPO, lane={"name": "a", "pr": 1})
    assert report.base_recorded == UNKNOWN
    assert report.behind is False


# ── LaneSpec / WaveBoard data shapes ───────────────────────────────────


def test_lane_spec_accepts_branch_optional():
    spec = LaneSpec(name="x", pr=5)
    assert spec.branch == UNKNOWN


def test_wave_board_blockers_are_prefixed_by_lane():
    state = LaneState(
        name="a", pr=1, branch="br", state="RED",
        failed_required=("Code Quality & Tests",),
        blockers=("required checks failed: Code Quality & Tests",),
    )
    board = WaveBoard(lanes=(state,))
    assert board.blockers == ["a: required checks failed: Code Quality & Tests"]


def test_status_enum_values_used():
    from nexus_scalp.pr_evidence.models import Status

    values = {member.value for member in Status}
    assert values >= {"PASS", "FAIL", "IN_PROGRESS", "UNKNOWN"}
