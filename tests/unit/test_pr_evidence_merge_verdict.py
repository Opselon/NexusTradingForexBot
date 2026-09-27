"""Merge-verdict stage: "Merge? Yes / No / Already merged" from evidence.

The stage must be deterministic and evidence-bound: a YES means every OBSERVED
precondition passed and nothing unobserved was needed; a NO lists the concrete
blockers; anything unreadable yields UNKNOWN rather than a confident guess.
No PR number, check name, branch, or repo specific appears in this file — the
stage works for ANY PR (spec §1: no special-casing).
"""

from __future__ import annotations

from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    CheckResult,
    EvidenceCollection,
    ReportMeta,
)
from nexus_scalp.pr_evidence.renderer import render_report

_SHA = "a" * 40
_BASE_SHA = "b" * 40


def _check(
    name: str = "Some Gate",
    status: str = "COMPLETED",
    conclusion: str = "SUCCESS",
) -> CheckResult:
    return CheckResult(
        name=name,
        status=status,
        conclusion=conclusion,
        workflow="Some Workflow",
        job="some-job",
        url="https://gh/1",
        head_sha=_SHA,
    )


def _meta(**over: object) -> ReportMeta:
    """A fully-observed, mergeable PR; override one field per test."""
    base: dict[str, object] = {
        "pr": 999,
        "pr_head_sha": _SHA,
        "local_head_sha": _SHA,
        "ci_head_sha": _SHA,
        "title": "Some feature",
        "branch": "agent/feature/x",
        "base_branch": "main",
        "author": "someone",
        "pr_state": "OPEN",
        "merged": False,
        "merged_at": UNKNOWN,
        "mergeable": True,
        "mergeable_state": "clean",
        "draft": False,
        "base_sha": _BASE_SHA,
    }
    base.update(over)
    return ReportMeta(**base)  # type: ignore[arg-type]


def _evidence(
    checks: list[CheckResult] | None = None,
    *,
    meta: ReportMeta | None = None,
    required_checks: list[str] | None = None,
    required_reviews: int | None = 0,
    require_up_to_date: bool | None = False,
    reviews: list[dict[str, object]] | None = None,
) -> EvidenceCollection:
    return EvidenceCollection(
        meta=meta or _meta(),
        checks=checks if checks is not None else [_check()],
        required_checks=required_checks if required_checks is not None else ["Some Gate"],
        required_reviews=required_reviews,
        require_up_to_date=require_up_to_date,
        reviews=reviews or [],
    )


class TestMergeYes:
    def test_all_observed_preconditions_pass(self) -> None:
        verdict = _evidence().merge_verdict()
        assert verdict.state == "YES"
        assert "Yes" in verdict.headline
        assert not verdict.blockers

    def test_yes_reasons_cite_observations(self) -> None:
        verdict = _evidence().merge_verdict()
        assert len(verdict.reasons) >= 1
        assert all(r.detail for r in verdict.reasons)


class TestMergeNo:
    def test_failing_check_blocks_and_is_named(self) -> None:
        evidence = _evidence(
            checks=[_check(name="A Gate"), _check(name="B Gate", conclusion="FAILURE")],
            required_checks=["A Gate", "B Gate"],
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "NO"
        assert any("B Gate" in r.detail for r in verdict.blockers)

    def test_running_check_blocks(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Slow Gate", status="IN_PROGRESS", conclusion="")],
            required_checks=["Slow Gate"],
        )
        assert evidence.merge_verdict().state == "NO"

    def test_required_check_never_reported_blocks(self) -> None:
        evidence = _evidence(
            checks=[_check(name="A Gate")],
            required_checks=["A Gate", "Never Ran"],
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "NO"
        assert any("Never Ran" in r.detail for r in verdict.blockers)

    def test_draft_blocks(self) -> None:
        assert _evidence(meta=_meta(draft=True)).merge_verdict().state == "NO"

    def test_dirty_blocks_with_conflict_reason(self) -> None:
        verdict = _evidence(meta=_meta(mergeable_state="dirty")).merge_verdict()
        assert verdict.state == "NO"
        assert any("conflict" in r.detail.lower() for r in verdict.blockers)

    def test_not_mergeable_blocks(self) -> None:
        evidence = _evidence(meta=_meta(mergeable=False, mergeable_state="dirty"))
        assert evidence.merge_verdict().state == "NO"

    def test_missing_required_approvals_block(self) -> None:
        evidence = _evidence(required_reviews=2, reviews=[])
        verdict = evidence.merge_verdict()
        assert verdict.state == "NO"
        assert any("approving review" in r.detail for r in verdict.blockers)

    def test_sufficient_approvals_do_not_block(self) -> None:
        evidence = _evidence(
            required_reviews=1,
            reviews=[{"state": "APPROVED", "user": {"login": "reviewer"}}],
        )
        assert evidence.merge_verdict().state == "YES"

    def test_repeated_approvals_from_one_user_count_once(self) -> None:
        evidence = _evidence(
            required_reviews=2,
            reviews=[
                {"state": "APPROVED", "user": {"login": "same"}},
                {"state": "APPROVED", "user": {"login": "same"}},
            ],
        )
        assert evidence.merge_verdict().state == "NO"


class TestNonRequiredChecksAreAdvisory:
    """A failing check the base does NOT require must not block the merge.

    GitHub's own answer in that case is ``mergeable: true`` with state
    ``unstable`` — a report that says NO would contradict the platform and be
    wrong. Non-required failures are disclosed as advisory evidence instead.
    """

    def test_failing_non_required_check_does_not_block(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Required Gate"), _check(name="Extra", conclusion="FAILURE")],
            required_checks=["Required Gate"],
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "YES"
        assert not verdict.blockers

    def test_advisory_failure_is_still_disclosed(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Required Gate"), _check(name="Extra", conclusion="FAILURE")],
            required_checks=["Required Gate"],
        )
        verdict = evidence.merge_verdict()
        assert any("Extra" in r.detail for r in verdict.reasons)
        assert any("does not gate" in r.detail for r in verdict.reasons)

    def test_failing_required_check_still_blocks(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Required Gate", conclusion="FAILURE")],
            required_checks=["Required Gate"],
        )
        assert evidence.merge_verdict().state == "NO"

    def test_unreadable_protection_defers_to_github_mergeable_true(self) -> None:
        """Unreadable protection must not invent a blocker GitHub does not report.

        A workflow token cannot read branch protection, so the required list is
        unknown — but GitHub computed mergeability WITH those rules, so its
        ``mergeable: true`` is authoritative over our gap.
        """
        evidence = _evidence(
            checks=[_check(conclusion="FAILURE")],
            required_checks=[],
            required_reviews=None,  # protection unreadable
            meta=_meta(mergeable=True, mergeable_state="unstable"),
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "YES"
        assert not verdict.blockers

    def test_unreadable_protection_with_unmergeable_is_no(self) -> None:
        evidence = _evidence(
            checks=[_check(conclusion="FAILURE")],
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=False, mergeable_state="blocked"),
        )
        assert evidence.merge_verdict().state == "NO"

    def test_unreadable_protection_and_unknown_mergeability_is_unknown(self) -> None:
        evidence = _evidence(
            checks=[_check(conclusion="FAILURE")],
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=None, mergeable_state=UNKNOWN),
        )
        assert evidence.merge_verdict().state == "UNKNOWN"


class TestGitHubMergeableIsAuthoritative:
    """GitHub computes ``mergeable`` WITH the base's protection rules applied.

    A workflow token cannot read ``/branches/<b>/protection`` (no
    ``administration`` scope exists for it), so the reporter is often unable to
    name the required checks. In that case GitHub's own answer must win: the
    verdict explains it, and must never contradict it with an invented blocker.
    """

    def test_mergeable_true_yields_yes_despite_unreadable_protection(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Optional Lint", conclusion="FAILURE")],
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=True, mergeable_state="unstable"),
        )
        assert evidence.merge_verdict().state == "YES"

    def test_mergeable_false_yields_no(self) -> None:
        evidence = _evidence(
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=False, mergeable_state="dirty"),
        )
        assert evidence.merge_verdict().state == "NO"

    def test_null_mergeability_is_unknown_not_yes(self) -> None:
        """GitHub returns null while computing — that is not a green light."""
        evidence = _evidence(
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=None, mergeable_state=UNKNOWN),
        )
        assert evidence.merge_verdict().state == "UNKNOWN"

    def test_yes_cites_the_mergeable_fact(self) -> None:
        evidence = _evidence(
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=True, mergeable_state="clean"),
        )
        verdict = evidence.merge_verdict()
        assert any("mergeable" in r.detail.lower() for r in verdict.reasons)

    def test_read_gap_is_still_disclosed_in_a_yes(self) -> None:
        evidence = _evidence(
            checks=[_check(name="Optional Lint", conclusion="FAILURE")],
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=True, mergeable_state="unstable"),
        )
        verdict = evidence.merge_verdict()
        assert any("could not be read" in r.detail for r in verdict.reasons)
        assert any("Optional Lint" in r.detail for r in verdict.reasons)


class TestBehindBase:
    """'Behind' blocks only when the base requires up-to-date branches."""

    def test_behind_blocks_when_base_is_strict(self) -> None:
        evidence = _evidence(meta=_meta(mergeable_state="behind"), require_up_to_date=True)
        verdict = evidence.merge_verdict()
        assert verdict.state == "NO"
        assert any("up to date" in r.detail for r in verdict.blockers)

    def test_behind_is_not_blocking_when_base_is_not_strict(self) -> None:
        evidence = _evidence(meta=_meta(mergeable_state="behind"), require_up_to_date=False)
        assert evidence.merge_verdict().state == "YES"

    def test_behind_with_unknown_strictness_is_unknown_not_yes(self) -> None:
        """Unknown strictness + unknown mergeability must not claim YES."""
        evidence = _evidence(
            meta=_meta(mergeable_state="behind", mergeable=None),
            require_up_to_date=None,
        )
        assert evidence.merge_verdict().state == "UNKNOWN"

    def test_behind_with_unreadable_protection_defers_to_mergeable(self) -> None:
        """GitHub says mergeable → behind does not block (base is not strict)."""
        evidence = _evidence(
            meta=_meta(mergeable_state="behind", mergeable=True),
            require_up_to_date=None,
        )
        assert evidence.merge_verdict().state == "YES"


class TestAlreadyMerged:
    def test_merged_is_terminal_even_with_failing_checks(self) -> None:
        evidence = _evidence(
            checks=[_check(conclusion="FAILURE")],
            meta=_meta(merged=True, pr_state="CLOSED", merged_at="2026-09-27T00:00:00Z"),
            required_checks=["Some Gate"],
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "MERGED"
        assert not verdict.blockers

    def test_closed_unmerged_blocks(self) -> None:
        evidence = _evidence(meta=_meta(merged=False, pr_state="CLOSED"))
        assert evidence.merge_verdict().state == "NO"


class TestUnknownIsNotYes:
    """A verdict of YES must not be reachable through unreadable evidence."""

    def test_unreadable_protection_alone_is_resolved_by_mergeable(self) -> None:
        """A read gap that GitHub's mergeability already covers is not UNKNOWN."""
        evidence = _evidence(
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=True, mergeable_state="clean"),
        )
        assert evidence.merge_verdict().state == "YES"

    def test_unreadable_protection_with_no_mergeability_is_unknown(self) -> None:
        evidence = _evidence(
            required_checks=[],
            required_reviews=None,
            meta=_meta(mergeable=None, mergeable_state=UNKNOWN),
        )
        verdict = evidence.merge_verdict()
        assert verdict.state == "UNKNOWN"
        assert not verdict.blockers

    def test_unreadable_mergeability_is_unknown(self) -> None:
        evidence = _evidence(
            meta=_meta(mergeable=None, mergeable_state=UNKNOWN),
        )
        assert evidence.merge_verdict().state == "UNKNOWN"

    def test_unknown_names_the_gap(self) -> None:
        evidence = _evidence(required_checks=[], required_reviews=None)
        verdict = evidence.merge_verdict()
        assert any(
            "could not be read" in r.detail or "Not observed" in r.detail for r in verdict.reasons
        )


class TestRendering:
    def test_stage_is_rendered_for_each_state(self) -> None:
        cases = [
            (_evidence(), "Merge? ***Yes***"),
            (_evidence(checks=[_check(conclusion="FAILURE")]), "Merge? ***No***"),
            (
                _evidence(
                    required_checks=[],
                    required_reviews=None,
                    meta=_meta(mergeable=None, mergeable_state=UNKNOWN),
                ),
                "Merge? ***Unknown***",
            ),
            (_evidence(meta=_meta(merged=True)), "Merge? ***Yes — already merged***"),
        ]
        for evidence, expected in cases:
            body = render_report(evidence)
            assert expected in body, expected

    def test_no_state_lists_its_reasons(self) -> None:
        evidence = _evidence(checks=[_check(conclusion="FAILURE")])
        body = render_report(evidence)
        assert "Merge? ***No***" in body
        assert "blocker" in body.lower()
        # The blocker list must name the actual failing check.
        assert "Some Gate" in body

    def test_verdict_is_the_LAST_stage_in_the_report(self) -> None:
        """It is the conclusion — every other stage is evidence for it."""
        body = render_report(_evidence())
        verdict_at = body.find("Final Verdict")
        assert verdict_at > 0
        for earlier in ("Revision", "Test Matrix", "Required checks", "Legend"):
            pos = body.find(earlier)
            if pos > 0:
                assert pos < verdict_at, f"{earlier} must precede the verdict"

    def test_truncation_never_eats_the_verdict(self) -> None:
        """The verdict is last; a size cut must keep it visible."""
        from nexus_scalp.pr_evidence.renderer import _truncate  # internal by design

        evidence = _evidence(checks=[_check(conclusion="FAILURE")])
        body = render_report(evidence)
        padded = "x" * 70_000 + body
        out = _truncate(padded)
        assert len(out) <= 60_000
        assert "Final Verdict" in out

    def test_report_still_carries_the_marker_once(self) -> None:
        body = render_report(_evidence())
        assert body.count("<!-- NSE-EVIDENCE-REPORT -->") == 1

    def test_verdict_is_generic_no_hardcoded_pr_facts(self) -> None:
        """The stage must not embed any repo/PR/check specific string."""
        body = render_report(_evidence())
        assert _SHA not in body
        assert "Opselon/" not in body
        assert "github.com" not in body
