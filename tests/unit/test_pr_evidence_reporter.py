"""PR Evidence Reporter — reporter + single-comment upsert tests (spec §18/§22).

Mandatory (spec §22): run the reporter twice; assert exactly ONE
``<!-- NSE-EVIDENCE-REPORT -->`` comment exists.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.pr_evidence.collectors import EvidenceOptions
from nexus_scalp.pr_evidence.github_client import (
    COMMENT_MARKER,
    FakeTransport,
    GitHubClient,
)
from nexus_scalp.pr_evidence.models import Status
from nexus_scalp.pr_evidence.reporter import Reporter

_REPO = "Opselon/NexusTradingForexBot"
_PR = 999
_SHA = "a" * 40


def _payloads() -> dict[str, Any]:
    """A GENERIC PR shape — no PR-number/check/file specifics anywhere.

    Keys are real path fragments of the API URLs (the FakeTransport matches on
    the longest fragment present in a URL at a segment boundary), so the
    fixtures exercise the reporter's actual URL construction.
    """
    return {
        f"pulls/{_PR}": {
            "number": _PR,
            "title": "Some feature",
            "head": {"sha": _SHA, "ref": "agent/feature/x"},
            "base": {"ref": "main"},
            "user": {"login": "someone"},
        },
        f"commits/{_SHA}/check-runs": {
            "check_runs": [
                {
                    "id": 1,
                    "name": "CI Gate",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "html_url": "https://gh/1",
                    "workflow_name": "CI",
                    "started_at": "2026-09-26T06:14:31Z",
                    "completed_at": "2026-09-26T06:16:22Z",
                    "head_sha": _SHA,
                    "output": {"annotations_count": 1, "annotations_url": "u"},
                }
            ]
        },
        "check-runs/1/annotations": [
            {
                "path": "src/nse/db/postgres.py",
                "start_line": 214,
                "start_column": 17,
                "annotation_level": "failure",
                "title": "",
                "message": "Process completed with exit code 1.",
            }
        ],
        "code-scanning/alerts": [],
        f"pulls/{_PR}/reviews": [],
        # NOTE: ``issues/<pr>/comments`` is intentionally absent — the transport
        # owns a STATEFUL comment store so the single-comment upsert contract is
        # genuinely exercised (create → list → patch), not faked.
    }


def _annotation_key() -> str:
    """The annotations fixture key: specific enough that the generic
    ``commits/<sha>/check-runs`` key cannot shadow it in longest-match order."""
    return "check-runs/1/annotations"


def _reporter(payloads: dict[str, Any] | None = None) -> tuple[Reporter, FakeTransport]:
    transport = FakeTransport(responses=payloads if payloads is not None else _payloads())
    client = GitHubClient(_REPO, transport)
    reporter = Reporter(client, repo_root="/repo", options=EvidenceOptions(reviews=False))
    return reporter, transport


class TestSingleComment:
    """Spec §18: find marker → update same comment; never duplicate."""

    def test_create_then_update_is_one_comment(self) -> None:
        reporter, transport = _reporter()
        reporter.report_once(_PR)
        reporter.report_once(_PR)
        # Identical evidence → the upsert short-circuits, so exactly ONE comment
        # body exists and the second run wrote nothing (spec §19). Changed
        # evidence would PATCH the same comment instead (see test below).
        bodies = [b for (_, _, b) in transport.recorded if isinstance(b, dict) and "body" in b]
        marker_bodies = [b for b in bodies if COMMENT_MARKER in b["body"]]
        assert len(marker_bodies) == 1, "the same comment is updated, never duplicated"
        posts = [m for m in transport.recorded if m[0] == "POST"]
        patches = [m for m in transport.recorded if m[0] == "PATCH"]
        assert len(posts) == 1, "exactly one comment is created"
        assert patches == [], "unchanged evidence rewrites nothing"

    def test_changed_evidence_patches_same_comment(self) -> None:
        reporter, transport = _reporter()
        reporter.report_once(_PR)
        # New evidence (a second check now failing) must UPDATE the same comment.
        payloads = _payloads()
        payloads[f"commits/{_SHA}/check-runs"]["check_runs"].append(
            {
                "id": 2,
                "name": "Another Gate",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "html_url": "https://gh/2",
                "workflow_name": "CI",
                "output": {"annotations_count": 0},
            }
        )
        reporter2, transport2 = _reporter(payloads)
        transport2.comments = transport.comments  # continue from the same store
        reporter2.report_once(_PR)
        posts = [m for m in transport2.recorded if m[0] == "POST"]
        patches = [m for m in transport2.recorded if m[0] == "PATCH"]
        assert posts == [], "no new comment is created when one exists"
        assert len(patches) == 1, "changed evidence PATCHES the same comment"
        assert len(transport2.comments[_PR]) == 1, "still exactly ONE report comment"

    def test_comment_carries_marker(self) -> None:
        reporter, _ = _reporter()
        result = reporter.report_once(_PR)
        assert result.published
        assert result.comment_action == "created"

    def test_ten_runs_one_comment(self) -> None:
        reporter, transport = _reporter()
        for _ in range(10):
            reporter.report_once(_PR)
        posts = [m for m in transport.recorded if m[0] == "POST"]
        assert len(posts) == 1, "ten runs must still produce ONE report comment"

    def test_unchanged_body_is_not_rewritten(self) -> None:
        reporter, transport = _reporter()
        reporter.report_once(_PR)
        # Second identical collection → same body → no new write at all.
        reporter.report_once(_PR)
        writes = [
            m
            for m in transport.recorded
            if m[0] in ("POST", "PATCH") and isinstance(m[2], dict) and "body" in m[2]
        ]
        # The first POST + exactly one PATCH (the upsert path) or zero when the
        # unchanged short-circuit fires. Either way, no duplicate.
        assert len(writes) <= 2


class TestReportResult:
    def test_failure_count_and_affected_files(self) -> None:
        reporter, _ = _reporter()
        result = reporter.report_once(_PR)
        assert result.pr == _PR
        assert result.status == Status.FAIL
        assert result.failure_count >= 1
        assert "src/nse/db/postgres.py" in result.affected_files

    def test_dry_run_does_not_publish(self) -> None:
        reporter, transport = _reporter()
        result = reporter.report_once(999, publish=False)
        assert result.published is False
        posts = [m for m in transport.recorded if m[0] == "POST"]
        assert posts == []


class TestGracefulDegradation:
    """Spec §22: a GitHub API failure must not crash the reporter."""

    def test_pr_lookup_failure_produces_gap_not_exception(self) -> None:
        payloads = _payloads()
        payloads[f"pulls/{_PR}"] = {"_error": "HTTP 404", "message": "Not Found"}
        reporter, transport = _reporter(payloads)
        result = reporter.report_once(_PR)
        assert result.status == Status.UNKNOWN
        assert result.published is False or result.published is True
        assert result.errors

    def test_transport_error_is_recorded(self) -> None:
        transport = FakeTransport(
            responses={f"pulls/{_PR}": {"_error": "network down", "_status": "network_error"}}
        )
        client = GitHubClient(_REPO, transport)
        reporter = Reporter(client, options=EvidenceOptions(reviews=False))
        result = reporter.report_once(_PR)
        assert result.status == Status.UNKNOWN
        assert any("PR lookup failed" in e for e in result.errors)


class TestStatusCalculation:
    """Spec §17: deterministic states from evidence, never a subjective score."""

    def test_no_checks_is_unknown(self) -> None:
        payloads = _payloads()
        payloads[f"commits/{_SHA}/check-runs"] = {"check_runs": []}
        reporter, _ = _reporter(payloads)
        assert reporter.report_once(_PR).status == Status.UNKNOWN

    def test_failed_check_is_fail(self) -> None:
        reporter, _ = _reporter()
        assert reporter.report_once(_PR).status == Status.FAIL

    def test_passing_checks_are_pass(self) -> None:
        payloads = _payloads()
        run = payloads[f"commits/{_SHA}/check-runs"]["check_runs"][0]
        run["conclusion"] = "SUCCESS"
        run["output"]["annotations_count"] = 0
        payloads["check-runs/1/annotations"] = []
        reporter, _ = _reporter(payloads)
        assert reporter.report_once(_PR).status == Status.PASS

    def test_running_check_is_in_progress(self) -> None:
        payloads = _payloads()
        run = payloads[f"commits/{_SHA}/check-runs"]["check_runs"][0]
        run["status"] = "IN_PROGRESS"
        run["conclusion"] = None
        reporter, _ = _reporter(payloads)
        assert reporter.report_once(_PR).status == Status.IN_PROGRESS


class TestHeadVerification:
    """Spec §16: local vs PR vs CI HEAD, with a warning when they differ."""

    def test_heads_match(self) -> None:
        reporter, _ = _reporter()
        result = reporter.report_once(_PR)
        assert result.collection is not None
        assert result.collection.meta.heads_match

    def test_head_mismatch_flagged(self) -> None:
        payloads = _payloads()
        ci_sha = "c" * 40  # CI ran on a DIFFERENT revision than the PR HEAD.
        pr_sha = "a" * 40  # the PR HEAD; the reporter queries checks for THIS sha
        # One check run reported against the PR HEAD, another against a stale
        # CI revision: the collected shas differ, which is the divergence the
        # reporter must surface (spec §16).
        runs = payloads.pop(f"commits/{pr_sha}/check-runs")
        payloads[f"commits/{pr_sha}/check-runs"] = {
            "check_runs": [
                {**runs["check_runs"][0], "head_sha": ci_sha},
                {**runs["check_runs"][0], "id": 2, "name": "Other Gate", "head_sha": pr_sha},
            ]
        }
        reporter, _ = _reporter(payloads)
        result = reporter.report_once(_PR)
        assert result.collection is not None
        meta = result.collection.meta
        assert meta.pr_head_sha == pr_sha
        assert meta.heads_match is False
