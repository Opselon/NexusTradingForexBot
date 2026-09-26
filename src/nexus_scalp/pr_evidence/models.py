"""Structured failure + evidence model for the PR Evidence Reporter.

Every failure is normalized into a :class:`Failure` carrying as much of the
following as the evidence supports, and ``"unknown"`` (never a guess) for the
rest: PR, HEAD SHA, commit, CI workflow, CI job, check name, test suite, test
name, failure type, repository-relative file, line, column, function/class,
error type, error message, traceback excerpt, and the source of the evidence.

BOUNDARY: pure data + deterministic classification. No network, no rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "UNKNOWN",
    "CheckResult",
    "EvidenceCollection",
    "Failure",
    "FailureCategory",
    "ReportMeta",
    "SkippedTest",
    "Status",
]


def _u(value: Any) -> str:
    """Normalize a nullable/empty scalar to the canonical ``"unknown"`` string."""
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    return text if text else UNKNOWN


#: Sentinel for any field the evidence could not determine. Never guess.
UNKNOWN = "unknown"


class Status(StrEnum):
    """Deterministic overall states (spec §17). Subjective scores are forbidden."""

    PASS = "PASS"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

    @property
    def dot(self) -> str:
        return {
            Status.PASS: "🟢",
            Status.IN_PROGRESS: "🟡",
            Status.BLOCKED: "🟠",
            Status.FAIL: "🔴",
            Status.UNKNOWN: "⚪",
        }[self]


class FailureCategory(StrEnum):
    """Factual failure classes. Categories map to evidence actually observed."""

    TEST_FAILURE = "TEST_FAILURE"
    TYPE_ERROR = "TYPE_ERROR"
    LINT_FAILURE = "LINT_FAILURE"
    FORMAT_FAILURE = "FORMAT_FAILURE"
    MYPY_FAILURE = "MYPY_FAILURE"
    IMPORT_FAILURE = "IMPORT_FAILURE"
    BUILD_FAILURE = "BUILD_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    MIGRATION_FAILURE = "MIGRATION_FAILURE"
    CONFIGURATION_FAILURE = "CONFIGURATION_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    CI_FAILURE = "CI_FAILURE"
    CODEQL_FINDING = "CODEQL_FINDING"
    SECURITY_FINDING = "SECURITY_FINDING"
    PACKAGING_FAILURE = "PACKAGING_FAILURE"
    DEPLOY_GATE_FAILURE = "DEPLOY_GATE_FAILURE"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SourceLocation:
    """A repository-relative file position with optional line and column.

    ``path`` is always repo-relative (absolute/local paths are normalized away
    at parse time) or ``UNKNOWN``. ``line``/``column`` are ``None`` when the
    evidence does not support them — never 0, never guessed.
    """

    path: str
    line: int | None = None
    column: int | None = None
    function: str = UNKNOWN

    @staticmethod
    def unknown() -> SourceLocation:
        return SourceLocation(path=UNKNOWN)

    def rendered(self) -> str:
        """``path`` or ``path:line`` or ``path:line:column`` (spec §6).

        A line is only rendered alongside a KNOWN path: ``unknown:218`` would
        fabricate a file location where the evidence gave none (spec §3: if the
        parser cannot determine a field, use ``unknown``; do not guess).
        """
        if self.path == UNKNOWN:
            return UNKNOWN
        out = self.path
        if self.line is not None:
            out += f":{self.line}"
            if self.column is not None:
                out += f":{self.column}"
        return out

    def describe(self) -> str:
        """``path:line — func()`` for impact tables."""
        out = self.rendered()
        if self.function != UNKNOWN:
            out += f" — {self.function}()"
        return out


@dataclass(frozen=True)
class Failure:
    """One normalized failure (spec §3).

    ``location`` is where the failure was DETECTED (usually a test file).
    ``production_location`` is the suspected production code location, only
    set when the traceback evidence supports it — never guessed. This is what
    keeps the reporter from claiming a test file *caused* a failure (spec §6).
    """

    source: str = UNKNOWN  # e.g. "pytest", "ruff", "github-checks"
    suite: str = UNKNOWN
    test: str = UNKNOWN
    location: SourceLocation = field(default_factory=SourceLocation.unknown)
    production_location: SourceLocation | None = None
    error_type: str = UNKNOWN
    message: str = UNKNOWN
    traceback: str = UNKNOWN
    category: FailureCategory = FailureCategory.UNKNOWN
    workflow: str = UNKNOWN
    job: str = UNKNOWN
    check: str = UNKNOWN
    check_url: str = UNKNOWN
    commit: str = UNKNOWN
    pr: int | None = None
    head_sha: str = UNKNOWN
    evidence_source: str = UNKNOWN  # "github-checks" | "local-test-output" | ...

    def affected_paths(self) -> list[str]:
        """Files this failure implicates: detected location + production location."""
        paths: list[str] = []
        for loc in (self.location, self.production_location):
            if loc is not None and loc.path != UNKNOWN and loc.path not in paths:
                paths.append(loc.path)
        return paths

    def with_unknowns(self) -> Failure:
        """A copy with every unfilled field set to the ``unknown`` sentinel."""
        return replace(
            self,
            suite=_u(self.suite),
            test=_u(self.test),
            location=self.location if self.location.path != UNKNOWN else SourceLocation.unknown(),
            production_location=self.production_location,
            error_type=_u(self.error_type),
            message=_u(self.message),
            traceback=_u(self.traceback),
            workflow=_u(self.workflow),
            job=_u(self.job),
            check=_u(self.check),
            check_url=_u(self.check_url),
            commit=_u(self.commit),
            head_sha=_u(self.head_sha),
            evidence_source=_u(self.evidence_source),
            source=_u(self.source),
        )


@dataclass(frozen=True)
class SkippedTest:
    """A test that did not run to completion. A skipped test is NOT pass (§14)."""

    test: str
    location: SourceLocation
    reason: str = UNKNOWN
    category: FailureCategory = FailureCategory.ENVIRONMENT_FAILURE
    suite: str = UNKNOWN


@dataclass(frozen=True)
class CheckResult:
    """One GitHub check run as evidence (workflow/job/step shape, spec §10)."""

    name: str
    status: str  # COMPLETED | IN_PROGRESS | QUEUED ...
    conclusion: str  # SUCCESS | FAILURE | SKIPPED | CANCELLED | TIMED_OUT | ...
    workflow: str
    job: str
    url: str
    started_at: str = UNKNOWN
    completed_at: str = UNKNOWN
    annotations_count: int = 0
    annotations_url: str = UNKNOWN
    annotations: tuple[CheckAnnotation, ...] = ()
    head_sha: str = UNKNOWN

    @property
    def is_failure(self) -> bool:
        return self.status == "COMPLETED" and self.conclusion in {
            "FAILURE",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "NEUTRAL",
        }

    @property
    def in_progress(self) -> bool:
        return self.status in {"IN_PROGRESS", "QUEUED", "PENDING"}


@dataclass(frozen=True)
class CheckAnnotation:
    """A GitHub check-run annotation (spec §4/§11)."""

    path: str
    start_line: int | None
    start_column: int | None
    level: str  # failure | warning | notice
    title: str
    message: str

    def to_location(self) -> SourceLocation:
        return SourceLocation(
            path=self.path,
            line=self.start_line,
            column=self.start_column,
            function=UNKNOWN,
        )


@dataclass(frozen=True)
class ReportMeta:
    """Revision identity of the report (spec §16)."""

    pr: int
    pr_head_sha: str
    local_head_sha: str = UNKNOWN
    ci_head_sha: str = UNKNOWN
    title: str = UNKNOWN
    branch: str = UNKNOWN
    base_branch: str = UNKNOWN
    author: str = UNKNOWN

    @property
    def heads_match(self) -> bool:
        """True only when every known head resolves to the PR HEAD."""
        known = {self.pr_head_sha, self.local_head_sha, self.ci_head_sha} - {
            UNKNOWN,
            "",
        }
        return len(known) <= 1


@dataclass
class EvidenceCollection:
    """All gathered evidence for one PR, pre-rendering (spec §1 architecture)."""

    meta: ReportMeta
    checks: list[CheckResult] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    skipped: list[SkippedTest] = field(default_factory=list)
    test_matrix: list[dict[str, str]] = field(default_factory=list)
    local_failures: list[Failure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def all_failures(self) -> list[Failure]:
        """CI-collected + locally-collected failures."""
        return [*self.failures, *self.local_failures]

    @property
    def status(self) -> Status:
        """Deterministic overall status (spec §17) — evidence, not opinion.

        IN_PROGRESS wins over FAIL (a half-finished run can still clear) and
        FAIL wins over BLOCKED; no check state at all is UNKNOWN, not PASS.
        """
        if not self.checks and not self.all_failures():
            return Status.UNKNOWN
        if any(c.in_progress for c in self.checks):
            return Status.IN_PROGRESS
        if any(c.is_failure for c in self.checks) or any(
            f.category != FailureCategory.ENVIRONMENT_FAILURE for f in self.all_failures()
        ):
            return Status.FAIL
        if self.all_failures():
            return Status.BLOCKED
        return Status.PASS
