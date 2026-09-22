# AGENT HANDOFF — Git/GitHub Governance Overhaul (GOV-GIT-001)

**Branch:** `agent/gov/git-governance-overhaul`
**Task:** Repository-wide Git/GitHub governance update (operator-directed)
**Date:** 2026-09-22

## Context

The PR queue had just been cleared (#347, #348, #349, #350, #351 merged).
The operator then required a real repository-wide governance update, not a
documentation rewrite, eliminating the class of failures seen in this
session: local main diverging from `origin/main`, agents working from a
stale local main, conflicting concurrent work, duplicate history from squash
merges, unsafe synchronization, and inconsistent git instructions.

## Audit performed (not a doc skim)

- Read the existing governance surface: `agents/multi-agent-git-contract.md`
  (61-section master contract), `agents/git-release-guardian.md` (364 lines),
  `agents/change_control.md`, `agents/skill.md`, `agents/locks.yaml`,
  `agents/decisions/DEC-0006`, `agents/decisions/DEC-0008`,
  `docs/engineering/workflow-test-build-release.md`,
  `docs/guides/common-workflows.md`, `.github/CODEOWNERS`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/ci.yml`,
  `scripts/ci/check_workflows.py`, `scripts/ci/check_local.py`.
- Searched the whole tree for git-policy keywords: 560 matches in 175 files
  (agents 96, docs 292, scripts 11, .github 4, tests 17, src 4).
- Verified the ACTUAL GitHub-side configuration via API — not what docs claim
  (see `agents/git_governance.md` §13 for the verified record).

## What was actually broken

The repository had rich governance PROSE but no operational rule a machine
could check. Specifically:

1. **No preflight guard existed at all.** Nothing stopped an agent from
   starting work on `main` or branching from a stale local main — the exact
   cause of this session's divergence.
2. **Zero git governance tests.** Only *model* governance tests existed.
3. **No canonical local-main sync rule** — no `--ff-only` + `MAIN_DIVERGED`
   procedure anywhere in the repository.
4. **`agents/skill.md` §10 item 5 was actively misleading**: "Branch
   `main`-based workflow" — implies developing on main.
5. **Unsafe patterns in docs**: `git checkout main && git pull`
   (`docs/engineering/workflow-test-build-release.md:179`), bare `git pull`
   (`docs/guides/common-workflows.md:12`).
6. **No squash-equivalence detection tooling**, no worktree-ownership check.

## Changes

### New enforcement code

| File | Role |
|---|---|
| `scripts/git/preflight.py` | Read-only governance preflight. Detects: on-main, MAIN_AHEAD, STALE_MAIN, bad branch prefix, double worktree checkout, push-to-main. Never mutates repo state. |
| `scripts/ci/check_git_governance.py` | CI lane: proves the preflight imports, scans `.github/` + `scripts/` for destructive-git INSTRUCTIONS, verifies the governance doc covers every implemented section. Wired into `ci.yml :: ci-integrity`. |
| `tests/unit/test_git_governance.py` | 33 hermetic tests (temp bare origin + clone; never touches the real repo). Registered in `tests/critical_suite.txt` (232 → 233). |

### New / updated governance docs

| File | Change |
|---|---|
| `agents/git_governance.md` | NEW — canonical model, 21 sections. Verified-vs-documented status for every GitHub-side rule. |
| `agents/skill.md` | §10 item 5 rewritten: task-branch workflow from `origin/main`, never from local main. |
| `agents/multi-agent-git-contract.md` | Defers to `git_governance.md` on git mechanics. |
| `docs/engineering/workflow-test-build-release.md` | Replaced `git checkout main && git pull` with the `--ff-only` mirror sync; added the git-mechanics authority note. |
| `docs/guides/common-workflows.md` | Replaced bare `git pull`. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Added the general PR contract checklist (identity, branch origin, preflight, evidence, approval gate). |
| `.github/workflows/ci.yml` | New `Git governance scan` step in `ci-integrity`. |
| `tests/critical_suite.txt` | + `tests/unit/test_git_governance.py`. |

## Validation evidence

```text
$ python3 -m pytest tests/unit/test_git_governance.py -q
33 passed, 1 skipped in 8.84s

$ python3 scripts/ci/check_git_governance.py
GIT GOVERNANCE OK — preflight importable, no destructive git in automation,
governance doc covers all implemented sections.  (rc=0)

$ python3 scripts/git/preflight.py --cwd /tmp/ntfx --for-push
PREFLIGHT OK — 1 warning(s), 0 blocker(s)

$ python3 scripts/ci/check_workflows.py --format text --strict
SUMMARY: 0 ERROR, 0 WARNING, 0 INFO  (rc=0)

$ python3 -m ruff check scripts/git/ scripts/ci/check_git_governance.py tests/unit/test_git_governance.py
All checks passed!

$ python3 -m ruff format --check (3 files)
3 files already formatted

$ python3 -m mypy scripts/git/preflight.py scripts/ci/check_git_governance.py --ignore-missing-imports
Success: no issues found in 2 source files

$ python3 scripts/ci/gate_parity.py --json
"drifts": 0

$ python3 scripts/ci/verify_critical_suite_manifest.py
CRITICAL_SUITE_MANIFEST_OK: 233 paths all exist
```

The `mypy` note about unused `module = [...]` sections in `pyproject.toml`
is pre-existing and unrelated.

## Enforcement matrix

See `agents/git_governance.md` §20. Summary: §3, §4 (both directions), §5,
§8, §10 are ENFORCED IN CODE; §13 contexts/no-force-push/no-direct-push are
ENFORCED BY GITHUB; §6 contexts are GitHub-enforced with a documented-only
template; §9 is enforced by scanner; required reviews and `strict: true`
are NOT VERIFIED and REQUIRE ADMIN ACTION (§14).

## What still requires human action (not fixable in-repo)

1. **Required reviews cannot be enforced server-side** — the fleet operates
   as one GitHub account; GitHub forbids author self-approval. Needs agent
   identities in distinct accounts. `CODEOWNERS` already documents this.
2. **`strict: true`** would deadlock path-scoped required workflows
   (DEC-0006). Deliberately `false`; changing it needs the always-report
   fix landed first and an admin decision.
3. **No repository rulesets** exist (`GET /rulesets` → `[]`); classic
   branch protection is what actually governs.

## Risks / notes

- The preflight is advisory on the developer machine (no git hook is
  installed; `beforePush.sh` integration is a possible follow-up). CI
  enforcement is live via the new `ci-integrity` step.
- The destructive-git scanner is string-based. It distinguishes
  prohibition from instruction via fence/table/comment context, which is
  deliberately conservative — it flags its own guard's ban-list if the
  exemption is removed, which the tests pin.
- No production/execution code was modified. Release behaviour unchanged.
