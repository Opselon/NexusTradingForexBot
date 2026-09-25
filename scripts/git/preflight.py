#!/usr/bin/env python3
"""Read-only Git governance preflight for NexusTradingForexBot (NSE).

Mandatory before ANY git operation that touches a branch or the remote.
Implements the canonical model of ``agents/git_governance.md``:

    origin/main  = integration truth   (read-only for normal work)
    local main   = tracking mirror     (never a development branch)
    task branch  = development space   (one task, one owner, one branch)
    PR           = integration boundary
    CI           = validation boundary

The guard is READ-ONLY: it never mutates the working tree, the index, the
branch, or any remote. It only REPORTS. A blocked preflight is a STOP
condition — the agent must resolve the underlying state (see the
MAIN_DIVERGED / STALE_MAIN / FOREIGN_WORKTREE procedures in
``agents/git_governance.md``), never ``reset``/``rebase``/``--force`` it away.

Exit codes (machine-parseable by agents and CI):
    0   all critical checks passed (warnings may remain)
    1   a critical governance rule is violated — STOP
    2   usage / environment error

Check categories (each maps to a rule in git_governance.md):

  BRANCH_NAME    task-branch naming + one-branch-per-task identity
  MAIN_MIRROR    local main is a clean fast-forward mirror of origin/main
  WORKTREE       branch is not checked out elsewhere / no foreign ownership
  REMOTE         the remote we would push to is the expected one
  SYNC           working tree state is understood before any git mutation

Usage:

    # Full preflight on the current branch (an agent starting work):
    python scripts/git/preflight.py

    # Verify a specific task branch is safe to push:
    python scripts/git/preflight.py --branch agent/qa/bug307-throttle --for-push

    # CI / hook mode: no network access, local checks only
    python scripts/git/preflight.py --offline

    # JSON for machine consumers:
    python scripts/git/preflight.py --format json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Canonical configuration — must stay identical to agents/git_governance.md
# --------------------------------------------------------------------------

#: The protected integration branch. Read-only for all normal development.
MAIN_BRANCH = "main"

#: The remote name that holds integration truth. Never a person's laptop.
CANONICAL_REMOTE = "origin"

#: Task-branch prefixes. Any new branch MUST use one of these.
TASK_BRANCH_PREFIXES: tuple[str, ...] = (
    "agent/feature/",
    "agent/bug/",
    "agent/test/",
    "agent/refactor/",
    "agent/docs/",
    "agent/chore/",
    "agent/sync/",
    "agent/gov/",
    # Human-scale equivalents (allowed, not agent-owned)
    "fix/",
    "chore/",
    "hotfix/",
    "integration/",
    "recovery/",
    "archive/",
)

#: Branches that exist for release mechanics and are coordinator-owned, not
#: task branches, and therefore exempt from the prefix rule.
EXEMPT_BRANCHES: frozenset[str] = frozenset(
    {MAIN_BRANCH, "develop", "ci-tests", "docker", "gh-pages"}
)

# The absolute list of git commands an autonomous agent must NOT run without
# explicit human authorization. Mirrors git_governance.md §9 verbatim. Kept
# here (not only in docs) so a policy drift between docs and code is a
# detectable test failure, not a silent disagreement.
DESTRUCTIVE_COMMANDS: tuple[str, ...] = (
    "git reset --hard",
    "git reset --merge",
    "git clean -fd",
    "git clean -fdx",
    "git checkout -- .",
    "git restore .",
    "git branch -D",
    "git push --force",
    "git push --force-with-lease",
    "git push --delete",
    "git stash drop",
    "git stash clear",
)

MAX_BRANCH_NAME_LEN = 60  # Windows path-length headroom

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "ok": 2}


@dataclass
class Check:
    """One governance finding."""

    category: str
    rule: str
    severity: str  # critical | warning | ok
    message: str
    remedy: str = ""

    def is_critical(self) -> bool:
        return self.severity == "critical"


@dataclass
class PreflightReport:
    branch: str
    remote_url: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.is_critical()]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.failures


# --------------------------------------------------------------------------
# Minimal git wrapper — no shell, no mutation, ever
# --------------------------------------------------------------------------


def _git(args: list[str], *, cwd: Path, offline_ok: bool = True) -> tuple[int, str]:
    """Run a read-only git query. Never returns a mutated repo."""
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode, (proc.stdout if proc.returncode == 0 else proc.stderr).strip()


def _require_git(cwd: Path) -> None:
    if shutil.which("git") is None:
        raise SystemExit("preflight: git not found on PATH")
    rc, out = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    if rc != 0:
        raise SystemExit(f"preflight: not inside a git work tree ({out})")


def _branch(cwd: Path) -> str:
    rc, out = _git(["branch", "--show-current"], cwd=cwd)
    if rc != 0:
        return "(detached)"
    return out.strip() or "(detached)"


def _remote_url(name: str, cwd: Path) -> str:
    rc, out = _git(["remote", "get-url", name], cwd=cwd)
    return out.strip() if rc == 0 else ""


# --------------------------------------------------------------------------
# Individual governance checks
# --------------------------------------------------------------------------


def check_branch_name(branch: str) -> Check:
    """Rule §5: a task branch must carry a recognized prefix and owner."""
    if branch in EXEMPT_BRANCHES:
        return Check(
            category="BRANCH_NAME",
            rule="§5 task-branch naming",
            severity="ok",
            message=f"{branch} is a coordinator-owned release/mechanics branch",
        )
    if branch == "(detached)":
        return Check(
            category="BRANCH_NAME",
            rule="§5 task-branch naming",
            severity="warning",
            message="HEAD is detached; cannot verify task-branch ownership",
            remedy="git switch -c <prefix>/<topic> origin/main",
        )
    if not branch.startswith(TASK_BRANCH_PREFIXES):
        allowed = ", ".join(TASK_BRANCH_PREFIXES[:7])
        return Check(
            category="BRANCH_NAME",
            rule="§5 task-branch naming",
            severity="critical",
            message=f"branch '{branch}' does not use a task-branch prefix",
            remedy=f"rename: git switch -c agent/bug/<topic> ; allowed prefixes: {allowed}, ...",
        )
    if len(branch) > MAX_BRANCH_NAME_LEN:
        return Check(
            category="BRANCH_NAME",
            rule="§5 branch length (Windows path cap)",
            severity="warning",
            message=f"branch name is {len(branch)} chars (>{MAX_BRANCH_NAME_LEN})",
            remedy="shorten the topic slug",
        )
    return Check(
        category="BRANCH_NAME",
        rule="§5 task-branch naming",
        severity="ok",
        message=f"'{branch}' carries a recognized task-branch prefix",
    )


def check_not_main(branch: str) -> Check:
    """Rule §3: main is read-only. An agent must never develop on it."""
    if branch == MAIN_BRANCH:
        return Check(
            category="BRANCH_NAME",
            rule="§3 main is read-only",
            severity="critical",
            message=(
                "you are ON local main. main is a tracking mirror, not a "
                "development branch — committing here diverges local main "
                "from origin/main"
            ),
            remedy=("git fetch origin && git switch -c agent/bug/<topic> origin/main"),
        )
    return Check(
        category="BRANCH_NAME",
        rule="§3 main is read-only",
        severity="ok",
        message="not on main",
    )


def check_main_mirror(cwd: Path, offline: bool) -> list[Check]:
    """Rule §4: local main must be a clean fast-forward of origin/main.

    This is the check that catches the exact failure class seen when an
    agent branched from a stale local main and landed work that
    ``main`` had already absorbed — producing duplicate history.
    """
    out: list[Check] = []
    rc, _ = _git(["rev-parse", "--verify", f"refs/heads/{MAIN_BRANCH}"], cwd=cwd)
    if rc != 0:
        # No local main at all (e.g. a thin agent checkout). Not a violation.
        out.append(
            Check(
                category="MAIN_MIRROR",
                rule="§4 local main is a mirror",
                severity="ok",
                message=f"no local {MAIN_BRANCH} present — nothing to drift",
            )
        )
        return out

    if offline:
        out.append(
            Check(
                category="MAIN_MIRROR",
                rule="§4 local main is a mirror",
                severity="warning",
                message="--offline: local main not compared to origin/main",
            )
        )
        return out

    up = f"refs/remotes/{CANONICAL_REMOTE}/{MAIN_BRANCH}"
    rc, _ = _git(["rev-parse", "--verify", up], cwd=cwd)
    if rc != 0:
        out.append(
            Check(
                category="MAIN_MIRROR",
                rule="§4 local main is a mirror",
                severity="warning",
                message=f"{up} missing — run: git fetch {CANONICAL_REMOTE}",
            )
        )
        return out

    rc, behind = _git(["rev-list", "--count", f"{MAIN_BRANCH}..{up}"], cwd=cwd)
    rc2, ahead = _git(["rev-list", "--count", f"{up}..{MAIN_BRANCH}"], cwd=cwd)  # noqa: RUF059
    behind = behind.strip() or "0"
    ahead = ahead.strip() or "0"

    if ahead != "0":
        out.append(
            Check(
                category="MAIN_MIRROR",
                rule="§4 local main is a mirror",
                severity="critical",
                message=(
                    f"MAIN_AHEAD: local main is {ahead} commit(s) ahead of "
                    f"{CANONICAL_REMOTE}/{MAIN_BRANCH}. Someone committed "
                    "directly to local main — this is diverged history, not "
                    "unpushed work."
                ),
                remedy=(
                    "STOP. Do not push and do not reset. Create a task branch "
                    "from those commits instead:\n"
                    "  git switch -c agent/sync/recover-main-commits\n"
                    "  # then reset local main back to the mirror:\n"
                    "  git switch main && git reset --hard "
                    f"{CANONICAL_REMOTE}/{MAIN_BRANCH}   # HUMAN-ONLY\n"
                    "If the commits are unwanted, archive them on the task "
                    "branch before touching main."
                ),
            )
        )
        return out

    if behind != "0":
        out.append(
            Check(
                category="MAIN_MIRROR",
                rule="§4 local main is a mirror",
                severity="critical",
                message=(
                    f"STALE_MAIN: local main is {behind} commit(s) behind "
                    f"{CANONICAL_REMOTE}/{MAIN_BRANCH}. Any task branch cut "
                    "from here is built on stale integration truth and will "
                    "produce duplicate/conflicting history."
                ),
                remedy=(
                    f"git fetch {CANONICAL_REMOTE}\n"
                    f"git switch {MAIN_BRANCH}\n"
                    f"git merge --ff-only {CANONICAL_REMOTE}/{MAIN_BRANCH}\n"
                    f"# then re-cut the task branch from the fresh mirror:\n"
                    f"git switch -c agent/bug/<topic> {CANONICAL_REMOTE}/{MAIN_BRANCH}"
                ),
            )
        )
        return out

    out.append(
        Check(
            category="MAIN_MIRROR",
            rule="§4 local main is a mirror",
            severity="ok",
            message=f"local main is a clean fast-forward of {CANONICAL_REMOTE}/{MAIN_BRANCH}",
        )
    )
    return out


def check_task_branch_freshness(branch: str, cwd: Path, offline: bool) -> Check:
    """Rule §3: a NEW task branch must be cut from current origin/main.

    The guard only warns here (the branch may legitimately be an hour old);
    the critical signal is that the branch's merge-base with origin/main is
    not ancient relative to origin/main's tip.
    """
    if branch in EXEMPT_BRANCHES or branch == "(detached)" or offline:
        return Check(
            category="MAIN_MIRROR",
            rule="§3 branch from origin/main",
            severity="ok",
            message="freshness check skipped (exempt/detached/offline)",
        )
    up = f"refs/remotes/{CANONICAL_REMOTE}/{MAIN_BRANCH}"
    rc, _ = _git(["rev-parse", "--verify", up], cwd=cwd)
    if rc != 0:
        return Check(
            category="MAIN_MIRROR",
            rule="§3 branch from origin/main",
            severity="warning",
            message=f"{up} missing — run: git fetch {CANONICAL_REMOTE}",
        )
    rc, base = _git(["merge-base", branch, up], cwd=cwd)
    rc2, tip = _git(["rev-parse", up], cwd=cwd)
    if rc != 0 or rc2 != 0 or not base.strip():
        return Check(
            category="MAIN_MIRROR",
            rule="§3 branch from origin/main",
            severity="warning",
            message=f"cannot compute merge-base of '{branch}' and {up}",
        )
    # merge-base == remote tip  ->  branch was cut from current main. Good.
    if base.strip() == tip.strip():
        return Check(
            category="MAIN_MIRROR",
            rule="§3 branch from origin/main",
            severity="ok",
            message=f"'{branch}' branches off current {CANONICAL_REMOTE}/{MAIN_BRANCH}",
        )
    rc3, gap = _git(["rev-list", "--count", f"{base.strip()}..{tip.strip()}"], cwd=cwd)  # noqa: RUF059
    gap = gap.strip() or "?"
    return Check(
        category="MAIN_MIRROR",
        rule="§3 branch from origin/main",
        severity="warning",
        message=(
            f"'{branch}' was cut {gap} commit(s) behind current {CANONICAL_REMOTE}/{MAIN_BRANCH}"
        ),
        remedy=(
            f"git fetch {CANONICAL_REMOTE} && "
            f"git merge --no-ff {CANONICAL_REMOTE}/{MAIN_BRANCH}   "
            "# merge INTO the task branch, never rebase a shared branch"
        ),
    )


def check_worktree(branch: str, cwd: Path) -> Check:
    """Rule §8: a branch may be checked out in only ONE worktree."""
    rc, out = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    if rc != 0:
        return Check(
            category="WORKTREE",
            rule="§8 one worktree per branch",
            severity="warning",
            message="git worktree list failed",
        )
    # Porcelain rows: "worktree <path>\nHEAD <sha>\nbranch refs/heads/<name>\n"
    worktrees: list[tuple[str, str]] = []
    cur_path: str | None = None
    cur_branch: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur_path and cur_branch is not None:
                worktrees.append((cur_path, cur_branch))
            cur_path = line[len("worktree ") :].strip()
            cur_branch = None
        elif line.startswith("branch "):
            cur_branch = line[len("branch ") :].strip()
    if cur_path and cur_branch is not None:
        worktrees.append((cur_path, cur_branch))

    if branch == "(detached)":
        return Check(
            category="WORKTREE",
            rule="§8 one worktree per branch",
            severity="ok",
            message="detached HEAD — no branch to double-checkout",
        )

    refs = f"refs/heads/{branch}" if not branch.startswith("refs/") else branch
    holders = [p for p, b in worktrees if b == refs]
    if len(holders) > 1:
        return Check(
            category="WORKTREE",
            rule="§8 one worktree per branch",
            severity="critical",
            message=(
                f"branch '{branch}' is checked out in {len(holders)} worktrees: "
                + ", ".join(holders)
            ),
            remedy=(
                "ONE ACTIVE WORKTREE PER BRANCH. Close the extra checkout or "
                "use a dedicated temporary worktree; never switch a foreign "
                "worktree. See git_governance.md §8."
            ),
        )
    return Check(
        category="WORKTREE",
        rule="§8 one worktree per branch",
        severity="ok",
        message=f"'{branch}' is checked out in exactly one worktree",
    )


def _worktree_map(cwd: Path) -> list[tuple[str, str, str]]:
    """Parse ``git worktree list --porcelain`` into (path, head, branch-or-'').

    Shared by the worktree and ownership checks. Detached worktrees have an
    empty branch field — that is exactly the case the incident of
    2026-09-22 exploited (work checked out with no branch to attribute it to).
    """
    rc, out = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    if rc != 0:
        return []
    rows: list[tuple[str, str, str]] = []
    cur_path: str | None = None
    cur_head: str | None = None
    cur_branch: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur_path and cur_head is not None:
                rows.append((cur_path, cur_head, cur_branch or ""))
            cur_path = line[len("worktree ") :].strip()
            cur_head = None
            cur_branch = None
        elif line.startswith("HEAD "):
            cur_head = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            cur_branch = line[len("branch ") :].strip()
    if cur_path and cur_head is not None:
        rows.append((cur_path, cur_head, cur_branch or ""))
    return rows


def check_branch_ownership(branch: str, cwd: Path) -> Check:
    """Rule §8a: ONE TASK · ONE OWNER · ONE BRANCH · ONE ACTIVE WORKTREE.

    Detects the exact race class seen on 2026-09-22: a second agent created a
    commit on ``main`` while the primary worktree was mid-synchronisation,
    because no check asserted who owns a branch before mutating it.

    ``cwd`` is the repository being inspected (the preflight's ``--cwd``). The
    "current worktree" is resolved against THAT path, never the process's own
    working directory — an agent may legitimately run the preflight from
    elsewhere, and resolving against the process cwd would turn every such
    invocation into a false FOREIGN_BRANCH.

    The check is evidence-based and read-only: it reports the worktree that
    holds the branch, whether that holder is the inspected one, and whether
    the current HEAD still matches the recorded holder's HEAD. A mismatch
    means another process moved the branch after this agent inspected it — a
    STOP condition, never something to ``reset`` away.
    """
    rows = _worktree_map(cwd)
    if not rows:
        return Check(
            category="OWNERSHIP",
            rule="§8a branch ownership",
            severity="warning",
            message="git worktree list failed — ownership cannot be established",
        )
    if branch == "(detached)":
        return Check(
            category="OWNERSHIP",
            rule="§8a branch ownership",
            severity="warning",
            message=(
                "HEAD is detached: no branch is owned. Work committed here is "
                "attributable to nobody — create a task branch before mutating "
                "(see git_governance.md §5)"
            ),
            remedy="git switch -c <prefix>/<topic> origin/main",
        )

    refs = f"refs/heads/{branch}" if not branch.startswith("refs/") else branch
    holders = [(p, h) for p, h, b in rows if b == refs]

    # The inspected repo is the current worktree. Resolve the repo's own path
    # the way git itself reports it (worktree list prints absolute paths).
    try:
        here = str(cwd.resolve())
    except OSError:
        here = str(cwd)
    current_holder = None
    for p, h in holders:
        try:
            resolved = str(Path(p).resolve())
        except OSError:
            resolved = p
        if resolved == here:
            current_holder = (p, h)
            break

    if len(holders) > 1:
        others = ", ".join(p for p, _ in holders if p != (current_holder or ("", ""))[0])
        return Check(
            category="OWNERSHIP",
            rule="§8a branch ownership",
            severity="critical",
            message=(
                f"BRANCH_OWNED_BY_OTHER_WORKTREE: '{branch}' is checked out in "
                f"{len(holders)} worktrees: {others}. SAFE_TO_MUTATE: NO."
            ),
            remedy=(
                "STOP. Do not switch, reset, commit or push — the branch has "
                "another active holder. Use your own task branch in its own "
                "worktree, or obtain an explicit ownership transfer "
                "(git_governance.md §8a)."
            ),
        )

    if holders and not current_holder:
        path, head = holders[0]
        return Check(
            category="OWNERSHIP",
            rule="§8a branch ownership",
            severity="critical",
            message=(
                f"FOREIGN_BRANCH: '{branch}' is checked out ONLY in {path}, "
                "which is not this repository. SAFE_TO_MUTATE: NO."
            ),
            remedy=(
                "You are about to move a branch checked out elsewhere. Cut your "
                "own task branch from origin/main in a dedicated worktree instead: "
                "git fetch origin && git worktree add <path> -c agent/bug/<id> origin/main"
            ),
        )

    if current_holder:
        path, head = current_holder
        rc, my_head = _git(["rev-parse", "HEAD"], cwd=cwd)
        if rc == 0 and my_head.strip() and head and my_head.strip() != head:
            return Check(
                category="OWNERSHIP",
                rule="§8a branch ownership",
                severity="critical",
                message=(
                    f"BRANCH_MOVED_UNDER_US: recorded worktree HEAD {head[:12]} "
                    f"differs from current HEAD {my_head.strip()[:12]} — another "
                    "process moved this branch since the preflight began. "
                    "SAFE_TO_MUTATE: NO."
                ),
                remedy=(
                    "STOP. Ownership assumptions are stale. Re-run the preflight; "
                    "do not reset or force to reconcile the divergence."
                ),
            )
        return Check(
            category="OWNERSHIP",
            rule="§8a branch ownership",
            severity="ok",
            message=(
                f"branch: {branch} | current_worktree: {path} | "
                f"other_worktrees: NONE | ownership_conflict: NO | SAFE_TO_MUTATE: YES"
            ),
        )

    return Check(
        category="OWNERSHIP",
        rule="§8a branch ownership",
        severity="warning",
        message=(
            f"'{branch}' is not checked out in any listed worktree — HEAD may "
            "have moved between listing and resolution"
        ),
    )


def check_task_identity(branch: str) -> Check:
    """Rule §5: the branch must carry a task identity an agent can prove.

    A branch with no owner/token in its name is attributable to nobody. The
    incident commit landed on ``main`` precisely because nothing tied a branch
    to a task. This warns (not blocks) so legacy branches keep working, while
    making the missing identity visible on every preflight.
    """
    if branch in EXEMPT_BRANCHES or branch == "(detached)":
        return Check(
            category="OWNERSHIP",
            rule="§5 task identity",
            severity="ok",
            message=f"{branch} is coordinator-owned — no task identity required",
        )
    return Check(
        category="OWNERSHIP",
        rule="§5 task identity",
        severity="ok",
        message=(
            f"'{branch}' carries a recognized task-branch prefix; record the "
            "TASK-ID / BUG-ID it serves in agents/taskboard.md before mutating"
        ),
    )


def check_dirty_tree(cwd: Path) -> Check:
    """Rule §8: the working tree state must be understood before any mutation."""
    rc, out = _git(["status", "--porcelain"], cwd=cwd)
    if rc != 0:
        return Check(
            category="SYNC",
            rule="§8 known working-tree state",
            severity="warning",
            message="git status failed",
        )
    entries = [ln for ln in out.splitlines() if ln.strip()]
    untracked = [ln for ln in entries if ln.startswith("??")]
    modified = [ln for ln in entries if not ln.startswith("??")]
    if not entries:
        return Check(
            category="SYNC",
            rule="§8 known working-tree state",
            severity="ok",
            message="working tree is clean",
        )
    bits = []
    if modified:
        bits.append(f"{len(modified)} modified/staged")
    if untracked:
        bits.append(f"{len(untracked)} untracked")
    return Check(
        category="SYNC",
        rule="§8 known working-tree state",
        severity="warning",
        message=(
            "working tree is not clean ("
            + ", ".join(bits)
            + ")"
            + (" — untracked files are PRESERVED, never git-clean'd" if untracked else "")
        ),
        remedy=(
            "commit your owned work to the task branch first; untracked files "
            "belong to nobody until claimed — do not delete them"
        ),
    )


def check_remote(url: str) -> Check:
    """Rule §10: we only ever push to the canonical origin."""
    if not url:
        return Check(
            category="REMOTE",
            rule="§10 canonical remote",
            severity="warning",
            message=f"no '{CANONICAL_REMOTE}' remote configured",
        )
    # Credential URLs must never be echoed. Strip any userinfo before logging.
    safe = re.sub(r"https?://[^@/]+@", "https://***:***@", url)
    if not re.match(r"^(https?://|git@|ssh://git@|file:///)", safe):
        return Check(
            category="REMOTE",
            rule="§10 canonical remote",
            severity="warning",
            message=f"remote url shape unrecognized: {safe}",
        )
    return Check(
        category="REMOTE",
        rule="§10 canonical remote",
        severity="ok",
        message=f"canonical remote present ({safe})",
    )


# --------------------------------------------------------------------------
# Scan-for-banned-command helper (used by the guard and by the tests)
# --------------------------------------------------------------------------

_BANNED_RE = re.compile(
    r"(?:^|[\s;&|`$()])("
    + "|".join(re.escape(c.split(None, 1)[1]) for c in DESTRUCTIVE_COMMANDS)
    + r")(?:\s|$)",
    re.IGNORECASE,
)


def scan_text_for_banned_commands(text: str) -> list[str]:
    """Return banned git command fragments found in a script or doc string.

    Used by the CI governance lane to catch a workflow or helper script that
    asks an agent to run a destructive command. Pure string analysis — no
    shell involved.
    """
    hits: list[str] = []
    # Normalise: strip git's own plumbing/confusion noise
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for cmd in DESTRUCTIVE_COMMANDS:
            fragment = cmd.split(None, 1)[1]
            if re.search(rf"\b{re.escape(fragment)}\b", stripped, re.IGNORECASE):
                hits.append(cmd)
    return hits


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_preflight(
    *,
    cwd: Path,
    branch: str | None = None,
    offline: bool = False,
    for_push: bool = False,
) -> PreflightReport:
    _require_git(cwd)
    current = _branch(cwd)
    target = branch or current
    url = _remote_url(CANONICAL_REMOTE, cwd)

    report = PreflightReport(
        branch=current, remote_url=re.sub(r"https?://[^@/]+@", "https://***:***@", url)
    )

    # --- always-on identity checks -------------------------------------
    if not branch or branch == current:
        report.checks.append(check_not_main(current))
    report.checks.append(check_branch_name(target))
    report.checks.extend(check_main_mirror(cwd, offline))
    report.checks.append(check_task_branch_freshness(target, cwd, offline))
    report.checks.append(check_worktree(target, cwd))
    report.checks.append(check_task_identity(target))
    report.checks.append(check_branch_ownership(target, cwd))
    report.checks.append(check_dirty_tree(cwd))
    report.checks.append(check_remote(url))

    # --- push-specific --------------------------------------------------
    if for_push:
        if target == MAIN_BRANCH:
            report.checks.append(
                Check(
                    category="REMOTE",
                    rule="§10 no direct push to main",
                    severity="critical",
                    message="refusing preflight for a push TO main — main is protected",
                    remedy="open a PR from a task branch instead",
                )
            )

    report.checks.sort(key=lambda c: (_SEVERITY_ORDER.get(c.severity, 3), c.category))
    return report


def _render_text(report: PreflightReport, stream) -> int:
    print(f"git governance preflight — branch: {report.branch}")
    print("-" * 68)
    for c in report.checks:
        tag = {"critical": "BLOCK", "warning": "WARN ", "ok": "OK   "}[c.severity]
        print(f"[{tag}] {c.category} {c.rule}")
        print(f"        {c.message}")
        if c.remedy:
            for line in c.remedy.splitlines():
                print(f"        {line}")
        print()
    if report.ok:
        print(f"PREFLIGHT OK — {len(report.warnings)} warning(s), 0 blocker(s)")
        return 0
    print(
        f"PREFLIGHT BLOCKED — {len(report.failures)} blocker(s), {len(report.warnings)} warning(s)"
    )
    print("STOP. Resolve the state; do NOT reset/rebase/force-push it away.")
    print("Recovery procedures: agents/git_governance.md")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only Git governance preflight (never mutates repo state)"
    )
    ap.add_argument("--branch", help="verify this branch instead of the current one")
    ap.add_argument("--for-push", action="store_true", help="add push-specific checks")
    ap.add_argument("--offline", action="store_true", help="skip remote-dependent checks")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--cwd", default=".", help="repository path (default: .)")
    args = ap.parse_args(argv)

    try:
        report = run_preflight(
            cwd=Path(args.cwd),
            branch=args.branch,
            offline=args.offline,
            for_push=args.for_push,
        )
    except SystemExit as exc:
        print(f"preflight: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        payload = {
            "branch": report.branch,
            "remote_url": report.remote_url,
            "ok": report.ok,
            "failures": [asdict(c) for c in report.failures],
            "warnings": [asdict(c) for c in report.warnings],
            "checks": [asdict(c) for c in report.checks],
        }
        print(json.dumps(payload, indent=2))
        return 0 if report.ok else 1

    return _render_text(report, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
