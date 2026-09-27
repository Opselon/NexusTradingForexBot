"""Code-scanning detail: security findings must reach the report with why + fix.

The live defect this locks down: PR-block CodeQL alerts are indexed under the
PR's MERGE ref (`refs/pull/<n>/merge`), because CodeQL analyses the merged
result. Querying only the head SHA returns an empty list for exactly the
findings that are failing the PR's gate, so the report published a red PR with
no security detail at all. Nothing here names a real PR, file, or rule — the
shapes are generic (spec §1).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.pr_evidence.collectors import CodeQLCollector, EvidenceOptions
from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.models import (
    CheckResult,
    EvidenceCollection,
    FailureCategory,
    ReportMeta,
    Status,
)
from nexus_scalp.pr_evidence.renderer import render_report

_REPO = "Opselon/NexusTradingForexBot"
_SHA = "a" * 40
_PR = 999


def _alert(number: int, *, state: str = "open") -> dict[str, Any]:
    """A generic code-scanning alert shaped like the real API payload."""
    return {
        "number": number,
        "state": state,
        "html_url": f"https://example.invalid/{_REPO}/security/code-scanning/{number}",
        "tool": {"name": "SomeScanner"},
        "rule": {
            "id": "some/rule-class",
            "name": "some/rule-class",
            "description": "Short rule name",
            "full_description": "Long explanation the scanner wrote for this rule.",
            "security_severity_level": "high",
        },
        "most_recent_instance": {
            "ref": f"refs/pull/{_PR}/merge",
            "message": {"text": "The finding text refers to a user-provided value."},
            "location": {
                "path": "src/some/package/module.py",
                "start_line": 934,
                "start_column": 17,
            },
        },
    }


def _client(alerts: list[dict[str, Any]]) -> GitHubClient:
    return GitHubClient(_REPO, FakeTransport(responses={"code-scanning/alerts": alerts}))


class TestMergeRefIsQueried:
    def test_queries_head_sha_and_pr_merge_ref(self) -> None:
        client = _client([_alert(1)])
        CodeQLCollector(client=client).collect(_PR, _SHA)
        urls = " || ".join(url for _m, url, _b in client._transport.recorded)
        assert f"ref={_SHA}" in urls
        # The ref is percent-encoded on the wire; accept either spelling.
        assert f"ref=refs/pull/{_PR}/merge" in urls or f"ref=refs%2Fpull%2F{_PR}%2Fmerge" in urls

    def test_head_only_query_would_have_missed_everything(self) -> None:
        """The bug: an empty head-SHA result must not end the search."""
        seen_refs: list[str] = []

        class _MergeOnly:
            def get(self, url: str, *, timeout: float = 10.0) -> dict[str, Any]:
                if "code-scanning/alerts" in url:
                    ref = url.split("ref=", 1)[1].split("&", 1)[0] if "ref=" in url else ""
                    seen_refs.append(ref)
                    # Head SHA yields nothing; only the merge ref has findings.
                    return [] if ref == _SHA else [_alert(7)]
                return []

            def post(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
                return {}

            def patch(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
                return {}

            def put(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
                return {}

        failures, alerts = CodeQLCollector(client=GitHubClient(_REPO, _MergeOnly())).collect(
            _PR, _SHA
        )
        assert len(alerts) == 1
        assert len(failures) == 1
        assert failures[0].location.line == 934

    def test_union_dedupes_by_alert_number(self) -> None:
        """An alert visible under both refs is reported once."""
        client = _client([_alert(1), _alert(1)])
        failures, _alerts = CodeQLCollector(client=client).collect(_PR, _SHA)
        assert len(failures) == 1


class TestFindingCarriesGitHubProvidedText:
    def _one(self) -> Any:
        failures, _ = CodeQLCollector(client=_client([_alert(1)])).collect(_PR, _SHA)
        return failures[0]

    def test_severity_comes_from_the_payload(self) -> None:
        assert self._one().severity == "high"

    def test_remediation_is_the_scanner_text(self) -> None:
        assert self._one().remediation.startswith("Long explanation the scanner wrote")

    def test_finding_link_is_carried(self) -> None:
        assert "security/code-scanning/1" in self._one().rule_url

    def test_why_text_prefers_the_instance_message(self) -> None:
        assert "user-provided value" in self._one().message

    def test_precise_location_survives(self) -> None:
        failure = self._one()
        assert failure.location.path == "src/some/package/module.py"
        assert (failure.location.line, failure.location.column) == (934, 17)

    def test_category_is_a_security_finding(self) -> None:
        assert self._one().category is FailureCategory.CODEQL_FINDING

    def test_fixed_alerts_are_not_reported(self) -> None:
        failures, _ = CodeQLCollector(client=_client([_alert(1, state="fixed")])).collect(_PR, _SHA)
        assert failures == []


class _MergeOnly2:
    """Transport that only yields findings on the merge ref."""

    def __init__(self, alert: dict[str, Any]) -> None:
        self.alert = alert

    def get(self, url: str, *, timeout: float = 10.0) -> dict[str, Any]:
        base = url.split("?", 1)[0]
        if base.endswith("/pulls/999"):
            return {
                "number": 999,
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
                        "name": "Some Gate",
                        "status": "COMPLETED",
                        "conclusion": "FAILURE",
                        "html_url": "https://example.invalid/1",
                        "head_sha": _SHA,
                        "output": {"annotations_count": 0},
                    }
                ]
            }
        if "code-scanning/alerts" in url:
            ref = url.split("ref=", 1)[1].split("&", 1)[0] if "ref=" in url else ""
            return [] if ref == _SHA else [self.alert]
        if base.endswith("/protection"):
            return {"required_status_checks": {"contexts": ["Some Gate"], "strict": False}}
        if base.endswith("/reviews"):
            return []
        return []

    def post(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
        return {}

    def patch(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
        return {}

    def put(self, url: str, body: Any, *, timeout: float = 10.0) -> dict[str, Any]:
        return {}


class TestEndToEndSecurityVisibility:
    def test_report_explains_the_security_failure(self) -> None:
        """A failing security gate must produce file + line + rule + why + fix."""
        from nexus_scalp.pr_evidence.collectors import collect_evidence

        evidence = collect_evidence(
            client=GitHubClient(_REPO, _MergeOnly2(_alert(11))),
            repo=_REPO,
            pr=_PR,
            options=EvidenceOptions(),
            repo_root=None,
            repo_dir=None,
        )
        body = render_report(evidence)
        assert "What Failed and Why" in body
        assert "some/rule-class" in body
        assert "src/some/package/module.py:934:17" in body
        assert "**Severity:** high" in body
        assert "Long explanation the scanner wrote for this rule." in body
        assert "security/code-scanning/11" in body

    def test_unknown_location_is_not_fabricated(self) -> None:
        alert = _alert(12)
        alert["most_recent_instance"]["location"] = {}
        from nexus_scalp.pr_evidence.collectors import collect_evidence

        evidence = collect_evidence(
            client=GitHubClient(_REPO, _MergeOnly2(alert)),
            repo=_REPO,
            pr=_PR,
            options=EvidenceOptions(),
            repo_root=None,
            repo_dir=None,
        )
        assert evidence.status is Status.FAIL
        assert any(f.location.path == "unknown" for f in evidence.failures)
