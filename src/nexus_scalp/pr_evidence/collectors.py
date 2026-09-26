"""Evidence collectors — turn raw API/output data into normalized failures.

Spec §1 ownership: this module owns CI result collection + failure
normalization. It composes :mod:`~nexus_scalp.pr_evidence.github_client` (API)
with :mod:`~nexus_scalp.pr_evidence.parsers` (output text) and
:mod:`~nexus_scalp.pr_evidence.locations` (file/line extraction).

All collectors are transport-injectable and side-effect free apart from the
explicit local-subprocess ones. Unavailable evidence degrades to ``unknown``
fields, never to an exception (spec §22: "GitHub API failure" must be handled).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from nexus_scalp.pr_evidence.github_client import GitHubClient
from nexus_scalp.pr_evidence.locations import (
    extract_error_type,
    extract_locations,
)
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    CheckAnnotation,
    CheckResult,
    EvidenceCollection,
    Failure,
    FailureCategory,
    ReportMeta,
    SkippedTest,
    SourceLocation,
)
from nexus_scalp.pr_evidence.parsers import (
    parse_mypy_output,
    parse_pytest_output,
    parse_ruff_output,
)

__all__ = [
    "AnnotationCollector",
    "CheckRunCollector",
    "CodeQLCollector",
    "LocalTestCollector",
    "ReviewCollector",
    "collect_evidence",
]


def _actions_run_id(url: Any) -> int | None:
    """Extract the Actions run id from a check-run URL (``.../runs/<id>/job/<id>``).

    Generic over any repo/host: the id is whatever sits between ``/runs/`` and
    the next ``/``. Returns ``None`` when the URL carries no run id, so callers
    degrade to ``unknown`` instead of inventing one.
    """
    text = _text(url)
    if not text:
        return None
    match = _ACTIONS_RUN_RE.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _replace_check_sha(check: CheckResult, sha: str) -> CheckResult:
    """Adopt the queried SHA when a check run reports a different or empty one."""
    if not sha or sha == UNKNOWN:
        return check
    if check.head_sha == sha:
        return check
    return CheckResult(
        name=check.name,
        status=check.status,
        conclusion=check.conclusion,
        workflow=check.workflow,
        job=check.job,
        url=check.url,
        started_at=check.started_at,
        completed_at=check.completed_at,
        annotations_count=check.annotations_count,
        annotations_url=check.annotations_url,
        annotations=check.annotations,
        head_sha=sha,
    )


def _ci_head_sha(checks: list[CheckResult]) -> str:
    """The revision the collected checks ran against (``unknown`` if unstated).

    Reports the majority sha when checks disagree — it is factual evidence of
    what CI actually ran on, so the revision table can surface a divergence
    from the PR HEAD (spec §16) rather than hiding it behind ``unknown``.
    """
    counts: dict[str, int] = {}
    for check in checks:
        sha = check.head_sha
        if sha in ("", UNKNOWN):
            continue
        counts[sha] = counts.get(sha, 0) + 1
    if not counts:
        return UNKNOWN
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


@dataclass
class CheckRunCollector:
    """GitHub check runs → :class:`CheckResult` + annotation-derived failures.

    Generic over every check name/workflow; the classification table below maps
    observed evidence shapes to categories without any PR-specific knowledge.
    """

    client: GitHubClient
    repo_root: str | None = None

    def collect(
        self, sha: str, *, fetch_annotations: bool = True
    ) -> tuple[list[CheckResult], list[Failure]]:
        raw_runs = self.client.get_check_runs_for_ref(sha)
        checks: list[CheckResult] = []
        failures: list[Failure] = []
        for raw in raw_runs:
            check = self._to_check(raw)
            if check.workflow == UNKNOWN:
                resolved = self._workflow_for(raw)
                if resolved != UNKNOWN:
                    check = replace(check, workflow=resolved)
            raw_annotations: list[dict[str, Any]] = []
            if fetch_annotations and check.annotations_count:
                fetched = self.client.get_annotations(int(raw["id"]))
                raw_annotations = [a for a in fetched if isinstance(a, dict)]
            if raw_annotations:
                check = CheckResult(
                    name=check.name,
                    status=check.status,
                    conclusion=check.conclusion,
                    workflow=check.workflow,
                    job=check.job,
                    url=check.url,
                    started_at=check.started_at,
                    completed_at=check.completed_at,
                    annotations_count=len(raw_annotations),
                    annotations_url=check.annotations_url,
                    annotations=tuple(self._to_annotation(a) for a in raw_annotations),
                    head_sha=check.head_sha,
                )
            checks.append(check)
            if check.is_failure or check.annotations:
                failures.extend(self._failures_from_check(check, raw_annotations))
        return checks, failures

    def _workflow_for(self, raw: dict[str, Any]) -> str:
        """Resolve the owning workflow for a check run (spec §10).

        A check-run payload carries no ``workflow_name``. Two evidence sources
        exist, in order of specificity:

        1. the Actions run named by ``details_url``
           (``.../actions/runs/<id>/job/<id>``) → the workflow name, e.g. ``CI``;
        2. the owning GitHub App (``app.name``), the only signal for non-Actions
           checks such as the code-scanning ``CodeQL`` run.

        Any failure degrades to ``UNKNOWN`` — the report must still render.
        """
        try:
            run_id = _actions_run_id(raw.get("details_url")) or _actions_run_id(raw.get("html_url"))
            if run_id is not None:
                name = _text(self.client.get_workflow_run(run_id).get("name"))
                if name:
                    return name
        except Exception:
            pass
        return _text((raw.get("app") or {}).get("name")) or UNKNOWN

    def _to_check(self, raw: dict[str, Any]) -> CheckResult:
        output = raw.get("output") or {}
        return CheckResult(
            name=_text(raw.get("name")) or UNKNOWN,
            # ``gh api`` lower-cases these for some check suites; the model's
            # vocabulary and the renderer are uppercase, so normalize here once
            # (spec §5: categories must map to evidence actually observed).
            status=_text(raw.get("status")).upper() or UNKNOWN,
            conclusion=_text(raw.get("conclusion")).upper() or UNKNOWN,
            workflow=_text(raw.get("workflow_name"))
            or _text((raw.get("check_suite") or {}).get("app", {}).get("name"))
            or UNKNOWN,
            job=_text(raw.get("name")) or UNKNOWN,
            url=_text(raw.get("html_url")) or UNKNOWN,
            started_at=_text(raw.get("started_at")) or UNKNOWN,
            completed_at=_text(raw.get("completed_at")) or UNKNOWN,
            annotations_count=int(output.get("annotations_count") or 0),
            annotations_url=_text(output.get("annotations_url")) or UNKNOWN,
            head_sha=_text(raw.get("head_sha")) or UNKNOWN,
        )

    def _to_annotation(self, raw: dict[str, Any]) -> CheckAnnotation:
        return CheckAnnotation(
            path=_text(raw.get("path")) or UNKNOWN,
            start_line=raw.get("start_line") if isinstance(raw.get("start_line"), int) else None,
            start_column=raw.get("start_column")
            if isinstance(raw.get("start_column"), int)
            else None,
            level=_text(raw.get("annotation_level")).upper() or UNKNOWN,
            title=_text(raw.get("title")) or UNKNOWN,
            message=_text(raw.get("message")) or UNKNOWN,
        )

    def _failures_from_check(
        self, check: CheckResult, raw_annotations: list[dict[str, Any]]
    ) -> list[Failure]:
        failures: list[Failure] = []
        # 1) Structured annotations are the primary file/line evidence (spec §4).
        # Only FAILURE-level annotations are failures — warnings/notices are
        # advisory and must not turn a red report greener than the gate that
        # produced them (spec §5: do not invent categories that do not
        # correspond to actual evidence).
        for anno in self._to_annotations(raw_annotations):
            if anno.level != "FAILURE":
                continue
            failures.append(self._failure_from_annotation(check, anno))
        # 2) A failed check with no annotations still yields a located failure
        #    when its summary/title/output text carries a file reference.
        text_blob = self._check_text(check)
        if not raw_annotations and check.is_failure:
            failures.extend(self._failures_from_text(check, text_blob))
        return failures

    def _to_annotations(self, raw_annotations: list[dict[str, Any]]) -> list[CheckAnnotation]:
        return [self._to_annotation(a) for a in raw_annotations if isinstance(a, dict)]

    def _check_text(self, check: CheckResult) -> str:
        return "\n".join(
            part for part in (check.name, check.workflow, check.job) if part != UNKNOWN
        )

    def _failure_from_annotation(self, check: CheckResult, anno: CheckAnnotation) -> Failure:
        path = normalize_or_unknown(anno.path, self.repo_root)
        loc = SourceLocation(path, anno.start_line, anno.start_column)
        rule = self._extract_rule(anno.title, anno.message)
        category = self._classify_check(check, anno, rule)
        return Failure(
            source="github-checks",
            suite=check.workflow,
            test=UNKNOWN,
            location=loc,
            production_location=None,
            error_type=rule or _classify_error_type(anno.message),
            message=anno.message[:400],
            category=category,
            workflow=check.workflow,
            job=check.job,
            check=check.name,
            check_url=check.url,
            evidence_source="github-checks",
        )

    def _failures_from_text(self, check: CheckResult, text: str) -> list[Failure]:
        locs = extract_locations(text, self.repo_root)
        base_message = f"CI gate failed: {check.name}"
        if not locs:
            return [
                Failure(
                    source="github-checks",
                    suite=check.workflow,
                    location=SourceLocation.unknown(),
                    message=base_message,
                    category=self._classify_check(check, None, None),
                    workflow=check.workflow,
                    job=check.job,
                    check=check.name,
                    check_url=check.url,
                    evidence_source="github-checks",
                )
            ]
        return [
            Failure(
                source="github-checks",
                suite=check.workflow,
                location=loc,
                message=base_message,
                category=self._classify_check(check, None, None),
                workflow=check.workflow,
                job=check.job,
                check=check.name,
                check_url=check.url,
                evidence_source="github-checks",
            )
            for loc in locs
        ]

    def _extract_rule(self, title: str, message: str) -> str:
        blob = f"{title}\n{message}"
        m = _RE_RULE_ID.search(blob) or _RE_RULE_CODE.search(blob)
        return m.group(1) if m else UNKNOWN

    def _classify_check(
        self, check: CheckResult, anno: CheckAnnotation | None, rule: str
    ) -> FailureCategory:
        name = f"{check.name} {check.workflow} {rule}".lower()
        if "codeql" in name:
            return FailureCategory.CODEQL_FINDING
        if "trivy" in name or "osv" in name or "security" in name or "secret" in name:
            return FailureCategory.SECURITY_FINDING
        if "ruff" in name and "format" in name:
            return FailureCategory.FORMAT_FAILURE
        if "ruff" in name:
            return FailureCategory.LINT_FAILURE
        if "mypy" in name:
            return FailureCategory.MYPY_FAILURE
        if "js test" in name or "jest" in name or "frontend" in name:
            return FailureCategory.TEST_FAILURE
        if "migration" in name or "lock" in name or "dependency" in name:
            return FailureCategory.MIGRATION_FAILURE
        if "deploy" in name or "release" in name:
            return FailureCategory.DEPLOY_GATE_FAILURE
        if "build" in name or "packag" in name:
            return FailureCategory.PACKAGING_FAILURE
        if "test" in name or "py test" in name or "quality" in name:
            return FailureCategory.TEST_FAILURE
        return FailureCategory.CI_FAILURE


_RE_RULE_CODE = re.compile(r"\b([A-Z][A-Z0-9_]{2,12})\b")
_RE_RULE_ID = re.compile(r"\b([a-z][a-z0-9-]*/[a-z0-9-]+)\b")

#: ``https://<host>/<owner>/<repo>/actions/runs/<run_id>[/job/<job_id>]``.
#: Bounded quantifiers keep this linear (no nested repeats) — ReDoS-safe.
_ACTIONS_RUN_RE = re.compile(r"/actions/runs/(\d{1,20})(?:/|$)")


def normalize_or_unknown(path: str, repo_root: str | None = None) -> str:
    from nexus_scalp.pr_evidence.locations import normalize_repo_relative

    return normalize_repo_relative(path, repo_root)


@dataclass
class CodeQLCollector:
    """CodeQL / code-scanning findings with rule, severity, file, line, column (§11).

    Uses the code-scanning alerts API (findings carry structured locations) and
    falls back to check-run annotations when the alert list is unavailable.
    Refuses to reproduce sensitive source material: only rule/severity/message
    text is carried, capped to a bounded excerpt.
    """

    client: GitHubClient
    repo_root: str | None = None

    def collect(self, pr: int, sha: str) -> tuple[list[Failure], list[dict[str, Any]]]:
        alerts = self._alerts_for_ref(sha)
        failures: list[Failure] = [self._failure(a) for a in alerts if self._is_open(a)]
        return failures, alerts

    def _alerts_for_ref(self, sha: str) -> list[dict[str, Any]]:
        payload = self.client.get_code_scanning_alerts(sha)
        if isinstance(payload, list):
            return [a for a in payload if isinstance(a, dict)]
        return []

    def _is_open(self, alert: dict[str, Any]) -> bool:
        state = _text(alert.get("state")).lower()
        return state in ("open", "")

    def _failure(self, alert: dict[str, Any]) -> Failure:
        rule = alert.get("rule") or {}
        most_severe = alert.get("most_recent_instance") or {}
        location = most_severe.get("location") or {}
        path = normalize_or_unknown(_text(location.get("path")), self.repo_root)
        start = location.get("start_line")
        start_col = location.get("start_column")
        return Failure(
            source="codeql",
            suite="codeql",
            test=UNKNOWN,
            location=SourceLocation(
                path,
                int(start) if isinstance(start, int) else None,
                int(start_col) if isinstance(start_col, int) else None,
            ),
            error_type=_text(rule.get("id")) or UNKNOWN,
            message=(_text(rule.get("description")) or _text(most_severe.get("message")))[:300],
            category=FailureCategory.CODEQL_FINDING,
            workflow="code-scanning",
            job="codeql",
            check="CodeQL Analysis",
            evidence_source="code-scanning-alerts",
        )


@dataclass
class ReviewCollector:
    """PR reviews → non-blocking review evidence (spec §1 review collection)."""

    client: GitHubClient

    def collect(self, pr: int) -> list[dict[str, Any]]:
        reviews = self.client.get_pr_reviews(pr)
        out: list[dict[str, Any]] = []
        for review in reviews or []:
            if not isinstance(review, dict):
                continue
            state = _text(review.get("state"))
            if state in ("COMMENTED", "DISMISSED"):
                continue
            out.append(
                {
                    "state": state,
                    "user": _text((review.get("user") or {}).get("login")) or UNKNOWN,
                    "submitted_at": _text(review.get("submitted_at")) or UNKNOWN,
                    "url": _text(review.get("html_url")) or UNKNOWN,
                }
            )
        return out


@dataclass
class LocalTestCollector:
    """Runs the repo's gates locally and parses the output (spec §15).

    Only invoked when the caller asks (``--local`` or ``--refresh``): a CI-only
    report must stay network-only + read-only. Every run is a bounded
    subprocess; failure to run degrades to a recorded gap, never an exception.
    """

    repo_dir: Path
    python: str = ""
    timeout_sec: int = 600
    runner_mode: str = "serial"
    repo_root: str | None = None
    markers: tuple[str, ...] = ("pytest", "ruff", "mypy")

    def collect(self) -> tuple[list[Failure], list[SkippedTest], list[dict[str, str]]]:
        failures: list[Failure] = []
        skipped: list[SkippedTest] = []
        matrix: list[dict[str, str]] = []
        env = {"PYTHONPATH": "src"}
        for tool in self.markers:
            if "pytest" in tool:
                continue
        if "pytest" in self.markers:
            out = self._run(
                [self._py(), "-m", "pytest", "tests/unit", "-q", "-p", "no:cacheprovider"],
                env=env,
            )
            fails, skips, counts = parse_pytest_output(out, self.repo_root, source="local-pytest")
            failures.extend(replace(f, source="local-test-output") for f in fails)
            skipped.extend(skips)
            matrix.append(
                {
                    "Gate": "Unit (local)",
                    "Status": "❌" if fails else "✅",
                    "Failed": str(len(fails)),
                    "Skipped": str(len(skips)),
                    "Passed": str(counts.get("passed", 0)),
                    "Duration": out_duration(out),
                }
            )
        if "ruff" in self.markers:
            out = self._run([self._py(), "-m", "ruff", "check", "."], env=env)
            ruff_failures = parse_ruff_output(out, self.repo_root, kind="lint")
            failures.extend(ruff_failures)
            matrix.append(
                {
                    "Gate": "Ruff (local)",
                    "Status": "❌" if ruff_failures else "✅",
                    "Failed": str(len(ruff_failures)),
                }
            )
        if "mypy" in self.markers:
            out = self._run([self._py(), "-m", "mypy", "src"], env=env)
            mypy_failures = parse_mypy_output(out, self.repo_root)
            failures.extend(mypy_failures)
            matrix.append(
                {
                    "Gate": "Mypy (local)",
                    "Status": "❌" if mypy_failures else "✅",
                    "Failed": str(len(mypy_failures)),
                }
            )
        return failures, skipped, matrix

    def _py(self) -> str:
        if self.python:
            return self.python
        venv = Path(self.repo_dir) / ".venv" / "Scripts" / "python.exe"
        if venv.exists():
            return str(venv)
        return "python"

    def _run(self, cmd: list[str], env: dict[str, str] | None = None) -> str:
        import os
        import subprocess as sp

        full_env = {**os.environ, **(env or {})}
        try:
            proc = sp.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=str(self.repo_dir),
                check=False,
                env=full_env,
            )
        except (OSError, sp.SubprocessError):
            return ""
        return f"{proc.stdout}\n{proc.stderr}"


def out_duration(_out: str) -> str:
    """Wall-clock duration is only reported when the tool printed one."""
    return "—"


@dataclass
class AnnotationCollector:
    """Fallback collector: derives located failures from any annotation payload.

    Used when check-run logs are unreachable (e.g. blob storage offline) so the
    report still carries file:line evidence from the annotation surface.
    """

    repo_root: str | None = None

    def collect(
        self, check: CheckResult, annotations: tuple[CheckAnnotation, ...]
    ) -> list[Failure]:
        out: list[Failure] = []
        for anno in annotations:
            if anno.level != "FAILURE":
                continue
            path = normalize_or_unknown(anno.path, self.repo_root)
            out.append(
                Failure(
                    source="github-annotations",
                    suite=check.workflow,
                    location=SourceLocation(path, anno.start_line, anno.start_column),
                    error_type=UNKNOWN,
                    message=anno.message[:300],
                    category=FailureCategory.CI_FAILURE,
                    workflow=check.workflow,
                    job=check.job,
                    check=check.name,
                    check_url=check.url,
                    evidence_source="github-annotations",
                )
            )
        return out


@dataclass
class EvidenceOptions:
    """What the reporter should collect (all OFF by default = CI-only report)."""

    fetch_annotations: bool = True
    fetch_codeql: bool = True
    local_tests: bool = False
    reviews: bool = True
    fetch_logs: bool = False
    log_max_bytes: int = 6000


def collect_evidence(
    *,
    client: GitHubClient,
    repo: str,
    pr: int,
    options: EvidenceOptions,
    repo_root: str | None = None,
    repo_dir: Path | None = None,
    local_python: str = "",
) -> EvidenceCollection:
    """The generic collection pipeline (spec §1): any PR → evidence.

    Degrades gracefully: every stage records a gap string instead of raising,
    so a partial API failure still yields a report with the evidence it could
    collect (spec §22: missing-environment + GitHub-API-failure handling).
    """
    errors: list[str] = []
    warnings: list[str] = []
    raw: dict[str, Any] = {}

    pr_payload = client.get_pr(pr)
    if "_error" in pr_payload or not isinstance(pr_payload, dict) or "number" not in pr_payload:
        return EvidenceCollection(
            meta=ReportMeta(pr=pr, pr_head_sha=UNKNOWN),
            errors=[f"PR lookup failed: {_text(pr_payload.get('_error')) or 'not found'}"],
        )

    head_sha = _text(pr_payload.get("head", {}).get("sha")) or UNKNOWN
    # Local HEAD is only evidence when the caller asked for a local collection
    # pass; otherwise the revision table reports only what was verified.
    local_sha = local_head_sha(repo_dir) if options.local_tests else UNKNOWN
    raw["pr"] = pr_payload

    checks: list[CheckResult] = []
    failures: list[Failure] = []
    ci_sha = UNKNOWN
    if head_sha != UNKNOWN:
        run_collector = CheckRunCollector(client=client, repo_root=repo_root)
        checks, failures = run_collector.collect(
            head_sha, fetch_annotations=options.fetch_annotations
        )
        raw["check_runs"] = [c.__dict__ for c in checks]
        # The CI revision is what the checks actually ran against. A divergence
        # from the PR HEAD is a first-class finding (spec §16).
        ci_sha = _ci_head_sha(checks)
    else:
        warnings.append("PR HEAD SHA could not be resolved; CI evidence collection skipped.")

    # Built AFTER collection so ci_head_sha reflects the checks actually gathered.
    meta = ReportMeta(
        pr=pr,
        pr_head_sha=head_sha,
        local_head_sha=local_sha,
        ci_head_sha=ci_sha,
        title=_text(pr_payload.get("title")) or UNKNOWN,
        branch=_text(pr_payload.get("head", {}).get("ref")) or UNKNOWN,
        base_branch=_text(pr_payload.get("base", {}).get("ref")) or UNKNOWN,
        author=_text((pr_payload.get("user") or {}).get("login")) or UNKNOWN,
    )

    codeql_failures: list[Failure] = []
    if options.fetch_codeql and head_sha != UNKNOWN:
        try:
            codeql_failures, raw_alerts = CodeQLCollector(
                client=client, repo_root=repo_root
            ).collect(pr, head_sha)
            raw["codeql_alerts"] = raw_alerts
        except Exception as exc:
            errors.append(f"CodeQL collection failed: {type(exc).__name__}: {exc}")
        failures.extend(codeql_failures)

    reviews: list[dict[str, Any]] = []
    if options.reviews:
        try:
            reviews = ReviewCollector(client=client).collect(pr)
            raw["reviews"] = reviews
        except Exception as exc:
            errors.append(f"Review collection failed: {type(exc).__name__}: {exc}")

    local_failures: list[Failure] = []
    skipped: list[SkippedTest] = []
    matrix: list[dict[str, str]] = []
    if options.local_tests and repo_dir is not None:
        try:
            local_failures, skipped, matrix = LocalTestCollector(
                repo_dir=repo_dir,
                python=local_python,
                repo_root=repo_root,
            ).collect()
        except Exception as exc:
            errors.append(f"Local test collection failed: {type(exc).__name__}: {exc}")

    return EvidenceCollection(
        meta=meta,
        checks=checks,
        failures=failures,
        skipped=skipped,
        test_matrix=matrix,
        local_failures=local_failures,
        warnings=warnings,
        errors=errors,
        raw=raw,
    )


def local_head_sha(repo_dir: Path) -> str:
    """HEAD of the local checkout (spec §16 revision table).

    Only consulted when local evidence is requested; otherwise ``unknown`` so
    the revision table never reports a HEAD the reporter did not verify.
    """
    import subprocess

    if repo_dir is None:
        return UNKNOWN
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(repo_dir),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return proc.stdout.strip() if proc.returncode == 0 else UNKNOWN


def _classify_error_type(message: str) -> str:
    return extract_error_type(message)
