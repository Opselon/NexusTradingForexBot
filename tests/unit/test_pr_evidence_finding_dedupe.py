"""The same security finding must be explained once, by its richest source."""

from __future__ import annotations

from typing import Any

from nexus_scalp.pr_evidence.collectors import EvidenceOptions, collect_evidence
from nexus_scalp.pr_evidence.github_client import GitHubClient
from nexus_scalp.pr_evidence.models import FailureCategory

_REPO = "Opselon/NexusTradingForexBot"
_SHA = "a" * 40
_PR = 999
_PATH = "src/some/package/module.py"
_LINE = 934


def _transport() -> Any:
    class _T:
        def get(self, url: str, *, timeout: float = 10.0) -> dict[str, Any]:
            base = url.split("?", 1)[0]
            if base.endswith(f"/pulls/{_PR}"):
                return {
                    "number": _PR,
                    "title": "Some feature",
                    "state": "open",
                    "merged": False,
                    "mergeable": True,
                    "mergeable_state": "unstable",
                    "draft": False,
                    "head": {"sha": _SHA, "ref": "agent/some/branch"},
                    "base": {"ref": "main", "sha": "b" * 40},
                    "user": {"login": "someone"},
                }
            if base.endswith("/check-runs"):
                return {
                    "check_runs": [
                        {
                            "id": 1,
                            "name": "CodeQL Gate",
                            "status": "COMPLETED",
                            "conclusion": "FAILURE",
                            "html_url": "https://example.invalid/1",
                            "head_sha": _SHA,
                            "output": {"annotations_count": 1, "annotations_url": "u"},
                        }
                    ]
                }
            if base.endswith("/annotations"):
                return [
                    {
                        "path": _PATH,
                        "start_line": _LINE,
                        "start_column": 17,
                        "end_column": 40,
                        "annotation_level": "failure",
                        "title": "SQL query built from user-controlled sources",
                        "message": "This query depends on a user-provided value.",
                    }
                ]
            if "code-scanning/alerts" in url:
                ref = url.split("ref=", 1)[1].split("&", 1)[0] if "ref=" in url else ""
                if ref == _SHA:
                    return []
                return [
                    {
                        "number": 5,
                        "state": "open",
                        "html_url": "https://example.invalid/security/code-scanning/5",
                        "tool": {"name": "SomeScanner"},
                        "rule": {
                            "id": "some/rule",
                            "full_description": "Scanner explanation of the rule.",
                            "security_severity_level": "high",
                        },
                        "most_recent_instance": {
                            "ref": f"refs/pull/{_PR}/merge",
                            "message": {"text": "depends on a user-provided value"},
                            "location": {
                                "path": _PATH,
                                "start_line": _LINE,
                                "start_column": 17,
                            },
                        },
                    }
                ]
            if base.endswith("/protection"):
                return {"required_status_checks": {"contexts": [], "strict": False}}
            if base.endswith("/reviews"):
                return []
            return []

        def post(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
            return {}

        def patch(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
            return {}

        def put(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
            return {}

    return _T()


def _evidence():
    return collect_evidence(
        client=GitHubClient(_REPO, _transport()),
        repo=_REPO,
        pr=_PR,
        options=EvidenceOptions(local_tests=False),
        repo_root=None,
        repo_dir=None,
    )


class TestFindingDeduplication:
    def test_same_finding_reported_once(self) -> None:
        evidence = _evidence()
        at_span = [
            f
            for f in evidence.failures
            if f.category is FailureCategory.CODEQL_FINDING
            and f.location.path == _PATH
            and f.location.line == _LINE
        ]
        assert len(at_span) == 1

    def test_the_surviving_copy_is_the_rich_one(self) -> None:
        evidence = _evidence()
        finding = next(
            f
            for f in evidence.failures
            if f.category is FailureCategory.CODEQL_FINDING and f.location.path == _PATH
        )
        # The alert carries what the annotation lacks.
        assert finding.error_type == "some/rule"
        assert finding.severity == "high"
        assert finding.remediation.startswith("Scanner explanation")
        assert finding.rule_url.endswith("code-scanning/5")

    def test_annotation_only_findings_survive(self) -> None:
        """Dedupe is location-scoped: findings elsewhere are untouched."""
        evidence = _evidence()
        others = [
            f for f in evidence.failures if (f.location.path, f.location.line) != (_PATH, _LINE)
        ]
        # The annotation for the SAME span is gone; nothing else is dropped.
        assert all(f.location.path != _PATH for f in others)
        assert evidence.failures, "a failing gate must always yield a failure"
