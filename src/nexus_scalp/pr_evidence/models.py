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
    "MergeReason",
    "MergeVerdict",
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
    #: Severity as the tooling stated it (e.g. "high", "critical"). Never guessed.
    severity: str = UNKNOWN
    #: GitHub's own explanation for the finding (rule full_description) — the
    #: scanner's words, quoted, never our paraphrase of the fix.
    remediation: str = UNKNOWN
    #: Deep link to the finding so a reader can open it and act.
    rule_url: str = UNKNOWN

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
            severity=_u(self.severity),
            remediation=_u(self.remediation),
            rule_url=_u(self.rule_url),
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
class MergeReason:
    """One piece of merge-verdict evidence, with the fact that produced it.

    ``blocking`` distinguishes evidence that PREVENTS a merge from evidence
    that merely explains the verdict (e.g. "no review required"). Every reason
    carries the concrete observation, never a bare assertion.
    """

    detail: str
    blocking: bool = False
    source: str = "GitHub"


@dataclass(frozen=True)
class MergeVerdict:
    """Can this PR be merged right now? (Deterministic, evidence-derived.)

    ``state`` is one of:
      MERGED      — already merged; mergeability is history, not a question.
      YES         — every merge precondition observed is satisfied.
      NO          — at least one observed precondition is unsatisfied.
      UNKNOWN     — mergeability could not be determined from the evidence.
    """

    state: str
    reasons: tuple[MergeReason, ...] = ()

    @property
    def blockers(self) -> tuple[MergeReason, ...]:
        return tuple(r for r in self.reasons if r.blocking)

    @property
    def dot(self) -> str:
        return {
            "MERGED": "✅",
            "YES": "🟢",
            "NO": "🔴",
            "UNKNOWN": "⚪",
        }.get(self.state, "⚪")

    @property
    def headline(self) -> str:
        """The literal answer to "Merge?": Yes / No / Already merged / Unknown."""
        return {
            "MERGED": "***Yes — already merged***",
            "YES": "***Yes***",
            "NO": "***No***",
            "UNKNOWN": "***Unknown***",
        }.get(self.state, "***Unknown***")


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
    # Merge-verdict inputs (all optional: absent = not observed, not "false").
    pr_state: str = UNKNOWN
    merged: bool | None = None
    merged_at: str = UNKNOWN
    mergeable: bool | None = None
    mergeable_state: str = UNKNOWN
    draft: bool | None = None
    base_sha: str = UNKNOWN

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
    #: Reviews observed on the PR (``state``/``user``/``submitted_at``).
    reviews: list[dict[str, Any]] = field(default_factory=list)
    #: Branch-protection required check names for the base branch.
    required_checks: list[str] = field(default_factory=list)
    #: Required approving review count (``None`` = protection unreadable).
    required_reviews: int | None = None
    #: Base requires branches up to date (``None`` = protection unreadable).
    require_up_to_date: bool | None = None

    def all_failures(self) -> list[Failure]:
        """CI-collected + locally-collected failures."""
        return [*self.failures, *self.local_failures]

    @property
    def settled(self) -> bool:
        """True when no collected check is still running (spec §17).

        A report published while checks are in flight is a snapshot, not a
        verdict: publishing one and stopping leaves ``IN_PROGRESS`` frozen on
        the PR even after every gate finishes. Callers that must land a FINAL
        state wait for ``settled``, which is evidence-based (every check has a
        conclusion) and therefore works for any PR shape.
        """
        return not any(c.in_progress for c in self.checks)

    # ------------------------------------------------------------------
    def merge_verdict(self) -> MergeVerdict:
        """Answer "Merge?" from observed evidence only (never an opinion).

        Every reason cites the specific observation behind it. A precondition
        that could NOT be observed (protection unreadable, mergeability not yet
        computed) is reported as a gap, not silently treated as satisfied — a
        verdict of YES must mean every OBSERVED gate passed and no unknown
        precondition was needed for it.
        """
        meta = self.meta
        reasons: list[MergeReason] = []
        unknown_gaps: list[str] = []

        # 1. Already merged is terminal: mergeability is history, not a question.
        if meta.merged is True:
            when = meta.merged_at if meta.merged_at != UNKNOWN else UNKNOWN
            reasons.append(
                MergeReason(f"Already merged on {when}." if when != UNKNOWN else "Already merged.")
            )
            return MergeVerdict("MERGED", tuple(reasons))

        # 2. PR-level state.
        if meta.pr_state.upper() == "CLOSED" and meta.merged is False:
            reasons.append(MergeReason("PR is CLOSED and was not merged.", blocking=True))
        if meta.draft is True:
            reasons.append(MergeReason("PR is a DRAFT.", blocking=True))
        if meta.draft is None:
            unknown_gaps.append("draft state")

        # 3. Conflicting files / mergeability.
        state = (meta.mergeable_state or UNKNOWN).lower()
        if meta.mergeable is False:
            reasons.append(
                MergeReason(
                    f"GitHub reports the branch is not mergeable (state: {state}).",
                    blocking=True,
                )
            )
        elif state == "dirty":
            reasons.append(MergeReason("Merge conflict with the base branch.", blocking=True))
        elif state == "behind":
            # "Behind" is a blocker ONLY if the base requires up-to-date
            # branches — otherwise GitHub will happily merge it. Never guess.
            if self.require_up_to_date is True:
                reasons.append(
                    MergeReason(
                        "Branch is behind the base, which requires branches to be up to date.",
                        blocking=True,
                    )
                )
            elif self.require_up_to_date is False:
                reasons.append(
                    MergeReason(
                        "Branch is behind the base (base does not require up-to-date "
                        "branches, so this does not block)."
                    )
                )
            else:
                reasons.append(MergeReason("Branch is behind the base.", blocking=False))
                unknown_gaps.append("up-to-date-branch requirement")
        elif state == "blocked":
            reasons.append(MergeReason("GitHub marks the merge blocked by a rule.", blocking=True))
        elif meta.mergeable is None:
            unknown_gaps.append("mergeability")

        # 4. Base-branch protection decides which checks actually GATE a merge.
        #    A failing check the base does not require does NOT block the merge:
        #    GitHub reports that as `mergeable` with state `unstable`. Blocking
        #    on it would contradict the platform's own answer.
        # ``required_reviews is None`` is the read-failure sentinel: 0 is a real
        # answer ("no reviews required"), None means protection never read.
        protection_known = self.required_reviews is not None
        gating: set[str] = set()
        if protection_known:
            gating = set(self.required_checks)
            by_name = {c.name: c for c in self.checks}
            missing: list[str] = []
            unsatisfied: list[str] = []
            for required in self.required_checks:
                check = by_name.get(required)
                if check is None:
                    missing.append(required)
                elif check.is_failure:
                    unsatisfied.append(required)
                elif check.in_progress:
                    unsatisfied.append(required)
            if unsatisfied:
                reasons.append(
                    MergeReason(
                        "Required check(s) not green: "
                        + ", ".join(f"`{n}`" for n in sorted(set(unsatisfied))[:10])
                        + ".",
                        blocking=True,
                    )
                )
            if missing:
                reasons.append(
                    MergeReason(
                        "Required check(s) never reported on this HEAD: "
                        + ", ".join(f"`{n}`" for n in sorted(set(missing))[:10])
                        + ".",
                        blocking=True,
                    )
                )
        else:
            # Protection is unreadable from this token (GitHub grants no
            # `administration` scope to a workflow token). GitHub already
            # computed mergeability WITH the protection rules applied, so its
            # answer is authoritative — defer to it and disclose the gap
            # rather than guessing a gate list.
            gating = {c.name for c in self.checks}
            unknown_gaps.append("which checks the base branch requires")

        # 5. Non-green checks that do NOT gate the merge are advisory: disclosed,
        #    never fatal. (GitHub reports this shape as `unstable`.)
        non_green = [c for c in self.checks if c.is_failure or c.in_progress]
        advisory = [c for c in non_green if c.name not in gating]
        if advisory:
            parts = []
            for check in advisory[:8]:
                what = "failing" if check.is_failure else "still running"
                parts.append(f"`{check.name}` ({what})")
            reasons.append(
                MergeReason(
                    f"{len(advisory)} non-required check(s) not green: "
                    + ", ".join(parts)
                    + " — the base branch does not gate on these."
                )
            )
        elif not protection_known and non_green:
            reasons.append(
                MergeReason(
                    f"{len(non_green)} check(s) not green: "
                    + ", ".join(f"`{c.name}`" for c in non_green[:8])
                    + " — the base branch's required list could not be read, so "
                    "GitHub's own mergeability answer is used instead."
                )
            )

        # 6. Required reviews.
        if self.required_reviews:
            approvals = {
                str(r.get("user") or UNKNOWN)
                for r in self.reviews
                if str(r.get("state") or "").upper() == "APPROVED"
            }
            if len(approvals) < self.required_reviews:
                reasons.append(
                    MergeReason(
                        f"{len(approvals)} approving review(s); {self.required_reviews} required.",
                        blocking=True,
                    )
                )
            else:
                reasons.append(
                    MergeReason(
                        f"{len(approvals)} approving review(s) meet the "
                        f"{self.required_reviews} required."
                    )
                )
        elif self.required_reviews is None:
            unknown_gaps.append("required-review count")
        else:
            reasons.append(MergeReason("No approving reviews required by the base branch."))

        if any(r.blocking for r in reasons):
            return MergeVerdict("NO", tuple(reasons))

        # Nothing blocking was observed. Decide using the strongest signal left:
        # GitHub's own `mergeable`, which is computed WITH the base's protection
        # rules — including the ones this token cannot read. It outranks our
        # gaps, so a known `mergeable` resolves them instead of yielding UNKNOWN.
        if unknown_gaps:
            reasons.append(
                MergeReason(
                    "Not observed: " + ", ".join(unknown_gaps) + " — could not be read.",
                    blocking=False,
                )
            )
        if meta.mergeable is True:
            reasons.append(
                MergeReason("GitHub reports this PR is mergeable.")
                if not unknown_gaps
                else MergeReason(
                    "GitHub reports this PR is mergeable, which already accounts "
                    "for the unreadable rules above."
                )
            )
            return MergeVerdict("YES", tuple(reasons))
        if meta.mergeable is False:
            reasons.append(MergeReason("GitHub reports this PR is not mergeable.", blocking=True))
            return MergeVerdict("NO", tuple(reasons))
        # mergeable is None: GitHub has not finished computing it yet.
        if meta.mergeable is None:
            reasons.append(
                MergeReason(
                    "GitHub has not finished computing mergeability for this HEAD "
                    "(it returns null until it does)."
                )
            )
        return MergeVerdict("UNKNOWN", tuple(reasons))

    @property
    def status(self) -> Status:
        """Deterministic overall status (spec §17) — evidence, not opinion.

        IN_PROGRESS wins over FAIL (a half-finished run can still clear) and
        FAIL wins over BLOCKED; no check state at all is UNKNOWN, not PASS.
        """
        if not self.checks and not self.all_failures():
            return Status.UNKNOWN
        if not self.settled:
            return Status.IN_PROGRESS
        if any(c.is_failure for c in self.checks) or any(
            f.category != FailureCategory.ENVIRONMENT_FAILURE for f in self.all_failures()
        ):
            return Status.FAIL
        if self.all_failures():
            return Status.BLOCKED
        return Status.PASS
