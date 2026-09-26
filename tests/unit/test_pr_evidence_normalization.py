"""Normalization regressions: annotation level, check conclusion, locations.

These guard the DEFECTS found while validating the reporter against a real PR:
warning-level annotations must never be counted as failures, lowercase check
conclusions (``gh api`` shape) must map to the model's uppercase vocabulary, and
a line number must never be rendered without a known path.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.pr_evidence.collectors import (
    AnnotationCollector,
    CheckRunCollector,
    EvidenceOptions,
    collect_evidence,
)
from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.models import UNKNOWN, CheckAnnotation, CheckResult, SourceLocation

_REPO = "Opselon/NexusTradingForexBot"
_SHA = "a" * 40


def _check(**kw: Any) -> CheckResult:
    base = {
        "name": "Some Gate",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
        "workflow": "CI",
        "job": "some-gate",
        "url": "https://gh/1",
        "head_sha": _SHA,
    }
    return CheckResult(**{**base, **kw})


class TestAnnotationLevel:
    def test_warning_annotation_is_not_a_failure(self) -> None:
        anno = CheckAnnotation(
            path="src/pkg/mod.py",
            start_line=12,
            start_column=3,
            level="WARNING",
            title="",
            message="This is advisory only.",
        )
        failures = AnnotationCollector(repo_root=None).collect(_check(), (anno,))
        assert failures == []

    def test_notice_annotation_is_not_a_failure(self) -> None:
        anno = CheckAnnotation(
            path="src/pkg/mod.py",
            start_line=12,
            start_column=3,
            level="NOTICE",
            title="",
            message="FYI",
        )
        assert AnnotationCollector(repo_root=None).collect(_check(), (anno,)) == []

    def test_failure_annotation_is_a_failure(self) -> None:
        anno = CheckAnnotation(
            path="src/pkg/mod.py",
            start_line=12,
            start_column=3,
            level="FAILURE",
            title="",
            message="boom",
        )
        failures = AnnotationCollector(repo_root=None).collect(_check(), (anno,))
        assert len(failures) == 1
        assert failures[0].location.path == "src/pkg/mod.py"


class TestConclusionNormalization:
    def test_lowercase_conclusion_is_normalized(self) -> None:
        """``gh api`` lower-cases conclusions for some suites; render must agree."""
        payloads = {
            f"commits/{_SHA}/check-runs": {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "Gate",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://gh/1",
                        "workflow_name": "CI",
                        "output": {"annotations_count": 0},
                        "head_sha": _SHA,
                    }
                ]
            }
        }
        client = GitHubClient(_REPO, FakeTransport(responses=payloads))
        checks, _ = CheckRunCollector(client=client).collect(_SHA)
        assert checks[0].conclusion == "FAILURE"
        assert checks[0].status == "COMPLETED"
        assert checks[0].is_failure is True


class TestUnknownPathNeverCarriesALine:
    def test_unknown_path_renders_without_line(self) -> None:
        loc = SourceLocation(UNKNOWN, 218, None)
        assert loc.rendered() == UNKNOWN

    def test_known_path_still_renders_line_and_column(self) -> None:
        loc = SourceLocation("src/pkg/mod.py", 218, 7)
        assert loc.rendered() == "src/pkg/mod.py:218:7"

    def test_unknown_location_is_not_an_affected_file(self) -> None:
        payloads = {
            "pulls/999": {
                "number": 999,
                "title": "t",
                "head": {"sha": _SHA, "ref": "b"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
            },
            f"commits/{_SHA}/check-runs": {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "Gate",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://gh/1",
                        "workflow_name": "CI",
                        "output": {"annotations_count": 1},
                        "head_sha": _SHA,
                    }
                ]
            },
            "check-runs/1/annotations": [
                {
                    "path": "",
                    "start_line": 218,
                    "annotation_level": "failure",
                    "message": "Process completed with exit code 1.",
                }
            ],
            "code-scanning/alerts": [],
            "pulls/999/reviews": [],
        }
        client = GitHubClient(_REPO, FakeTransport(responses=payloads))
        evidence = collect_evidence(
            client=client,
            repo=_REPO,
            pr=999,
            options=EvidenceOptions(reviews=False),
            repo_root=None,
        )
        paths = [p for f in evidence.all_failures() for p in f.affected_paths()]
        assert UNKNOWN not in paths
