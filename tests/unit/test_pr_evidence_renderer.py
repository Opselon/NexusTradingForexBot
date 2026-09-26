"""PR Evidence Reporter — rendering + sanitization tests (spec §§11,13,20,21,17)."""

from __future__ import annotations

from nexus_scalp.pr_evidence.collectors import EvidenceOptions
from nexus_scalp.pr_evidence.github_client import COMMENT_MARKER, FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    CheckResult,
    EvidenceCollection,
    Failure,
    FailureCategory,
    ReportMeta,
    SkippedTest,
    SourceLocation,
    Status,
)
from nexus_scalp.pr_evidence.renderer import render_report, sanitize
from nexus_scalp.pr_evidence.reporter import Reporter

_REPO = "Opselon/NexusTradingForexBot"


def _collection(failures: list[Failure], **kw: object) -> EvidenceCollection:
    meta = ReportMeta(pr=999, pr_head_sha="a" * 40, local_head_sha="a" * 40, ci_head_sha="a" * 40)
    return EvidenceCollection(meta=meta, failures=failures, **kw)  # type: ignore[arg-type]


def _failure(**kw: object) -> Failure:
    base = Failure(
        source="pytest",
        suite="PostgreSQL Integration",
        test="test_pg_pool_config_reaches_pool",
        location=SourceLocation(
            "tests/db/test_pg_pool_config.py", 184, None, "test_pg_pool_config_reaches_pool"
        ),
        production_location=SourceLocation(
            "src/nse/db/postgres.py", 214, None, "build_pool_config"
        ),
        error_type="AssertionError",
        message="expected configured password to reach pool configuration",
        workflow="CI",
        job="postgres-integration",
        check="postgres-integration",
        check_url="https://gh/job",
        commit="a53d48e",
        category=FailureCategory.TEST_FAILURE,
    )
    return Failure(**{**base.__dict__, **kw})  # type: ignore[arg-type]


class TestReportStructure:
    def test_marker_first(self) -> None:
        out = render_report(_collection([_failure()]))
        assert out.startswith(COMMENT_MARKER)

    def test_required_sections_present(self) -> None:
        out = render_report(_collection([_failure()]))
        assert "## 🔴 Affected Files" in out
        assert "## 🔴 Failed Tests" in out
        assert "## 🔗 Revision" in out
        assert "## Overall" in out
        assert "## 🧪 Test Matrix" in out or "## 🏗️ CI Jobs" in out

    def test_failed_test_shows_implementation_file(self) -> None:
        out = render_report(_collection([_failure()]))
        assert "tests/db/test_pg_pool_config.py:184" in out
        assert "src/nse/db/postgres.py:214" in out

    def test_test_vs_production_distinct(self) -> None:
        """Spec §6: never claim the test file caused the failure."""
        f = _failure()
        f = Failure(**{**f.__dict__, "production_location": None})
        out = render_report(_collection([f]))
        assert "Not determined from available evidence" in out

    def test_affected_files_grouped(self) -> None:
        out = render_report(_collection([_failure()]))
        assert "### `src/nse/db/postgres.py`" in out
        assert "### `tests/db/test_pg_pool_config.py`" in out

    def test_empty_evidence_is_unknown(self) -> None:
        out = render_report(_collection([]))
        assert "⚪" in out
        assert "UNKNOWN" in out


class TestBoundedOutput:
    def test_no_log_dump(self) -> None:
        big = "\n".join(f"line {i}" for i in range(2000))
        f = _failure()
        f = Failure(**{**f.__dict__, "traceback": big})
        out = render_report(_collection([f]))
        assert len(out) <= 62000

    def test_traceback_truncation_marker(self) -> None:
        big = "\n".join(f'File "a.py", line {i}, in f' for i in range(500))
        f = _failure()
        f = Failure(**{**f.__dict__, "traceback": big})
        out = render_report(_collection([f]))
        assert out.count("File ") < 500


class TestSecretSanitization:
    def test_connection_string_masked(self) -> None:
        out = sanitize("postgresql://postgres:Re110121@localhost:5432/nexusdb")
        assert "Re110121" not in out
        assert out == "postgresql://postgres:***@localhost:5432/nexusdb"

    def test_token_masked(self) -> None:
        assert sanitize("ghp_" + "a" * 30) == "[REDACTED_GH_TOKEN]"

    def test_local_username_never_published(self) -> None:
        text = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nse\db\postgres.py"
        out = sanitize(text)
        assert "Capsizer" not in out
        assert "C:" not in out

    def test_local_path_reanchored_to_repo_relative(self) -> None:
        text = r"C:\Users\someone\source\repos\NexusTradingForexBot\src\nse\db\postgres.py"
        assert sanitize(text) == "src/nse/db/postgres.py"

    def test_report_body_is_sanitized(self) -> None:
        f = _failure()
        f = Failure(
            **{
                **f.__dict__,
                "message": "password=Re110121 did not match",
            }
        )
        out = render_report(_collection([f]))
        assert "Re110121" not in out


class TestStatusRendering:
    def test_fail(self) -> None:
        out = render_report(_collection([_failure()]))
        assert "🔴 **FAIL**" in out

    def test_blocked(self) -> None:
        coll = _collection(
            [],
            skipped=[
                SkippedTest(
                    test="test_pg_real_connection",
                    location=SourceLocation("tests/integration/test_pg_real_connection.py", 91),
                    reason="NSE_PG_TEST_URL is not configured",
                )
            ],
        )
        coll = EvidenceCollection(
            meta=coll.meta,
            skipped=coll.skipped,
            checks=[
                CheckResult(
                    name="x",
                    status="COMPLETED",
                    conclusion="SUCCESS",
                    workflow="CI",
                    job="x",
                    url="u",
                )
            ],
        )
        out = render_report(coll)
        assert "🟠" in out
        assert "Skipped / Blocked" in out

    def test_pass(self) -> None:
        coll = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha="a" * 40),
            checks=[
                CheckResult(
                    name="x",
                    status="COMPLETED",
                    conclusion="SUCCESS",
                    workflow="CI",
                    job="x",
                    url="u",
                )
            ],
        )
        assert "🟢" in render_report(coll)

    def test_in_progress(self) -> None:
        coll = EvidenceCollection(
            meta=ReportMeta(pr=1, pr_head_sha="a" * 40),
            checks=[
                CheckResult(
                    name="x",
                    status="IN_PROGRESS",
                    conclusion="NEUTRAL",
                    workflow="CI",
                    job="x",
                    url="u",
                )
            ],
        )
        assert "🟡" in render_report(coll)


class TestCodeQLFinding:
    def test_codeql_rendered_with_rule_and_column(self) -> None:
        f = Failure(
            source="codeql",
            suite="codeql",
            location=SourceLocation("src/nse/model/replay.py", 475, 17),
            error_type="py/path-injection",
            message="Unsanitized input flows into a path operation",
            category=FailureCategory.CODEQL_FINDING,
            workflow="Security",
            job="codeql",
            check="CodeQL Analysis",
        )
        out = render_report(_collection([f]))
        assert "py/path-injection" in out
        assert "src/nse/model/replay.py:475:17" in out


class TestHeadMismatchRendering:
    def test_warning_shown_when_heads_differ(self) -> None:
        coll = _collection([_failure()])
        meta = ReportMeta(
            pr=999, pr_head_sha="a" * 40, local_head_sha="b" * 40, ci_head_sha="a" * 40
        )
        coll = EvidenceCollection(meta=meta, failures=coll.failures)
        out = render_report(coll)
        assert "different revisions" in out


class TestEndToEndComment:
    """Spec §18/§22: one comment, no duplicates, across repeated runs."""

    def _reporter(self) -> Reporter:
        sha = "a" * 40
        payloads = {
            "pulls/999": {
                "number": 999,
                "title": "t",
                "head": {"sha": sha, "ref": "b"},
                "base": {"ref": "main"},
                "user": {"login": "u"},
            },
            f"commits/{sha}/check-runs": {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "CI Gate",
                        "status": "COMPLETED",
                        "conclusion": "FAILURE",
                        "html_url": "https://gh/1",
                        "workflow_name": "CI",
                        "output": {"annotations_count": 1},
                        "head_sha": sha,
                    }
                ]
            },
            "check-runs/1/annotations": [
                {
                    "path": "src/nse/db/postgres.py",
                    "start_line": 214,
                    "start_column": 17,
                    "annotation_level": "failure",
                    "message": "Process completed with exit code 1.",
                }
            ],
            "code-scanning/alerts": [],
            "pulls/999/reviews": [],
        }
        return Reporter(
            GitHubClient(_REPO, FakeTransport(responses=payloads)),
            repo_root="/repo",
            options=EvidenceOptions(reviews=False),
        )

    def test_run_twice_one_comment(self) -> None:
        reporter = self._reporter()
        reporter.report_once(999)
        reporter.report_once(999)
        transport = reporter._comments._client._transport  # type: ignore[attr-defined]
        assert isinstance(transport, FakeTransport)
        posts = [m for m in transport.recorded if m[0] == "POST"]
        assert len(posts) == 1
        assert sum(1 for rows in transport.comments.values() for _ in rows) == 1

    def test_report_body_has_affected_files(self) -> None:
        reporter = self._reporter()
        result = reporter.report_once(999)
        assert "src/nse/db/postgres.py" in result.affected_files
