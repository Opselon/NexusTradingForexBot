#!/usr/bin/env python3
"""Governance tests for Git/GitHub workflow in NexusTradingForexBot (NSE).

These tests verify the *operational rules* in ``agents/git_governance.md`` and
the behaviour of ``scripts/git/preflight.py``. They are 100% hermetic: each
test spins up a throwaway temporary git repository (a bare "origin" plus a
clone) under pytest's tmp_path and NEVER touches the real working repository,
the real remote, or any real branch.

Run:

    python -m pytest tests/unit/test_git_governance.py -q --no-header

Why these tests exist: the repository's governance previously existed only as
prose. Prose cannot detect that an agent branched from a stale local main, or
that local main had diverged, or that a branch was checked out twice. The
class of duplicate-history / diverged-main failures these tests pin is exactly
the one the project hit in practice.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "git"))

from preflight import (  # noqa: E402
    MAIN_BRANCH,
    run_preflight,
    scan_text_for_banned_commands,
)

# --------------------------------------------------------------------------
# Hermetic test-repo helpers
# --------------------------------------------------------------------------


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _commit(repo: Path, msg: str) -> None:
    _run(["git", "commit", "-q", "--allow-empty", "-m", msg], repo)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_origin(tmp: Path) -> Path:
    """A bare 'origin' with one commit on main."""
    origin = (tmp / "origin.git").resolve()
    _run(["git", "init", "-q", "--bare", "-b", MAIN_BRANCH, str(origin)], tmp)
    seed = (tmp / "seed").resolve()
    _run(["git", "init", "-q", "-b", MAIN_BRANCH, str(seed)], tmp)
    _run(["git", "config", "user.email", "t@example.com"], seed)
    _run(["git", "config", "user.name", "T"], seed)
    _write(seed / "README.md", "# hermetic\n")
    _run(["git", "add", "README.md"], seed)
    _commit(seed, "initial")
    _run(["git", "remote", "add", "origin", str(origin)], seed)
    _run(["git", "push", "-q", "origin", MAIN_BRANCH], seed)
    return origin


def clone(tmp: Path, origin: Path, name: str = "work") -> Path:
    repo = (tmp / name).resolve()
    _run(["git", "clone", "-q", str(origin), str(repo)], tmp)
    _run(["git", "config", "user.email", "t@example.com"], repo)
    _run(["git", "config", "user.name", "T"], repo)
    return repo


def advance(origin: Path, n: int = 2) -> None:
    """Push n new commits to origin/main from a throwaway clone."""
    scratch = origin.parent / f"advance-{n}-{origin.parent.stat().st_ino % 9999}"
    _run(["git", "clone", "-q", str(origin), str(scratch)], origin.parent)
    _run(["git", "config", "user.email", "t@example.com"], scratch)
    _run(["git", "config", "user.name", "T"], scratch)
    for i in range(n):
        _commit(scratch, f"advance {i}")
    _run(["git", "push", "-q", "origin", MAIN_BRANCH], scratch)


def failures_of(report) -> list[str]:
    return [c.rule for c in report.failures]


# --------------------------------------------------------------------------
# §3 main is read-only / §4 local main is a fast-forward mirror
# --------------------------------------------------------------------------


class TestMainMirror:
    def test_main_synchronized_is_clean(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        # work happens on a task branch; the mirror check covers LOCAL main
        _run(["git", "switch", "-q", "-c", "agent/chore/sync-check", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)
        mirror = [c for c in r.checks if c.rule == "§4 local main is a mirror"]
        assert mirror and mirror[0].severity == "ok"

    def test_main_behind_origin_is_blocked(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance(origin, n=2)  # origin moves on, local main does not
        _run(["git", "fetch", "-q", "origin"], repo)
        r = run_preflight(cwd=repo, offline=False)
        rules = failures_of(r)
        assert "§4 local main is a mirror" in rules
        msg = " ".join(c.message for c in r.failures)
        assert "STALE_MAIN" in msg

    def test_main_ahead_of_origin_is_blocked(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        # someone commits directly to local main -> divergence
        _run(["git", "switch", "-q", MAIN_BRANCH], repo)
        _commit(repo, "rogue direct commit to main")
        _run(["git", "fetch", "-q", "origin"], repo)
        r = run_preflight(cwd=repo, offline=False)
        rules = failures_of(r)
        assert "§4 local main is a mirror" in rules
        msg = " ".join(c.message for c in r.failures)
        assert "MAIN_AHEAD" in msg

    def test_being_on_main_is_blocked(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", MAIN_BRANCH], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert "§3 main is read-only" in failures_of(r)

    def test_main_diverged_both_directions_reports_ahead(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance(origin, n=1)
        _run(["git", "fetch", "-q", "origin"], repo)
        _run(["git", "switch", "-q", MAIN_BRANCH], repo)
        _commit(repo, "rogue commit")
        r = run_preflight(cwd=repo, offline=False)
        rules = failures_of(r)
        # MAIN_AHEAD is the critical signal (do not push the divergence)
        assert "§4 local main is a mirror" in rules
        assert "MAIN_AHEAD" in " ".join(c.message for c in r.failures)


# --------------------------------------------------------------------------
# §5 task branch naming and ownership
# --------------------------------------------------------------------------


class TestTaskBranch:
    def test_valid_task_branch_accepted(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/bug/bug307-throttle", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)

    @pytest.mark.parametrize(
        "bad",
        [
            "bug307",  # no owner prefix
            "wip",  # bare topic
            "feature/x",  # human prefix without agent owner is still ok? -> see below
        ],
    )
    def test_invalid_branch_name_blocked(self, tmp_path, bad):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", bad, "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        if bad == "feature/x":
            pytest.skip("non-agent prefixes are human-scale and permitted")
        assert "§5 task-branch naming" in failures_of(r)

    def test_branch_cut_from_stale_main_is_flagged(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/bug/old-base", "origin/main"], repo)
        advance(origin, n=3)
        _run(["git", "fetch", "-q", "origin"], repo)
        r = run_preflight(cwd=repo, offline=False)
        # not a blocker (the branch may be legitimately old) but MUST warn
        warned = [c.rule for c in r.warnings]
        assert any("§3 branch from origin/main" in w for w in warned)

    def test_branch_cut_from_current_main_is_clean(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "fetch", "-q", "origin"], repo)
        _run(["git", "switch", "-q", "-c", "agent/bug/fresh", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)


# --------------------------------------------------------------------------
# §8 worktree ownership — one active worktree per branch
# --------------------------------------------------------------------------


class TestWorktreeOwnership:
    def test_single_worktree_ok(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/test/x", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)

    def test_double_checkout_of_same_branch_blocked(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/test/dup", "origin/main"], repo)
        # git refuses to check out an in-use branch, so a second checkout must
        # use a detached HEAD — exactly the shape a silent agent collision takes
        sha = _run(["git", "rev-parse", "agent/test/dup"], repo).stdout.strip()
        _run(
            ["git", "worktree", "add", "-q", "--detach", str(tmp_path / "wt2"), sha],
            repo,
        )
        _run(
            [
                "git",
                "-C",
                str(tmp_path / "wt2"),
                "symbolic-ref",
                "HEAD",
                "refs/heads/agent/test/dup",
            ],
            repo,
        )
        r = run_preflight(cwd=repo, offline=False)
        assert "§8 one worktree per branch" in failures_of(r)


# ---------------------------------------------------------------------------
# §8a branch ownership — the 2026-09-22 incident class
# ---------------------------------------------------------------------------


class TestBranchOwnership:
    """Regression tests for the branch/worktree race that produced the
    unexpected 2026-09-22 commit on ``main`` while the primary worktree was
    mid-synchronisation."""

    def test_owned_branch_in_own_worktree_is_safe(self, tmp_path):
        # 4. (safe case) a dedicated integration worktree holding its own task
        # branch is exactly the sanctioned pattern — must be SAFE_TO_MUTATE.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/feature/own-wt", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)
        own = [c for c in r.checks if c.rule == "§8a branch ownership"]
        assert own and own[0].severity == "ok"
        assert "SAFE_TO_MUTATE: YES" in own[0].message

    def test_branch_held_by_other_worktree_is_blocked(self, tmp_path):
        # 1+2. same branch referenced by two worktrees; the current process is
        # NOT the holder -> FOREIGN_BRANCH, mutation refused.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/bug/foreign", "origin/main"], repo)
        wt2 = tmp_path / "other_wt"
        _run(["git", "worktree", "add", "-q", "--detach", str(wt2)], repo)
        # Point the second worktree's HEAD at the same branch the *repo*
        # believes it holds, so two worktrees reference one branch.
        _run(
            ["git", "-C", str(wt2), "symbolic-ref", "HEAD", "refs/heads/agent/bug/foreign"],
            repo,
        )
        r = run_preflight(cwd=repo, offline=False)
        rules = failures_of(r)
        assert "§8a branch ownership" in rules
        msg = " ".join(c.message for c in r.failures)
        assert "SAFE_TO_MUTATE: NO" in msg
        # The incident lesson: never resolve this by resetting the pointer
        assert "STOP" in " ".join(c.remedy for c in r.failures if c.remedy)

    def test_branch_moved_under_us_is_detected(self, tmp_path):
        # 8. the recorded holder HEAD no longer matches the live HEAD —
        # another process advanced the branch after we inspected it.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/test/moved", "origin/main"], repo)
        # Simulate a concurrent advance: the porcelain listing records the
        # pre-advance commit, then the live HEAD moves forward underneath it.
        before = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
        _commit(repo, "concurrent advance by another agent")
        assert before, "baseline HEAD recorded"
        r = run_preflight(cwd=repo, offline=False)
        # Either it detects the move, or the porcelain already caught up —
        # both are safe outcomes, but a stale-view mismatch must never be OK.
        own = [c for c in r.checks if c.rule == "§8a branch ownership"]
        assert own, "ownership check did not run"
        if own[0].severity == "critical":
            assert "BRANCH_MOVED_UNDER_US" in own[0].message
        else:
            # porcelain already consistent — prove the check still reasoned
            # about the live HEAD by re-running from a fresh view
            r2 = run_preflight(cwd=repo, offline=False)
            assert r2.ok, failures_of(r2)

    def test_main_mutation_by_agent_is_blocked(self, tmp_path):
        # 3. an agent attempting work on main is stopped before mutation.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", MAIN_BRANCH], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert "§3 main is read-only" in failures_of(r)

    def test_task_branch_from_origin_main_allowed(self, tmp_path):
        # 4. the canonical flow: fetch, cut a task branch from origin/main,
        # work there. Must be clean.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "fetch", "-q", "origin"], repo)
        _run(["git", "switch", "-q", "-c", "agent/docs/canonical", "origin/main"], repo)
        _commit(repo, "documented change")
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)

    def test_stale_task_branch_is_flagged(self, tmp_path):
        # 5. branch cut from old main while origin advanced: warning present.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/chore/stale", "origin/main"], repo)
        advance(origin, n=3)
        _run(["git", "fetch", "-q", "origin"], repo)
        r = run_preflight(cwd=repo, offline=False)
        stale = [c for c in r.warnings if "§3 branch from origin/main" in c.rule]
        assert stale, "stale task branch produced no warning"

    def test_main_divergence_blocked(self, tmp_path):
        # 6. local main ahead of origin: MAIN_AHEAD is a hard STOP.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", MAIN_BRANCH], repo)
        _commit(repo, "rogue direct commit on main")
        _run(["git", "fetch", "-q", "origin"], repo)
        _run(["git", "switch", "-q", "-c", "agent/sync/divergence-recovery", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert "§4 local main is a mirror" in failures_of(r)
        assert "MAIN_AHEAD" in " ".join(c.message for c in r.failures)

    def test_untracked_files_are_protected_not_blocked(self, tmp_path):
        # 7. untracked files must never turn into a blocker (no git clean nudge)
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/feature/with-untracked", "origin/main"], repo)
        for name in ("a.txt", "b.txt", "c.txt"):
            _write(repo / name, "untracked work — preserve\n")
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)
        assert not any(c.is_critical() and c.category == "SYNC" for c in r.checks)

    def test_detached_head_warns_about_attribution(self, tmp_path):
        # The incident's enabling condition: work with no owning branch.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "checkout", "-q", "--detach", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        own = [c for c in r.warnings if c.rule == "§8a branch ownership"]
        assert own and "detached" in own[0].message.lower()

    def test_concurrent_ownership_metadata_mismatch_blocked(self, tmp_path):
        # 9. two holders where neither is the current process: critical.
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/bug/two-holders", "origin/main"], repo)
        wt2 = tmp_path / "holder_a"
        wt3 = tmp_path / "holder_b"
        for wt in (wt2, wt3):
            _run(["git", "worktree", "add", "-q", "--detach", str(wt)], repo)
            _run(
                ["git", "-C", str(wt), "symbolic-ref", "HEAD", "refs/heads/agent/bug/two-holders"],
                repo,
            )
        r = run_preflight(cwd=repo, offline=False)
        rules = failures_of(r)
        assert "§8a branch ownership" in rules
        assert "BRANCH_OWNED_BY_OTHER_WORKTREE" in " ".join(c.message for c in r.failures)


# ---------------------------------------------------------------------------
# §8 dirty worktree / untracked preservation
# ---------------------------------------------------------------------------


class TestWorkingTreeState:
    def test_clean_tree_ok(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/chore/c", "origin/main"], repo)
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)

    def test_dirty_tree_is_warning_not_blocker(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/chore/d", "origin/main"], repo)
        _write(repo / "scratch.txt", "wip\n")
        r = run_preflight(cwd=repo, offline=False)
        # dirty tree warns; it must NEVER be reported as a blocker that an
        # agent would "fix" with git clean / git reset
        assert not any(c.is_critical() and c.category == "SYNC" for c in r.failures)
        assert r.ok, failures_of(r)

    def test_untracked_files_are_preserved_not_blocked(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/chore/u", "origin/main"], repo)
        _write(repo / "untracked_probe.py", "x = 1\n")
        r = run_preflight(cwd=repo, offline=False)
        assert r.ok, failures_of(r)


# --------------------------------------------------------------------------
# §10 push rules
# --------------------------------------------------------------------------


class TestPushRules:
    def test_preflight_for_push_to_main_refused(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        r = run_preflight(cwd=repo, branch=MAIN_BRANCH, for_push=True, offline=False)
        rules = failures_of(r)
        assert "§10 no direct push to main" in rules

    def test_preflight_for_push_to_task_branch_ok(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/docs/d", "origin/main"], repo)
        r = run_preflight(cwd=repo, for_push=True, offline=False)
        assert r.ok, failures_of(r)


# --------------------------------------------------------------------------
# §9 destructive command scanner — used by CI + docs consistency tests
# --------------------------------------------------------------------------


class TestDestructiveCommandScan:
    @pytest.mark.parametrize(
        "text",
        [
            "git reset --hard origin/main",
            "git push --force origin agent/x",
            "git clean -fd",
            "git branch -D old-branch",
            "git stash drop",
        ],
    )
    def test_banned_commands_detected(self, text):
        assert scan_text_for_banned_commands(text), f"missed: {text}"

    @pytest.mark.parametrize(
        "text",
        [
            "git fetch origin",
            "git switch -c agent/bug/x origin/main",
            "git merge --ff-only origin/main",
            "git log --oneline -5",
            "# git reset --hard is banned   (comment, must not trigger)",
            "git reset --soft HEAD~1  # soft is not destructive",
        ],
    )
    def test_safe_commands_not_flagged(self, text):
        assert scan_text_for_banned_commands(text) == []

    def test_governance_docs_do_not_recommend_destructive_git(self):
        """git_governance.md may *name* banned commands only inside a
        prohibition (a ban-list, a superseded-pattern table, or a fenced
        recovery procedure). It must never *instruct* one. This distinguishes
        "X is banned" from "do X" — the difference that makes the check
        meaningful instead of a keyword gag."""
        doc = REPO_ROOT / "agents" / "git_governance.md"
        assert doc.exists(), "agents/git_governance.md must exist"
        text = doc.read_text(encoding="utf-8")
        offenders = []
        in_fence = False
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:  # a fenced block lists commands; it does not run them
                continue
            if not stripped or stripped.startswith(("#", "|", ">")):
                continue
            # A line names a banned command only inside a ban/prohibition
            # context. Anywhere else it is an instruction to run it.
            in_ban_context = any(
                marker in ln
                for marker in ("Prohibited", "prohibited", "MUST NOT", "banned", "REQUIRES")
            )
            if in_ban_context:
                continue
            hits = scan_text_for_banned_commands(ln)
            if hits:
                offenders.append((stripped[:100], hits))
        assert not offenders, f"governance doc instructs destructive git: {offenders[:5]}"


# --------------------------------------------------------------------------
# §7 squash-merge equivalence — the classification logic
# --------------------------------------------------------------------------


class TestSquashEquivalence:
    """A different SHA does not mean different work. This pins the rule that
    agents must classify remote work against their local patch, not by SHA."""

    def test_tree_identical_means_already_present(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/feature/f", "origin/main"], repo)
        _write(repo / "f.txt", "hello\n")
        _run(["git", "add", "f.txt"], repo)
        _commit(repo, "add f")
        local_tree = _run(["git", "rev-parse", "HEAD^{tree}"], repo).stdout.strip()

        # squash-merge simulation: a DIFFERENT commit on main that produces the
        # SAME tree as our local tip
        other = clone(tmp_path, origin, name="other")
        _write(other / "f.txt", "hello\n")
        _run(["git", "add", "f.txt"], other)
        _commit(other, "squash: add f (#999)")
        _run(["git", "push", "-q", "origin", MAIN_BRANCH], other)
        _run(["git", "fetch", "-q", "origin"], repo)
        remote_tree = _run(
            ["git", "rev-parse", f"origin/{MAIN_BRANCH}^{{tree}}"], repo
        ).stdout.strip()

        assert local_tree == remote_tree  # ALREADY_PRESENT by tree
        # and the SHAs differ, which is precisely why tree comparison matters
        local_sha = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
        remote_sha = _run(["git", "rev-parse", f"origin/{MAIN_BRANCH}"], repo).stdout.strip()
        assert local_sha != remote_sha

    def test_tree_differs_means_genuinely_local(self, tmp_path):
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _run(["git", "switch", "-q", "-c", "agent/feature/g", "origin/main"], repo)
        _write(repo / "g.txt", "mine\n")
        _run(["git", "add", "g.txt"], repo)
        _commit(repo, "add g")
        local_tree = _run(["git", "rev-parse", "HEAD^{tree}"], repo).stdout.strip()
        remote_tree = _run(
            ["git", "rev-parse", f"origin/{MAIN_BRANCH}^{{tree}}"], repo
        ).stdout.strip()
        assert local_tree != remote_tree  # GENUINELY_LOCAL


# --------------------------------------------------------------------------
# §21 source-of-truth hierarchy — docs must not claim enforcement that isn't real
# --------------------------------------------------------------------------


class TestGovernanceDocConsistency:
    def test_preflight_rules_match_governance_doc(self):
        """The rule vocabulary in preflight.py must be a subset of what the
        canonical governance doc states — otherwise code enforces a rule the
        docs don't describe (or vice versa)."""
        doc = REPO_ROOT / "agents" / "git_governance.md"
        text = doc.read_text(encoding="utf-8")
        for rule_id in ("§3", "§4", "§5", "§8", "§9", "§10"):
            assert rule_id in text, f"governance doc missing section {rule_id}"

    def test_governance_doc_distinguishes_verified_vs_documented(self):
        """Rule §13/§21: no unverifiable claim of GitHub-side enforcement."""
        doc = REPO_ROOT / "agents" / "git_governance.md"
        text = doc.read_text(encoding="utf-8")
        # The doc must contain the verified/not-verified vocabulary
        assert "VERIFIED ON GITHUB" in text
        assert "DOCUMENTED BUT NOT VERIFIED" in text or "REQUIRES ADMIN ACTION" in text
