"""Job-log deep extraction — the ``github-advanced-security`` regression.

PR #594's report showed, for a failed ``github-advanced-security`` check:

    Where: dynamic/agents/github-advanced-security
    Why:   Process completed with exit code 1.

The check publishes no artifact and its only annotation is a synthetic
``.github`` path. The real cause — GitHub's Copilot code-scanning agent hit
``CAPIError: 400 The requested model is not supported`` — exists only in the
Actions job log. These tests pin that extraction.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from nexus_scalp.pr_evidence.collectors import (
    CheckRunCollector,
    EvidenceOptions,
    collect_evidence,
)
from nexus_scalp.pr_evidence.github_client import FakeTransport, GitHubClient
from nexus_scalp.pr_evidence.job_logs import (
    LogDiagnostics,
    StepDiagnostic,
    is_agent_check,
    parse_job_log,
)
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    EvidenceCollection,
    Failure,
    ReportMeta,
)

_SHA = "fd6e1fda58ffa5c2f8e991a6c2b4a418b04f2ebc"

# --------------------------------------------------------------------------
# Real-shape Copilot agent log (trimmed to the essential rows).
# --------------------------------------------------------------------------

COPILOT_LOG = """2026-09-29T19:57:31.4404493Z shell: /usr/bin/bash --noprofile --norc -e -o pipefail {0}
2026-09-29T19:57:55.7724527Z Running detector: ccr_security[EnableRestrictedResultPublication=true]
2026-09-29T19:57:55.7728609Z Resolved repo directory: /home/runner/work/NexusTradingForexBot/NexusTradingForexBot
2026-09-29T19:57:58.3100661Z Creating copilot-sdk session with model: claude-opus-5[ReasoningEffort=medium]
2026-09-29T19:57:59.2160267Z Failed to read cca-setup error log: ENOENT: no such file or directory, open '/home/runner/work/_temp/autofind-cca-setup-error.txt'
2026-09-29T19:57:59.2170982Z Error creating PR review request: SessionModelError: Execution failed: CAPIError: 400 The requested model is not supported. (Request ID: B434:3CF96D:4539E:51D9F:6ABC1846); cause: Error: Execution failed: CAPIError: 400 The requested model is not supported.
2026-09-29T19:57:59.2984963Z $t [SessionModelError]: Execution failed: CAPIError: 400 The requested model is not supported.
2026-09-29T19:57:59.2987038Z   errorType: 'query',
2026-09-29T19:57:59.2984963Z   [cause]: CAPIError: 400 The requested model is not supported.
2026-09-29T19:57:59.3006114Z       at t.fromAPIError (file:///home/runner/.cache/copilot/pkg/linux-x64/1.0.80/app.js:2323:57048)
2026-09-29T19:57:59.3021346Z }
2026-09-29T19:57:59.7394473Z ##[error]Process completed with exit code 1.
2026-09-29T19:57:59.7620668Z Cleaning up...
"""


class TestParseJobLog:
    def test_extracts_the_real_cause_not_the_exit_code(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        assert not d.is_empty
        lines = [x.line for x in d.diagnostics]
        # The exit-code line must NOT be the surfaced cause.
        assert not any("Process completed with exit code" in ln for ln in lines)
        # The model error must be.
        assert any(
            "The requested model is not supported" in ln and "CAPIError: 400" in ln for ln in lines
        )

    def test_marks_infrastructure_not_pr_code(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        assert any(x.infrastructure for x in d.diagnostics)

    def test_exception_block_becodes_traceback(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        assert "app.js" in d.traceback
        # Bounded (spec §20: never dump whole logs).
        assert len(d.traceback) < 2000

    def test_shell_scaffolding_is_not_surfaced(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        for x in d.diagnostics:
            assert "RETRY_COUNT" not in x.line
            assert "FALLBACK_FILE" not in x.line
            assert "Cleaning up" not in x.line
            assert "cca-setup error log" not in x.line
            assert "fallback error annotations" not in x.line

    def test_echo_lines_drop(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        lines = [x.line for x in d.diagnostics]
        # ``[cause]:`` and ``$t [`` repeat the primary exception.
        assert not any(ln.startswith("[cause]:") for ln in lines)
        assert not any(ln.startswith("$t [") for ln in lines)

    def test_redacts_secrets(self) -> None:
        log = (
            "2026-09-29T19:57:31Z shell: bash\n"
            "Error: download failed token=AKIAIOSFODNN7EXAMPLE extra\n"
        )
        d = parse_job_log(log, job_name="Build")
        for x in d.diagnostics:
            assert "AKIAIOSFODNN7EXAMPLE" not in x.line
        assert "AKIAIOSFODNN7EXAMPLE" not in d.traceback

    def test_empty_log_is_empty(self) -> None:
        d = parse_job_log("", job_name="Build")
        assert d.is_empty

    def test_plain_gha_failure_still_surfaces(self) -> None:
        log = (
            "2026-09-29T19:57:31Z ##[group]Run make test\n"
            "2026-09-29T19:57:32Z AssertionError: 1 == 2\n"
            "2026-09-29T19:57:32Z ##[error]Process completed with exit code 1.\n"
        )
        d = parse_job_log(log, job_name="Tests")
        lines = [x.line for x in d.diagnostics]
        assert any("AssertionError" in ln for ln in lines)

    def test_max_log_bytes_caps_huge_logs(self) -> None:
        huge = "2026-09-29T19:57:31Z noise\n" * 100_000
        d = parse_job_log(huge, job_name="Build")
        # Must not raise and must stay bounded.
        assert len(d.diagnostics) <= 6


class TestIsAgentCheck:
    def test_copilot_names(self) -> None:
        assert is_agent_check("github-advanced-security")
        assert is_agent_check("Code scanning AI findings on PR #594")

    def test_plain_checks_are_not_agent_checks(self) -> None:
        assert not is_agent_check("Code Quality & Tests")
        assert not is_agent_check("Py Tests (windows-latest)")
        assert not is_agent_check(UNKNOWN)
        assert not is_agent_check("")


class TestToFailures:
    def test_failure_shape(self) -> None:
        d = parse_job_log(COPILOT_LOG, job_name="Processing Request (Linux)")
        failures = d.to_failures(
            "GitHub Advanced Security",
            "github-advanced-security",
            "github-advanced-security",
            "https://api.github.com/repos/x/check-runs/1",
        )
        assert failures, "expected at least one failure row"
        f = failures[0]
        assert f.source == "job-logs"
        assert f.evidence_source == "job-logs"
        assert f.error_type == "SessionModelError"
        assert f.infrastructure is True
        # The failed STEP is the where — not a repo file that does not exist.
        assert f.location.path == "Processing Request"
        assert f.traceback != UNKNOWN

    def test_runner_scratch_js_is_not_a_repo_path(self) -> None:
        d = LogDiagnostics(
            check_run_id=1,
            job_name="Processing Request",
            failed_step="Processing Request",
            diagnostics=[
                StepDiagnostic(
                    label="Raised exception",
                    line="Error at /home/runner/work/_temp/x-action-main/dist/y.js:1101:9705",
                )
            ],
        )
        failures = d.to_failures("w", "j", "c", "u")
        assert failures[0].location.path == "Processing Request"


# --------------------------------------------------------------------------
# Pipeline: collect_evidence fetches the job log for agent checks.
# --------------------------------------------------------------------------


class TestPipelineFetchesJobLog:
    def _client(self) -> GitHubClient:
        transport = FakeTransport(
            responses={
                # FakeTransport keys match on URL path SEGMENTS.
                "pulls/594": {
                    "number": 594,
                    "head": {"sha": _SHA, "ref": "agent/x"},
                    "base": {"ref": "main"},
                    "title": "t",
                    "user": {"login": "u"},
                    "state": "open",
                },
                "check-runs": {
                    "total_count": 1,
                    "check_runs": [
                        {
                            "id": 109592443880,
                            "name": "github-advanced-security",
                            "status": "completed",
                            "conclusion": "failure",
                            "app": {"name": "GitHub Actions"},
                            "html_url": "https://github.com/Opselon/NexusTradingForexBot/actions/runs/36622879481/job/109592443880",
                            "url": "https://api.github.com/repos/Opselon/NexusTradingForexBot/check-runs/109592443880",
                            "output": {"title": None, "summary": None, "text": None},
                            "started_at": "2026-09-29T19:57:00Z",
                            "completed_at": "2026-09-29T19:58:00Z",
                        }
                    ],
                },
                "annotations": [
                    {
                        "path": ".github",
                        "start_line": 218,
                        "annotation_level": "failure",
                        "message": "Process completed with exit code 1.",
                    }
                ],
                # check-run detail (get_job_id_for_check resolves the job id).
                "check-runs/109592443880": {
                    "id": 109592443880,
                    "html_url": "https://github.com/Opselon/NexusTradingForexBot/actions/runs/36622879481/job/109592443880",
                },
                # Job metadata + the binary log endpoint share the job-id path.
                "jobs/109592443880": {
                    "name": "github-advanced-security",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Set up job", "conclusion": "success"},
                        {"name": "Processing Request (Linux)", "conclusion": "failure"},
                    ],
                },
                "artifacts": {"artifacts": []},
            }
        )
        # Binary endpoint: scripted separately (get_bytes uses its own keying).
        transport.responses["jobs/109592443880/logs"] = COPILOT_LOG
        return GitHubClient("Opselon/NexusTradingForexBot", transport)

    def test_agent_check_gets_log_failures(self) -> None:
        client = self._client()
        ev = collect_evidence(
            client=client,
            repo="Opselon/NexusTradingForexBot",
            pr=594,
            options=EvidenceOptions(fetch_logs=True, fetch_artifacts=True),
        )
        assert ev.log_failures, "expected job-log failures for the agent check"
        f = ev.log_failures[0]
        assert f.error_type == "SessionModelError"
        assert "The requested model is not supported" in f.message
        assert f.infrastructure is True
        # And they reach the rendered failure list.
        rendered = [x for x in ev.all_failures() if x.source == "job-logs"]
        assert rendered

    def test_fetch_logs_off_disables(self) -> None:
        client = self._client()
        ev = collect_evidence(
            client=client,
            repo="Opselon/NexusTradingForexBot",
            pr=594,
            options=EvidenceOptions(fetch_logs=False, fetch_artifacts=False),
        )
        assert ev.log_failures == []

    def test_infrastructure_row_renders_with_notice(self) -> None:
        from nexus_scalp.pr_evidence.renderer import render_report

        client = self._client()
        ev = collect_evidence(
            client=client,
            repo="Opselon/NexusTradingForexBot",
            pr=594,
            options=EvidenceOptions(fetch_logs=True, fetch_artifacts=True),
        )
        body = render_report(ev)
        assert "Infrastructure failure" in body
        assert "The requested model is not supported" in body
        assert "SessionModelError" in body
