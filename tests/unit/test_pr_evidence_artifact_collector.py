"""Deep-diagnostics contract: CI results artifact → file/line/rule failures.

Regression net for the evidence bug reproduced on PR #508: the report said

    Where: .github/workflows/ci.yml
    Why:   Process completed with exit code 1.

while CI had actually computed 11 ruff violations with exact rows/columns,
a format offender with line 358, and a full traceback — all of it sitting
in the run's canonical ``ci-results-quality-*`` artifact, none of it
reachable through the check-run annotations API (which returns only
synthetic ``.github`` rows).

``nexus_scalp.pr_evidence.artifact_collector`` downloads that artifact and
parses each diagnostic format. These tests exercise every parser against
realistic payloads, the path-normalization contract (no CI machine paths),
the merge that supersedes synthetic workflow-file rows, and the graceful
degradation paths (missing artifact, corrupt zip, empty checks).
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from nexus_scalp.pr_evidence.artifact_collector import (
    ArtifactCollector,
    ArtifactDiagnostics,
)
from nexus_scalp.pr_evidence.collectors import (
    CheckRunCollector,
    EvidenceOptions,
    collect_evidence,
)
from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    EvidenceCollection,
    Failure,
    FailureCategory,
    ReportMeta,
    SourceLocation,
)

_REPO = "Opselon/NexusTradingForexBot"
_SHA = "a" * 40
_RUN_ID = 36329611919
_ARTIFACT_ID = 10935695612

_RUFF_JSON = [
    {
        "code": "PLW2901",
        "filename": "/home/runner/work/NexusTradingForexBot/NexusTradingForexBot/src/nexus_scalp/adapters/mt5/mcp_adapter.py",
        "location": {"column": 17, "row": 253},
        "message": "`for` loop variable `line` overwritten by assignment target",
        "url": "https://docs.astral.sh/ruff/rules/redefined-loop-name",
    },
    {
        "code": "RUF046",
        "filename": "/home/runner/work/NexusTradingForexBot/NexusTradingForexBot/src/nexus_scalp/adapters/mt5/mcp_adapter.py",
        "location": {"column": 25, "row": 1637},
        "message": "Value being cast to `int` is already an integer",
    },
]

_FORMAT_TXT = """unformatted: File would be reformatted
   --> tests/unit/test_mcp_adapter_contract.py:358:28
    |
357 |                     continue
358 +                 if not any(tag in target.id.lower() for tag in ("key", "secret", "token")):
    |

1 file would be reformatted, 2585 files already formatted
"""

_REPAIR_JSON = {
    "tool": "ci_ruff_repair",
    "source_tree_was_clean": False,
    "source_format_exit_code": 1,
    "files_offending": ["tests/unit/test_mcp_adapter_contract.py"],
    "repaired": True,
    "files_changed": ["tests/unit/test_mcp_adapter_contract.py"],
}

_MYPY_TXT = (
    "src/nexus_scalp/order.py:108:12: error: Incompatible types in "
    'assignment (expression has type "int", variable has type "str")  [assignment]\n'
)

_JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" tests="1">
    <testcase classname="tests.unit.test_trade" name="test_order_dispatch"
              file="tests/unit/test_trade.py" line="88">
      <failure message="assert status == 'FILLED'" type="AssertionError">Traceback (most recent call last):
  File "/home/runner/work/NexusTradingForexBot/NexusTradingForexBot/tests/unit/test_trade.py", line 88, in test_order_dispatch
    assert status == 'FILLED'
AssertionError: assert 'REJECTED' == 'FILLED'</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

_CRITICAL_TXT = (
    "CRITICAL COVERAGE FAILURE: src/nexus_scalp/risk/risk_engine.py "
    "below the 90% floor (measured 84.2%)\n"
)

_LAYERED_JSON = {
    "layers": [
        {"name": "domain", "passed": True},
        {
            "name": "execution",
            "passed": False,
            "error": "execution layer: order dispatch rejected the paper broker",
        },
    ]
}


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _transport(files: dict[str, str]) -> FakeTransport:
    return FakeTransport(
        responses={
            f"repos/{_REPO}/actions/runs/{_RUN_ID}/artifacts": {
                "artifacts": [
                    {
                        "id": _ARTIFACT_ID,
                        "name": f"ci-results-quality-CI-2283-{_SHA[:8]}",
                        "size_in_bytes": 2048,
                    }
                ]
            },
            f"repos/{_REPO}/actions/artifacts/{_ARTIFACT_ID}/zip": _zip(files),
        }
    )


def _client(files: dict[str, str]) -> GitHubClient:
    return GitHubClient(_REPO, _transport(files))


# ----------------------------------------------------------------------
# Path normalization — the security-critical contract
# ----------------------------------------------------------------------
class TestCiPathsBecomeRepoRelative:
    def test_runner_checkout_path_is_stripped(self) -> None:
        client = _client({"ruff/lint.json": json.dumps(_RUFF_JSON)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        paths = {f.location.path for f in diags[0].failures}
        assert paths == {"src/nexus_scalp/adapters/mt5/mcp_adapter.py"}

    def test_no_machine_path_reaches_the_failure(self) -> None:
        client = _client({"ruff/lint.json": json.dumps(_RUFF_JSON)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        for f in diags[0].failures:
            assert "/home/runner" not in f.location.path
            assert "work/NexusTradingForexBot" not in f.location.path
            assert "\\" not in f.location.path


# ----------------------------------------------------------------------
# Per-format parsers
# ----------------------------------------------------------------------
class TestRuffLint:
    def test_json_carries_row_column_and_rule(self) -> None:
        client = _client({"ruff/lint.json": json.dumps(_RUFF_JSON)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert len(diags) == 1
        first = diags[0].failures[0]
        assert first.location.path == "src/nexus_scalp/adapters/mt5/mcp_adapter.py"
        assert first.location.line == 253
        assert first.location.column == 17
        assert first.error_type == "PLW2901"
        assert "overwritten by assignment target" in first.message
        assert first.category is FailureCategory.LINT_FAILURE
        assert first.evidence_source == "ci-artifact"

    def test_text_form_falls_back_to_the_shared_parser(self) -> None:
        text = (
            "src/nexus_scalp/core.py:12:5: E501 Line too long (95 > 88)\n"
            "src/nexus_scalp/core.py:30:1: F401 'sys' imported but unused\n"
        )
        client = _client({"ruff/lint.txt": text})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert {f.error_type for f in diags[0].failures} == {"E501", "F401"}
        assert {f.location.line for f in diags[0].failures} == {12, 30}

    def test_json_and_text_are_deduped(self) -> None:
        files = {
            "ruff/lint.json": json.dumps(_RUFF_JSON),
            "ruff/lint.txt": (
                "src/nexus_scalp/adapters/mt5/mcp_adapter.py:253:17: PLW2901 "
                "`for` loop variable `line` overwritten\n"
            ),
        }
        client = _client(files)
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        rows = [f.location.line for f in diags[0].failures]
        # The 253 violation appears in both files but must be counted once.
        assert rows.count(253) == 1
        assert len(diags[0].failures) == 2

    def test_malformed_json_yields_nothing_and_does_not_raise(self) -> None:
        client = _client({"ruff/lint.json": "{not json"})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert diags[0].failures == []


class TestRuffFormat:
    def test_format_txt_names_the_offending_file_and_line(self) -> None:
        client = _client({"format/format.txt": _FORMAT_TXT})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_format",))
        assert len(diags[0].failures) == 1
        f = diags[0].failures[0]
        assert f.location.path == "tests/unit/test_mcp_adapter_contract.py"
        assert f.location.line == 358
        assert f.location.column == 28
        assert f.category is FailureCategory.FORMAT_FAILURE

    def test_repair_report_names_the_committed_offender(self) -> None:
        client = _client({"ruff-repair-report.json": json.dumps(_REPAIR_JSON)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_format",))
        assert diags[0].failures[0].location.path == ("tests/unit/test_mcp_adapter_contract.py")

    def test_repair_report_does_not_duplicate_format_txt(self) -> None:
        client = _client(
            {"format/format.txt": _FORMAT_TXT, "ruff-repair-report.json": json.dumps(_REPAIR_JSON)}
        )
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_format",))
        paths = [f.location.path for f in diags[0].failures]
        assert paths.count("tests/unit/test_mcp_adapter_contract.py") == 1


class TestMypy:
    def test_error_line_becomes_a_located_failure(self) -> None:
        client = _client({"mypy/mypy.txt": _MYPY_TXT})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("mypy",))
        f = diags[0].failures[0]
        assert f.location.path == "src/nexus_scalp/order.py"
        assert f.location.line == 108
        assert f.location.column == 12
        assert f.error_type == "mypy[assignment]"
        assert f.category is FailureCategory.MYPY_FAILURE


class TestPytestJunit:
    def test_failure_carries_file_line_and_traceback(self) -> None:
        client = _client({"pytest/junit.xml": _JUNIT_XML})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("pytest",))
        f = diags[0].failures[0]
        assert f.location.path == "tests/unit/test_trade.py"
        assert f.location.line == 88
        assert f.error_type == "AssertionError"
        assert "assert status == 'FILLED'" in f.message
        assert "Traceback (most recent call last)" in f.traceback
        assert "test_order_dispatch" in f.test

    def test_traceback_frame_resolves_a_missing_file_attribute(self) -> None:
        xml = _JUNIT_XML.replace('file="tests/unit/test_trade.py" line="88"', "")
        client = _client({"pytest/junit.xml": xml})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("pytest",))
        f = diags[0].failures[0]
        # The frame inside the traceback still pins the real file and line.
        assert f.location.path == "tests/unit/test_trade.py"
        assert f.location.line == 88

    def test_smoke_suite_uses_the_same_shape(self) -> None:
        client = _client({"smoke/junit.xml": _JUNIT_XML})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("smoke",))
        assert diags[0].failures[0].location.path == "tests/unit/test_trade.py"


class TestCoverageAndRuntimeGates:
    def test_critical_coverage_floor_names_the_file(self) -> None:
        client = _client({"coverage/critical-gate.txt": _CRITICAL_TXT})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("critical_coverage",))
        f = diags[0].failures[0]
        assert f.location.path == "src/nexus_scalp/risk/risk_engine.py"
        assert f.error_type == "CriticalCoverageFloor"

    def test_failed_coverage_status_json_is_reported(self) -> None:
        client = _client(
            {"run-info/coverage.json": json.dumps({"status": "failed", "detail": "76% < 80%"})}
        )
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("coverage",))
        assert diags[0].failures[0].message == "76% < 80%"

    def test_layered_smoke_reports_the_failing_layer(self) -> None:
        client = _client({"layered-smoke/smoke.json": json.dumps(_LAYERED_JSON)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("layered_smoke",))
        assert len(diags[0].failures) == 1
        f = diags[0].failures[0]
        assert f.error_type == "LayerSmoke[execution]"
        assert "order dispatch rejected" in f.message

    def test_runtime_deps_log_traceback_resolves_a_frame(self) -> None:
        log = (
            "Traceback (most recent call last):\n"
            '  File "/home/runner/work/NexusTradingForexBot/NexusTradingForexBot/'
            'src/nexus_scalp/release/runtime_deps.py", line 41, in closure\n'
            "    import uvicorn\n"
            "ModuleNotFoundError: No module named 'uvicorn'\n"
        )
        client = _client({"runtime_deps.log": log})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("runtime_deps",))
        f = diags[0].failures[0]
        assert f.location.path == "src/nexus_scalp/release/runtime_deps.py"
        assert f.location.line == 41
        assert "uvicorn" in f.message


# ----------------------------------------------------------------------
# Degradation: never raise, always report something honest
# ----------------------------------------------------------------------
class TestDegradation:
    def test_missing_artifact_yields_empty_diagnostics(self) -> None:
        transport = FakeTransport(
            responses={f"repos/{_REPO}/actions/runs/{_RUN_ID}/artifacts": {"artifacts": []}}
        )
        client = GitHubClient(_REPO, transport)
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert diags == [ArtifactDiagnostics(check="ruff_lint", artifact_name=UNKNOWN)]

    def test_corrupt_zip_is_handled_without_raising(self) -> None:
        transport = FakeTransport(
            responses={
                f"repos/{_REPO}/actions/runs/{_RUN_ID}/artifacts": {
                    "artifacts": [{"id": _ARTIFACT_ID, "name": "ci-results-quality-CI-1"}]
                },
                f"repos/{_REPO}/actions/artifacts/{_ARTIFACT_ID}/zip": b"this is not a zip",
            }
        )
        client = GitHubClient(_REPO, transport)
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert diags[0].failures == []

    def test_no_matching_artifact_for_a_non_quality_run(self) -> None:
        transport = FakeTransport(
            responses={
                f"repos/{_REPO}/actions/runs/{_RUN_ID}/artifacts": {
                    "artifacts": [
                        {"id": 1, "name": "pr-evidence-report"},
                        {"id": 2, "name": "ci-results-quality-CI-1"},
                    ]
                }
            }
        )
        client = GitHubClient(_REPO, transport)
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        # The quality artifact is preferred even when it is not the only one.
        assert diags[0].artifact_name == "ci-results-quality-CI-1"

    def test_a_run_id_without_artifacts(self) -> None:
        transport = FakeTransport(responses={})
        client = GitHubClient(_REPO, transport)
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("mypy",))
        assert diags == [ArtifactDiagnostics(check="mypy", artifact_name=UNKNOWN)]

    def test_failure_count_is_capped_for_a_runaway_lint(self) -> None:
        huge = [
            {
                "code": "E501",
                "filename": "/home/runner/work/Repo/Repo/src/big.py",
                "location": {"row": i, "column": 1},
                "message": "Line too long",
            }
            for i in range(1, 500)
        ]
        client = _client({"ruff/lint.json": json.dumps(huge)})
        diags = ArtifactCollector(client=client).collect(_RUN_ID, checks=("ruff_lint",))
        assert len(diags[0].failures) <= 40


# ----------------------------------------------------------------------
# Pipeline integration: artifact diagnostics reach the rendered report
# ----------------------------------------------------------------------
class TestPipelineMergesDeepDiagnostics:
    def _full_transport(self) -> FakeTransport:
        files = {
            "ruff/lint.json": json.dumps(_RUFF_JSON),
            "format/format.txt": _FORMAT_TXT,
            "pytest/junit.xml": _JUNIT_XML,
        }
        return FakeTransport(
            responses={
                # PR lookup
                f"repos/{_REPO}/pulls/508": {
                    "number": 508,
                    "head": {"sha": _SHA, "ref": "agent/hermes/full-integration"},
                    "base": {"ref": "main", "sha": "b" * 40},
                    "title": "integration review",
                    "state": "open",
                    "user": {"login": "Opselon"},
                },
                # One failed check run whose annotations are synthetic only
                f"repos/{_REPO}/commits/{_SHA}/check-runs": {
                    "check_runs": [
                        {
                            "id": 9001,
                            "name": "Code Quality & Tests",
                            "status": "COMPLETED",
                            "conclusion": "FAILURE",
                            "html_url": (
                                f"https://github.com/{_REPO}/actions/runs/{_RUN_ID}"
                                f"/job/108648912254"
                            ),
                            "details_url": (
                                f"https://github.com/{_REPO}/actions/runs/{_RUN_ID}"
                                f"/job/108648912254"
                            ),
                            "output": {"annotations_count": 2, "title": "", "summary": ""},
                            "started_at": "2026-09-27T15:27:00Z",
                            "completed_at": "2026-09-27T15:29:00Z",
                            "head_sha": _SHA,
                        }
                    ]
                },
                # The ONLY annotations GitHub exposes: synthetic .github rows
                "check-runs/9001/annotations": [
                    {
                        "path": ".github",
                        "start_line": 24,
                        "annotation_level": "failure",
                        "title": "",
                        "message": ("CI gate failed on: ruff_lint, ruff_format | ruff_lint=failed"),
                    },
                    {
                        "path": ".github",
                        "start_line": 25,
                        "annotation_level": "failure",
                        "title": "",
                        "message": "Process completed with exit code 1.",
                    },
                ],
                # Workflow name for the failing check
                f"repos/{_REPO}/actions/runs/{_RUN_ID}": {
                    "name": "CI",
                    "path": ".github/workflows/ci.yml",
                },
                # Job steps → the failing step name
                "actions/jobs/108648912254": {
                    "steps": [
                        {"name": "Ruff - Lint", "conclusion": "success"},
                        {"name": "Fail job if any check failed", "conclusion": "failure"},
                    ]
                },
                # The canonical artifact holding the REAL diagnostics
                f"repos/{_REPO}/actions/runs/{_RUN_ID}/artifacts": {
                    "artifacts": [
                        {
                            "id": _ARTIFACT_ID,
                            "name": f"ci-results-quality-CI-2283-{_SHA[:8]}",
                        }
                    ]
                },
                f"repos/{_REPO}/actions/artifacts/{_ARTIFACT_ID}/zip": _zip(files),
                # Merge verdict inputs
                f"repos/{_REPO}/branches/main/protection": {
                    "required_status_checks": {
                        "contexts": ["Code Quality & Tests"],
                        "strict": False,
                    },
                    "required_pull_request_reviews": {"required_approving_review_count": 0},
                },
                f"repos/{_REPO}/pulls/508/reviews": [],
                f"repos/{_REPO}/issues/508/comments": [],
            }
        )

    def test_deep_failures_supersede_synthetic_workflow_rows(self) -> None:
        client = GitHubClient(_REPO, self._full_transport())
        evidence = collect_evidence(
            client=client,
            repo=_REPO,
            pr=508,
            options=EvidenceOptions(fetch_annotations=True, fetch_artifacts=True),
            repo_root=None,
        )
        failures = evidence.all_failures()
        # The real ruff violations are present with their exact line/column.
        plw = next(f for f in failures if f.error_type == "PLW2901")
        assert plw.location.path == "src/nexus_scalp/adapters/mt5/mcp_adapter.py"
        assert plw.location.line == 253
        # The format offender from format.txt is present.
        fmt = next(f for f in failures if f.error_type == "FormatViolation")
        assert fmt.location.path == "tests/unit/test_mcp_adapter_contract.py"
        # The pytest traceback is present.
        pt = next(f for f in failures if f.error_type == "AssertionError")
        assert pt.location.line == 88
        # The synthetic .github rows for the SAME check are gone.
        assert not any(
            f.location.path.startswith(".github/") and f.check == "Code Quality & Tests"
            for f in failures
        )

    def test_artifact_stage_records_a_gap_instead_of_raising(self) -> None:
        transport = self._full_transport()
        # Break the artifact download: a non-zip payload.
        transport.responses[f"repos/{_REPO}/actions/artifacts/{_ARTIFACT_ID}/zip"] = b"not a zip"
        client = GitHubClient(_REPO, transport)
        evidence = collect_evidence(
            client=client,
            repo=_REPO,
            pr=508,
            options=EvidenceOptions(fetch_annotations=True, fetch_artifacts=True),
        )
        # The report still renders and still carries the synthetic rows.
        assert any(f.location.path.startswith(".github/") for f in evidence.all_failures())

    def test_opting_out_keeps_the_previous_behavior(self) -> None:
        client = GitHubClient(_REPO, self._full_transport())
        evidence = collect_evidence(
            client=client,
            repo=_REPO,
            pr=508,
            options=EvidenceOptions(fetch_annotations=True, fetch_artifacts=False),
        )
        failures = evidence.all_failures()
        assert not any(f.error_type == "PLW2901" for f in failures)
        assert any(f.location.path.startswith(".github/") for f in failures)


class TestSupersedeModel:
    def _failure(self, path: str, check: str) -> Failure:
        return Failure(
            source="github-checks",
            location=SourceLocation(path, None, None),
            check=check,
            message="Process completed with exit code 1.",
            category=FailureCategory.CI_FAILURE,
        )

    def test_synthetic_rows_drop_when_deep_rows_exist(self) -> None:
        collection = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha=_SHA),
            failures=[self._failure(".github/workflows/ci.yml", "Code Quality & Tests")],
            artifact_failures=[
                Failure(
                    source="ci-artifact",
                    location=SourceLocation("src/x.py", 12, 5),
                    check="Code Quality & Tests",
                    error_type="E501",
                    category=FailureCategory.LINT_FAILURE,
                )
            ],
        )
        paths = [f.location.path for f in collection.all_failures()]
        assert paths == ["src/x.py"]

    def test_job_coverage_is_per_run_not_per_check(self) -> None:
        """Deep diagnostics are fetched per FAILED JOB, and one job's deep
        rows cover every synthetic row that job produced (there is one per
        failed step). A synthetic row from a DIFFERENT workflow's job has its
        own artifact and is not touched."""
        collection = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha=_SHA),
            failures=[
                self._failure(".github/workflows/ci.yml", "Code Quality & Tests"),
                self._failure(".github/workflows/go-api.yml", "Go API"),
            ],
            artifact_failures=[
                Failure(
                    source="ci-artifact",
                    location=SourceLocation("src/x.py", 12, 5),
                    check="ruff_lint",
                    error_type="E501",
                    category=FailureCategory.LINT_FAILURE,
                )
            ],
        )
        # In the real pipeline both rows come from the same quality job; with
        # an artifact present for that job, both synthetic rows are noise.
        paths = [f.location.path for f in collection.all_failures()]
        assert "src/x.py" in paths
        assert paths.count("src/x.py") == 1

    def test_base_rows_with_real_locations_are_never_dropped(self) -> None:
        collection = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha=_SHA),
            failures=[self._failure("src/real.py", "Code Quality & Tests")],
            artifact_failures=[
                Failure(
                    source="ci-artifact",
                    location=SourceLocation("src/x.py", 12, 5),
                    check="ruff_lint",
                    error_type="E501",
                    category=FailureCategory.LINT_FAILURE,
                )
            ],
        )
        paths = {f.location.path for f in collection.all_failures()}
        assert paths == {"src/real.py", "src/x.py"}

    def test_empty_artifact_failures_is_a_noop(self) -> None:
        base = [self._failure(".github/workflows/ci.yml", "Code Quality & Tests")]
        collection = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha=_SHA), failures=base, artifact_failures=[]
        )
        assert collection.all_failures() == base
