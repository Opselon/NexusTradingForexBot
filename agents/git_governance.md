# GIT / GITHUB GOVERNANCE MODEL — Nexus Scalp Engine (NSE)

> **Status:** CANONICAL. This file is the single authoritative source for how
> Git and GitHub are used in this repository. Every other document that
> mentions git must agree with this one; if a document disagrees, that
> document is wrong — fix it and reference this file.
> Sibling authority: `agents/multi-agent-git-contract.md` (agent collaboration
> protocol) defers to this file on synchronization, branch, and merge
> mechanics. Decision records DEC-0006 / DEC-0008 remain authoritative for
> merge *authorization* and remote-ref safety.

## 0. Why this file exists

The repository hit the exact failure class this model prevents: an agent
branched from a **stale local main** and produced history that conflicted
with work already on `origin/main`, forcing a destructive rebase-resolve
cycle. The repo had rich governance *prose* but no operational rule that a
machine could check. Prose cannot detect that local main drifted. This file
pairs each rule with its enforcement mechanism, and states plainly when a
rule is documented only and not enforced.

---

## 1. The canonical model

```text
origin/main            ← integration truth (read-only for normal work)
   │
   ▼ fetch
local main             ← tracking MIRROR only. Never a development branch.
   │
   ▼ switch -c <task-branch> origin/main
task branch            ← development space (one task, one owner, one branch)
   │
   ▼ develop + validate locally (ruff → format → mypy → critical suite)
   │
   ▼ push task branch
   │
   ▼ PR to main
   │
   ▼ required CI checks (11 contexts)
   │
   ▼ coordinator approval (DEC-0008) + squash merge
   │
   ▼
origin/main
```

**Governing principles**

| Principle | Meaning |
|---|---|
| ORIGIN/MAIN = INTEGRATION TRUTH | The remote is the only authority on released state |
| LOCAL MAIN = MIRROR | Local main is refreshed, never built upon |
| TASK BRANCH = DEVELOPMENT SPACE | All work happens on an owned branch |
| PR = INTEGRATION BOUNDARY | Nothing reaches main except through a PR |
| CI = VALIDATION BOUNDARY | Green CI is a precondition, never an authorization |
| PROTECTED MAIN = RELEASED STATE | Main's protection configuration is part of the release surface |
| PRESERVE WORK > CLEAN HISTORY | Never destroy work to make git look tidy |
| EVIDENCE > ASSUMPTIONS | Compare patch/tree, not SHA, when judging duplicates |
| ENFORCEMENT > DOCUMENTATION | A rule with no mechanism is a wish |
| STOP ON AMBIGUITY > GUESS | When uncertain, stop and ask |

---

## 2. Source-of-truth hierarchy

From strongest to weakest. A weaker source claiming something a stronger one
contradicts is a defect to fix, not a disagreement to tolerate.

1. **Executable code, tests, and CI** — actual enforced behaviour.
2. **This file (`agents/git_governance.md`)** — authoritative agent policy.
3. **GitHub repository / ruleset configuration** — protection on `main`.
4. **`agents/decisions/`** — decision records (authoritative on their subject).
5. **Engineering governance documentation** (`docs/engineering/`).
6. **Taskboard / operational records** (`agents/taskboard.md`, `locks.yaml`).
7. **README, examples, guides.**

A document must never claim enforcement that does not exist. Where a rule is
documentation-only, it says so (see §13).

---

## 3. Main-branch rules

`main` is **read-only for normal development**.

An agent MUST NOT:

- commit directly to `main`
- create new work from a stale local main
- merge arbitrary branches into `main` locally as a shortcut
- rebase `main`
- reset `main` to make divergence disappear
- force-push `main`
- delete `main` history
- use `main` as a working branch

New work starts:

```bash
git fetch origin
git switch -c agent/<kind>/<topic> origin/main
```

**Enforcement:** `scripts/git/preflight.py` reports `[BLOCK] §3 main is
read-only` when HEAD is on `main`, and `[BLOCK] MAIN_AHEAD` when local main
has commits origin does not. GitHub blocks direct pushes server-side (§13).

---

## 4. Local main synchronization — the ONLY normal pattern

```bash
git fetch origin
git switch main
git merge --ff-only origin/main
```

If `--ff-only` fails: **STOP.** This is the `MAIN_DIVERGED` condition.

Do NOT automatically: reset · rebase · merge · force-push · delete commits ·
discard local work.

`MAIN_DIVERGED` recovery procedure (documented; requires human authorization
for the destructive step):

1. Do not push and do not `reset` anything yet.
2. Preserve the divergent commits on a task branch, where they are safe:
   ```bash
   git switch -c agent/sync/recover-main-commits
   ```
3. Return local main to mirror state. **This is the one destructive
   operation the model permits, and only against a freshly-verified backup:**
   ```bash
   git switch main
   git reset --hard origin/main      # HUMAN AUTHORIZATION REQUIRED
   ```
   Verify the backup branch still holds the commits before this runs.
4. If the divergent commits are wanted, land them through a normal PR from
   `agent/sync/recover-main-commits`. If unwanted, record why in the PR or
   handoff so the history is explained, not silently erased.

Two derived states the preflight names explicitly:

- `STALE_MAIN` — local main is behind `origin/main`. A task branch cut here
  is built on stale truth. Fix the mirror, then re-cut.
- `MAIN_AHEAD` — local main has commits origin lacks. Someone committed on
  main. Never push this; recover per above.

**Enforcement:** preflight `MAIN_MIRROR` checks. These two states are the
direct cause of the duplicate-history class of bug, so both are blockers.

---

## 5. Branch naming and ownership

Standard prefixes (task branch = `<prefix>/<owner>/<topic>`):

| Prefix | Use |
|---|---|
| `agent/feature/` | new capability |
| `agent/bug/` | bug fix |
| `agent/test/` | test work |
| `agent/refactor/` | internal restructuring, no behaviour change |
| `agent/docs/` | documentation |
| `agent/chore/` | tooling, deps, non-functional |
| `agent/sync/` | recovery/synchronization mechanics |
| `agent/gov/` | governance itself |
| `fix/`, `chore/`, `hotfix/`, `integration/`, `recovery/`, `archive/` | human-scale / coordinator-owned |

Rules:

- **ONE TASK · ONE OWNER · ONE BRANCH · ONE ACTIVE WORKTREE PER BRANCH.**
- Branch names map to the `agents/taskboard.md` row / issue / PR they serve.
- Keep names short (Windows path-length headroom; preflight warns past 60
  chars).

**Enforcement:** preflight `BRANCH_NAME` check rejects an unrecognized prefix.

---

## 6. PR rules

Every PR provides:

- **task/issue identity** (TASK-ID / BUG-ID / CHANGE-ID / DEC-ID per the
  existing `agents/git-release-guardian.md` §3 contract)
- **summary of changes**
- **affected areas**
- **validation evidence** (real commands run and their real results)
- **risk notes** where applicable
- **migration notes** where applicable

The PR is the integration boundary. Direct main pushes are prohibited.
Do not bypass required CI. Do not merge merely because local tests passed —
green CI is a precondition, and coordinator approval is a separate gate
(DEC-0008).

**Enforcement:** GitHub requires all 11 status contexts; the repo operates as
a single GitHub account, so server-side *review* cannot be enforced today
(§13, §14). The PR template carries the checklist.

---

## 7. Squash-merge equivalence rules

This repository squash-merges. A squash rewrites the commit SHA, so **a
different SHA does NOT mean different work.** Never judge duplication by SHA.

Before claiming work is "already merged" or "lost", compare:

1. the **actual patch** (`git diff` / `git show`)
2. the **affected files**
3. the **`patch-id`** where useful
4. the **resulting tree** — `git rev-parse <sha>^{tree}`
5. PR / merge metadata
6. ancestry (`git merge-base`)

Required classification:

| Class | Test | Action |
|---|---|---|
| `ALREADY_PRESENT` | resulting tree equal, patch equal | discard local duplicate; do not re-land |
| `PARTIALLY_PRESENT` | some hunks match by patch-id | keep only the non-matching hunks |
| `GENUINELY_LOCAL` | tree differs, patch differs | land via PR |
| `UNCERTAIN` | cannot reconcile confidently | STOP — do not delete anything |

Never declare local work "lost" solely because its original SHA is absent
from `origin/main`. Never declare work "already merged" without evidence.

**Enforcement:** `tests/unit/test_git_governance.py::TestSquashEquivalence`
pins the tree-comparison logic itself (identical trees can carry different
SHAs — the exact trap).

---

## 8. Worktree rules

Before changing a branch:

```bash
git worktree list --porcelain
```

Determine:

- whether the branch is checked out elsewhere
- whether another agent owns it (`agents/locks.yaml`)
- whether a worktree contains uncommitted work
- whether an operation would disturb another agent

An agent MUST NOT:

- delete foreign worktrees
- switch foreign worktrees
- reset foreign worktrees
- overwrite foreign changes
- reuse another agent's branch without explicit ownership transfer

For risky integration work, prefer a dedicated temporary worktree
(`git worktree add <tmp> --detach <sha>`).

**Enforcement:** preflight `WORKTREE` check reports a branch claimed by more
than one worktree. Untracked files are always PRESERVED — a dirty tree is a
warning, never a blocker, so no agent is ever nudged toward `git clean`.

### 8a. Branch ownership before mutation (incident-driven)

On 2026-09-22 a concurrent agent committed to `main` while the primary
worktree was mid-synchronisation, and then re-cherry-picked that work onto
another branch — invisible to any check because nothing asserted who owns a
branch before a mutation. This section closes that class of race.

Before ANY of these operations, an agent MUST resolve ownership:

`git switch` · `git checkout` · `git commit` · `git merge` · `git rebase` ·
`git reset` · `git update-ref` · `git branch -f` · `git worktree add/remove` ·
`git cherry-pick` · `git revert` · `git push`

Resolution means determining, from `git worktree list --porcelain` plus
`git rev-parse HEAD`:

- current branch and current worktree
- which worktree holds that branch
- whether the holder is *this* worktree
- whether the holder's recorded HEAD still equals the current HEAD
- whether the branch carries a TASK-ID / BUG-ID it can be traced to

Then:

```
BRANCH_OWNERSHIP
  branch: agent/feature/ML-001
  current_worktree: C:/.../nse_task_wt
  other_worktrees: NONE
  ownership_conflict: NO
  SAFE_TO_MUTATE: YES
```

Conflict → `SAFE_TO_MUTATE: NO` → **STOP**. Do not switch, reset, commit,
rebase or push. Do NOT "fix" it by deleting the other worktree or moving the
branch pointer — cut your own task branch from `origin/main` in a dedicated
worktree, or obtain an explicit ownership transfer recorded in
`agents/locks.yaml` and `agents/taskboard.md`.

**Ownership can change mid-task.** The repository is a live concurrent system.
If the holder's HEAD no longer matches the HEAD this agent recorded, the
ownership evidence is STALE — re-run the preflight; never reconcile a
divergence with `reset` or `--force`.

**Enforcement:** preflight `OWNERSHIP` checks (`check_branch_ownership`,
`check_task_identity`) are ENFORCED IN CODE: `BRANCH_OWNED_BY_OTHER_WORKTREE`,
`FOREIGN_BRANCH`, and `BRANCH_MOVED_UNDER_US` are critical STOP conditions.

---

## 9. Destructive-git rules

Prohibited for autonomous agents absent explicit human authorization:

```
git reset --hard          git reset --merge        git clean -fd
git clean -fdx            git checkout -- .        git restore .
git branch -D             git push --force         git push --force-with-lease
git push --delete         git stash drop           git stash clear
```

Also prohibited:

- destructive cleanup of foreign work
- deleting potentially useful branches
- silently stashing foreign changes
- blind `ours`/`theirs` conflict resolution
- history rewriting of shared branches

`--force-with-lease` is permitted only on a branch you own, and is never
normal workflow (DEC-0008).

**Enforcement:** `scan_text_for_banned_commands()` in
`scripts/git/preflight.py`; `TestDestructiveCommandScan` proves the scanner
and that this document never *instructs* a banned command. A CI lane can call
the same scanner over `.github/` and `scripts/`.

---

## 10. Push rules

An agent may push only:

- its **owned task branch**, or
- an **approved integration/recovery branch**

An agent MUST NOT push directly to `main`.

```bash
git push -u origin agent/<kind>/<topic>
```

Force push is not part of normal workflow. If a force push would be
necessary: **STOP** and require explicit human authorization.

**Enforcement:** preflight `--for-push` refuses a push whose target is `main`
(Rule §10). GitHub blocks force-push to `main` server-side (§13).

---

## 11. CI/CD git governance

GitHub Actions workflows must not:

- mutate `main` unexpectedly
- force-push or rewrite history
- bypass PR validation
- deploy unreviewed branches
- treat local main as authoritative
- overwrite agent work
- silently clean or reset repositories

The repository's existing validation sequence is preserved and unchanged:

```text
ruff → ruff format → mypy → critical suite → deploy gate (release)
```

The governance lane additionally runs the preflight tool and the governance
tests. Existing gates are not weakened; no test result is invented.

---

## 12. Templates and CODEOWNERS

- `.github/PULL_REQUEST_TEMPLATE.md` carries the §6 PR contract checklist.
- `.github/CODEOWNERS` is the owner-map for conflict arbitration. It
  currently maps all roles to a single account (`@Opselon`) — see §14.
- Contribution instructions point here for git mechanics.

---

## 13. GitHub branch protection — verified status

Verified via the GitHub API on 2026-09-22 at `main`:

| Protection | Status |
|---|---|
| `enforce_admins` | **VERIFIED ON GITHUB** — `enabled: true` |
| `allow_force_pushes` | **VERIFIED ON GITHUB** — `enabled: false` |
| `required_status_checks` | **VERIFIED ON GITHUB** — 11 contexts listed below |
| `strict` (require branches up to date) | **VERIFIED ON GITHUB** — `false`. Documented but not enforced: enabling it would deadlock PRs whose workflows are path-scoped (DEC-0006). REQUIRES ADMIN ACTION to change deliberately. |
| `required_pull_request_reviews` | **DOCUMENTED BUT NOT VERIFIED** — API reports `null`. DEC-0006 records that the API shows no review requirement yet merges still need approval in practice; the requirement lives in UI/org settings the API does not surface. |
| Repository rulesets | **VERIFIED ON GITHUB** — `GET /rulesets` returns `[]` (none configured). |

Required contexts on `main` (all 11 verified present):

```text
CI Integrity and Change Classification
Code Quality & Tests
Py Tests (windows-latest)
Py Tests (macos-latest)
Frontend JS Unit Tests
Validate documentation
Dependency drift (lock vs pyproject)
Migration safety (fail-loud + version postconditions)
CodeQL Analysis
Trivy Vulnerability Scan
OSV Scanner / osv-scan
```

Server-side **direct pushes to `main` are blocked** (verified: the protection
record exists and `enforce_admins` is on). Server-side **deletion and
force-push of `main` are blocked** (verified above).

---

## 14. What REQUIRES ADMIN ACTION (not fixable in-repo)

1. **Required reviews cannot be enforced server-side.** The fleet operates
   as ONE GitHub account (`@Opselon`), and GitHub forbids author
   self-approval. `CODEOWNERS` notes this explicitly. Enabling
   `required_reviews` needs agent identities separated into distinct
   accounts, then a settings-only change. **REQUIRES ADMIN ACTION.**
2. **`strict: true`** on required checks would force every PR to be rebased
   onto the latest main before merging, but several required workflows are
   path-scoped (DEC-0006), so `strict` would deadlock pure-code PRs.
   Deliberately `false`. **REQUIRES ADMIN ACTION to change, with the
   DEC-0006 always-report fix landed first.**
3. **Rulesets** are none. The classic-protection record in §13 is what
   actually governs. A migration to rulesets is possible but is an owner
   decision, not an agent one.

---

## 15. Agent behavioural protocol

An agent starting work:

1. inspect current state (`git status`, `git branch --show-current`,
   `git log -5 --oneline`)
2. run the preflight: `python scripts/git/preflight.py`
3. `git fetch origin`
4. verify `origin/main`
5. create a task branch from `origin/main` — never from local main
6. work only on the owned branch
7. validate (ruff → format → mypy → critical suite)
8. `git push -u origin <branch>`
9. open / update the PR
10. wait for required CI and the integration gate
11. merge only with the coordinator's go-ahead (DEC-0008)

An agent MUST STOP when:

- main is diverged
- ownership is unclear
- another agent's work may be affected
- the working tree contains unknown changes
- local/remote history cannot be reconciled confidently
- merge equivalence is uncertain
- a destructive operation appears necessary

---

## 16. Supervisor rules

A supervisor/coordinator must not optimize for "clean Git state" at the
expense of preserving developer/agent work. Specifically it must not:

- develop on main directly
- `git pull` blindly (fetch, then compare)
- sync destructively
- interfere with foreign worktrees
- skip the preflight before a push
- merge without the required checks and the explicit approval gate
- guess at squash equivalence — it compares tree/patch
- skip recovery refs before a risky synchronization

---

## 17. Security / credential rules

- No token, password, or PAT may be committed or embedded in repository
  documentation or scripts.
- Secrets are never printed in logs. The preflight strips any userinfo from
  remote URLs before reporting them.
- If a remote URL carries an embedded credential, treat it as a security
  issue: do not expose the secret, do not commit it anywhere, document the
  secure remediation (store the credential in the environment or the OS
  credential store, not the URL). Do not automatically revoke credentials
  unless explicitly authorized.
- `scripts/ci/scan_secrets.py` remains the in-repo secret scanner.

---

## 18. Release / deployment rules

- Unreviewed branches cannot silently become production: releases are
  tag-triggered (`v*`) and every gate is fail-closed.
- Release branches have explicit ownership (release lane in `CODEOWNERS`).
- Production deployment gates stay intact; this governance change does not
  alter release behaviour.
- Deploy workflows do not bypass branch protection — they fire on tags, not
  on branch pushes.
- CI status is authoritative for required checks.

---

## 19. Superseded patterns

The following older patterns are obsolete and replaced:

| Obsolete | Replacement |
|---|---|
| `git checkout main && git pull` | `git fetch origin && git switch main && git merge --ff-only origin/main` |
| bare `git pull` | `git fetch origin` + explicit comparison |
| "branch main-based workflow" (`agents/skill.md` §10 item 5) | task-branch workflow from `origin/main` (§3) |
| `reset --hard origin/main` as routine hygiene | only inside the `MAIN_DIVERGED` recovery, with a verified backup and human authorization |
| deleting a branch after any operation | squash-merge auto-deletes the *task* branch; coordinator-owned refs (`recovery/*`, `archive/*`) are never agent-deleted (DEC-0008b) |
| `git stash` as a cleanup step | commit to the task branch; stashing foreign changes is prohibited |
| rebase of a shared branch | merge `origin/main` INTO the PR branch (`--no-ff`), never rebase shared history |

Historical decisions that remain valuable context are not deleted — they are
superseded explicitly here and in `agents/decisions/`.

---

## 20. Enforcement summary

| Rule | Mechanism | Status |
|---|---|---|
| §3 no development on main | `preflight.py` `check_not_main` | ENFORCED IN CODE |
| §3 branch from origin/main | `preflight.py` `check_task_branch_freshness` | ENFORCED IN CODE (warning) |
| §4 local main is a mirror | `preflight.py` `check_main_mirror` (`STALE_MAIN` / `MAIN_AHEAD`) | ENFORCED IN CODE |
| §4 `--ff-only` only | documented procedure + `MAIN_DIVERGED` recovery | DOCUMENTED ONLY (human step) |
| §5 task-branch naming | `preflight.py` `check_branch_name` | ENFORCED IN CODE |
| §6 PR contract | PR template + §13 required contexts | ENFORCED BY GITHUB (contexts); template is DOCUMENTED ONLY |
| §7 squash equivalence | `TestSquashEquivalence` | ENFORCED IN CODE (logic pinned by test) |
| §8 one worktree per branch | `preflight.py` `check_worktree` | ENFORCED IN CODE |
| §8 untracked preserved | `check_dirty_tree` is a warning, never a blocker | ENFORCED IN CODE |
| §9 no destructive git | `scan_text_for_banned_commands` + `TestDestructiveCommandScan` | ENFORCED IN CODE (scanner); agents themselves are DOCUMENTED ONLY |
| §10 no push to main | `preflight.py --for-push` | ENFORCED IN CODE |
| §10 no direct push to main (server) | branch protection | ENFORCED BY GITHUB |
| §10 no force-push / deletion of main | `allow_force_pushes: false` + protection | ENFORCED BY GITHUB |
| §13 11 required contexts | branch protection `required_status_checks` | ENFORCED BY GITHUB |
| §13 required reviews | — | NOT VERIFIED / REQUIRES ADMIN ACTION (§14) |
| §13 `strict` up-to-date | — | VERIFIED `false` — REQUIRES ADMIN ACTION (§14) |

---

## 21. Authority and changes

This document governs git mechanics. Changing it is a governance change:
open a PR from an `agent/gov/` branch, run the preflight and the governance
tests, and record the reasoning. A change that weakens an ENFORCED rule must
say so out loud in its PR body.
