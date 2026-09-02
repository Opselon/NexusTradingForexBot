# MASTER MULTI-AGENT CONTRACT — Nexus Scalp Engine (NSE)

> **This file is the authoritative in-repo copy of the MASTER MULTI-AGENT
> ENGINEERING / GIT / GITHUB / ARCHITECTURE COLLABORATION CONTRACT (v2, 61 sections).**
> User-mandated, ALWAYS-ON for every agent working on this repository.
> The full text lives in the Hermes skill reference
> `nexus-scalp-engine-dev/references/multi-agent-git-contract.md` (identical).
> If this file and the skill reference ever diverge, this file is the
> in-repo source of truth for agents; reconcile the skill copy after.

## Purpose

This repository is intentionally developed by multiple AI agents
(potentially 56+) working sequentially and/or in parallel. The repository
is CODEBASE + ARCHITECTURE MEMORY + BUG MEMORY + CONTRACT REGISTRY +
RUNTIME INVARIANTS + CHANGE CONTROL + GIT HISTORY + AGENT HANDOFF SYSTEM.
Git is NOT merely a publishing mechanism; GitHub is NOT merely a remote.
They are part of the system's engineering memory. Every agent MUST leave
the repository in a state that another agent can safely continue from
without depending on the previous chat.

## Mandatory bootstrap (before ANY code change)

1. `git status --short`, `git branch --show-current`, `git log -10 --oneline`, `git diff`, `git diff --cached`
2. Read: `agents/skill.md`, `agents/bugs.md`, `agents/contracts.md`,
   `agents/runtime_invariants.md`, `agents/change_control.md`,
   `agents/taskboard.md`, `agents/repository_state.md`, THIS file.
3. Inspect branch, recent relevant commits, active/uncommitted work,
   ownership conflicts (agents/locks.yaml), task dependencies.
4. Determine what is implemented / being worked on / which contracts are
   relevant / which invariants must not be broken.

NEVER start coding before this bootstrap.

## Golden rules (full list in the master text)

1. NO IMPORTANT CHANGE WITHOUT CONTEXT.
2. NO SHARED FUNCTION CHANGE WITHOUT HISTORY REVIEW.
3. NO SEMANTIC CHANGE WITHOUT DOCUMENTATION.
4. NO CONTRACT CHANGE WITHOUT CONTRACT UPDATE.
5. NO RUNTIME INVARIANT CHANGE WITHOUT EXPLICIT REVIEW.
6. NO BUG FIX WITHOUT A REPRODUCER WHEN PRACTICAL.
7. NO FIX WITHOUT REGRESSION TEST.
8. NO BLIND MERGE.
9. NO DESTRUCTIVE GIT OPERATION ON UNKNOWN WORK.
10. NO FAKE GREEN.
11. NO CHAT-ONLY KNOWLEDGE.
12. NO DUPLICATE ARCHITECTURE WITHOUT EVIDENCE.
13. NO UNEXPLAINED DATABASE CHANGE.
14. NO UNEXPLAINED FEATURE/MODEL CONTRACT CHANGE.
15. GIT HISTORY + SKILL + BUGS + CONTRACTS + INVARIANTS + HANDOFFS
    TOGETHER FORM THE PROJECT'S LONG-TERM ENGINEERING MEMORY.

## Key sections quick-reference

- §0 Bootstrap (above) · §1 Working tree ownership (preserve unknown work) ·
  §2-3 Agent identity & role ownership (Hermes-Runtime/Execution/Risk/MT5/
  Learning/Research/News/Accounting/Model/UI/Release; CROSS-OWNER CHANGE tag) ·
  §4 change_control.md (CHANGE-ID lifecycle) · §5 taskboard.md (TASK-ID) ·
  §6 repository_state.md · §7 contracts.md · §8 runtime_invariants.md
  (INV-001..012) · §9 docs/architecture/dependency-map.md · §10 DB ownership
  map · §11-12 git forensics & shared-function protocol · §13 SHARED API
  CHANGED · §14 SEMANTIC CHANGE · §15 REUSE > EXTEND > REFACTOR > CREATE ·
  §16 agent/<name>/<task> branches · §17 locks.yaml · §18 commit contract
  (<AGENT>: summary + structured body) · §19-21 commit size / self-audit /
  tests travel with fix · §22 forensic fix workflow (BUG→REPRODUCER→ROOT
  CAUSE→REGRESSION TEST→FIX→RE-RUN→FULL TESTS→RUNTIME VALIDATION→DOCS→COMMIT) ·
  §23 tests/golden/ · §24 IMPACT CHECK · §25 cross-subsystem boundaries ·
  §26 model/feature protocol · §27 DB protocol · §28 runtime protocol ·
  §29 BEFORE/AFTER/DELTA measurements · §30-31 no silent green / runtime
  claims (CODE/TEST/INTEGRATION/LIVE MT5/PACKAGED RELEASE VERIFIED) ·
  §32-36 merge/overlap/conflict/cherry-pick/rebase rules · §37-38 handoff
  docs (docs/agent_handoffs/YYYY-MM-DD_<agent>_<task>.md) & function-level
  detail · §39 decisions/ (DEC-XXXX) · §40-41 skill.md additive, bugs.md
  forensic · §42-43 PRs forensic + CI checks · §44 release/tag protection ·
  §45 no destructive cleanup · §46-47 reproducibility & trustworthy history ·
  §48-51 Builder/Reviewer/Gatekeeper multi-agent review · §52 mandatory
  handoff block · §53 next-agent bootstrap · §54 no chat-only knowledge ·
  §55 quality gates (focused→subsystem→full unit→integration→ruff→format→
  mypy→beforePush.sh→beforePush.ps1) · §56 TESTED/OBSERVED/INFERRED/NOT
  TESTED · §57 performance/scale safety · §58 final discipline checklist ·
  §59 master flow · §60 golden rules · §61 final objective.

## Full text

The complete 61-section contract text (with the §18 commit example, the
§52 AGENT HANDOFF template, and §8 invariant examples) is in the Hermes
skill reference `nexus-scalp-engine-dev/references/multi-agent-git-contract.md`.
Agents WITHOUT Hermes access should ask the project owner for the full text;
the condensed rules above plus the sibling registry files (contracts.md,
runtime_invariants.md, change_control.md, taskboard.md, repository_state.md,
locks.yaml, decisions/) cover the enforceable workflow.
