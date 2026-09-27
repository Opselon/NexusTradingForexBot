"""Unknown file paths must be resolved, not printed (spec §4/§6/§13).

Two defects motivated this file:

1. ``.git`` was matched as a string PREFIX, so it also swallowed ``.github``.
   Every CI annotation pointing at a workflow file (``.github/workflows/x.yml``)
   was therefore discarded to ``unknown`` — real, navigable evidence thrown
   away by an over-broad blocklist.
2. GitHub emits a synthetic ``.github`` path for JOB-level failures. That path
   names no real file, so the report printed ``unknown`` even though the failing
   STEP ("Build", "Tests") was available from the job payload and is the actual
   location of the failure.

All fixtures are generic — no PR number, check name, or repo path is special
cased, so the behavior is proven for any PR.
"""

from __future__ import annotations

from nexus_scalp.pr_evidence.collectors import CheckRunCollector
from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.locations import normalize_repo_relative
from nexus_scalp.pr_evidence.models import UNKNOWN, Status
from nexus_scalp.pr_evidence.renderer import render_report

_SHA = "b" * 40
_REPO = "Opselon/NexusTradingForexBot"
_DETAILS = "https://github.com/Opselon/NexusTradingForexBot/actions/runs/5000001/job/6000001"
_WORKFLOW_FILE = ".github/workflows/go-api.yml"


class TestGitPrefixDoesNotSwallowGitHub:
    """`.git` must match a whole segment; `.github` is not a git internal."""

    def test_workflow_file_path_survives_normalization(self):
        assert normalize_repo_relative(_WORKFLOW_FILE, None) == _WORKFLOW_FILE

    def test_github_directory_itself_survives(self):
        assert normalize_repo_relative(".github", None) == ".github"

    def test_git_internals_are_still_rejected(self):
        assert normalize_repo_relative(".git/config", None) == UNKNOWN
        assert normalize_repo_relative(".git/objects/ab/cdef", None) == UNKNOWN

    def test_absolute_workflow_path_is_reanchored_not_dropped(self):
        # Spec §21: re-anchor absolute local paths rather than publish them, and
        # never degrade a `.github/workflows` file to `unknown`.
        got = normalize_repo_relative(
            "C:/Users/someone/repo/.github/workflows/ci.yml", "C:/Users/someone/repo"
        )
        assert got == ".github/workflows/ci.yml"

    def test_dependency_roots_are_still_rejected(self):
        assert normalize_repo_relative("node_modules/pkg/index.js", None) == UNKNOWN

    def test_github_synthetic_placeholder_is_not_a_location(self):
        # GitHub emits the literal `.github` for job-level failures: a directory,
        # not a file. It must never be published as the answer.
        from nexus_scalp.pr_evidence.locations import (
            SYNTHETIC_ANNOTATION_PATH,
            is_usable_path,
        )

        assert SYNTHETIC_ANNOTATION_PATH == ".github"
        assert is_usable_path(".github") is False
        assert is_usable_path(".github/") is False
        assert is_usable_path(UNKNOWN) is False
        assert is_usable_path("") is False
        # A real path under .github IS usable.
        assert is_usable_path(".github/workflows/ci.yml") is True
        assert is_usable_path("src/pkg/mod.py") is True


class TestFailingStepResolvesUnknownPaths:
    """A job-level failure gets the failing step + workflow file as its 'where'."""

    def _client(
        self,
        *,
        annotations: list[dict],
        steps: list[dict],
        check_name: str = "Go API (ubuntu-latest)",
    ) -> GitHubClient:
        raw_check = {
            "id": 9001,
            "name": check_name,
            "status": "completed",
            "conclusion": "failure",
            "details_url": _DETAILS,
            "html_url": _DETAILS,
            "head_sha": _SHA,
            "output": {"annotations_count": len(annotations)},
        }
        transport = FakeTransport(
            responses={
                f"commits/{_SHA}/check-runs": {"check_runs": [raw_check]},
                "actions/jobs/6000001": {"id": 6000001, "steps": steps},
                "actions/runs/5000001": {
                    "id": 5000001,
                    "name": "Go API",
                    "path": _WORKFLOW_FILE,
                },
                "check-runs/9001/annotations": annotations,
            }
        )
        return GitHubClient(_REPO, transport)

    def test_failing_step_and_workflow_file_are_resolved(self):
        collector = CheckRunCollector(
            self._client(
                annotations=[
                    {
                        "path": ".github",
                        "annotation_level": "failure",
                        "title": "Process completed with exit code 1.",
                        "message": "Process completed with exit code 1.",
                    }
                ],
                steps=[
                    {"name": "Set up job", "conclusion": "success"},
                    {"name": "Build", "conclusion": "failure"},
                    {"name": "Test", "conclusion": "skipped"},
                ],
            )
        )
        _checks, failures = collector.collect(_SHA, fetch_annotations=True)
        assert _checks, "the scripted check-run must be collected"
        assert failures, "a failed check must yield at least one failure"
        got = failures[0]
        assert got.step != UNKNOWN
        assert got.step == "Build"
        assert got.workflow_file != UNKNOWN
        # The unusable `.github` placeholder is re-anchored to a REAL file.
        assert got.location.path == _WORKFLOW_FILE

    def test_first_failing_step_wins_over_later_ones(self):
        collector = CheckRunCollector(
            self._client(
                annotations=[],
                steps=[
                    {"name": "Checkout", "conclusion": "failure"},
                    {"name": "Build", "conclusion": "failure"},
                ],
            )
        )
        _checks, failures = collector.collect(_SHA, fetch_annotations=False)
        assert failures[0].step == "Checkout"

    def test_line_number_is_dropped_when_reanchoring(self):
        """A line from the synthetic path must not be carried onto another file."""
        collector = CheckRunCollector(
            self._client(
                annotations=[
                    {
                        "path": ".github",
                        "annotation_level": "failure",
                        "start_line": 1,
                        "start_column": 9,
                        "title": "Process completed with exit code 1.",
                        "message": "Process completed with exit code 1.",
                    }
                ],
                steps=[{"name": "Build", "conclusion": "failure"}],
            )
        )
        _checks, failures = collector.collect(_SHA, fetch_annotations=True)
        got = failures[0]
        assert got.location.path == _WORKFLOW_FILE
        # `.github:1` is not a location in go-api.yml; saying so would be a
        # fabricated precision.
        assert got.location.line is None
        assert got.location.column is None
        assert got.location.rendered() == _WORKFLOW_FILE

    def test_no_steps_degrades_honestly(self):
        collector = CheckRunCollector(self._client(annotations=[], steps=[]))
        _checks, failures = collector.collect(_SHA, fetch_annotations=False)
        # Never invents a step; falls back to the real workflow file.
        assert failures[0].step == UNKNOWN
        assert failures[0].location.path == _WORKFLOW_FILE


class TestRendererSurfacesStepAndWorkflowFile:
    def _report(self, location, **failure_kwargs) -> str:
        from nexus_scalp.pr_evidence.models import (
            CheckResult,
            EvidenceCollection,
            Failure,
            ReportMeta,
        )

        failure = Failure(
            source="github-checks",
            suite="Go API",
            location=location,
            message="Process completed with exit code 1.",
            workflow="Go API",
            job="Go API (ubuntu-latest)",
            check="Go API (ubuntu-latest)",
            evidence_source="github-checks",
            **failure_kwargs,
        )
        evidence = EvidenceCollection(
            meta=ReportMeta(
                pr=999,
                pr_head_sha=_SHA,
                local_head_sha=_SHA,
                ci_head_sha=_SHA,
                title="t",
                base_branch="main",
            ),
            failures=[failure],
            checks=[
                CheckResult(
                    name="Go API (ubuntu-latest)",
                    workflow="Go API",
                    job="Go API (ubuntu-latest)",
                    url="https://gh/1",
                    status="COMPLETED",
                    conclusion="FAILURE",
                )
            ],
        )
        assert evidence.status == Status.FAIL
        return render_report(evidence)

    def test_where_block_reports_step_when_it_is_the_location(self):
        from nexus_scalp.pr_evidence.models import SourceLocation

        body = self._report(
            SourceLocation(_WORKFLOW_FILE, None, None),
            step="Build",
            workflow_file=_WORKFLOW_FILE,
        )
        assert "**Failed step:** `Build`" in body
        assert f"**Where:** `{_WORKFLOW_FILE}`" in body
        assert "**Where:** `unknown`" not in body
        # `Where` already IS the workflow file — repeating it adds nothing.
        assert "**Workflow file:**" not in body

    def test_workflow_file_line_shown_when_location_differs(self):
        from nexus_scalp.pr_evidence.models import SourceLocation

        body = self._report(
            SourceLocation("src/pkg/mod.py", 12, 3),
            step="Build",
            workflow_file=_WORKFLOW_FILE,
        )
        assert "**Where:** `src/pkg/mod.py:12:3`" in body
        assert "**Failed step:** `Build`" in body
        # Distinct from `Where`, so it carries real information.
        assert f"**Workflow file:** `{_WORKFLOW_FILE}`" in body

    def test_heading_never_renders_bare_unknown(self):
        from nexus_scalp.pr_evidence.models import SourceLocation

        body = self._report(SourceLocation.unknown(), step="Build")
        assert "### 1. `Build`" in body
        assert "### 1. `unknown`" not in body

    def test_primary_affected_files_header_never_dangles(self):
        from nexus_scalp.pr_evidence.models import SourceLocation

        body = self._report(SourceLocation.unknown(), step="Build")
        assert "**Primary affected files:** none determined" in body
        # The dangling-header shape from the bug report must not recur.
        assert "**Primary affected files:**\n\n##" not in body


class TestStillNoHardcodedSpecifics:
    """Genericness guard: no PR/check/path literal leaked into production code."""

    def test_no_pr_specific_branching_in_collectors(self):
        import nexus_scalp.pr_evidence.collectors as col

        src = open(col.__file__, encoding="utf-8").read()
        # No PR-specific conditionals: the reporter must behave identically for
        # every PR (spec §27). Docstring prose is fine; code is not.
        for literal in ("== 503", "pr == 492", "pr_number == ", "_PR = 503"):
            assert literal not in src
