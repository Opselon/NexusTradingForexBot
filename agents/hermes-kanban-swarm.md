# Hermes Kanban Swarm Integration Contract

> **Purpose:** Define how the NexusTradingForexBot repository is operated by Hermes Kanban Swarm / multi-agent orchestration without weakening the repository's existing forensic, Git, runtime, or ownership contracts.
>
> **Status:** 🟢 PROJECT INTEGRATION CONTRACT — the repository-side rules below are authoritative for Nexus. Hermes runtime behavior must be treated as an external capability and must never be assumed to provide guarantees that are not verified in the installed Hermes version.

## 1. Core objective

Nexus may be developed by multiple Hermes agents in parallel. The swarm is an **orchestration layer**, not a replacement for the repository's engineering memory.

The project remains governed by:

1. `agents/skill.md`
2. `agents/multi-agent-git-contract.md`
3. `agents/contracts.md`
4. `agents/runtime_invariants.md`
5. `agents/change_control.md`
6. `agents/taskboard.md`
7. `agents/repository_state.md`
8. `agents/locks.yaml`
9. `agents/bugs.md`
10. `agents/decisions/`
11. `docs/agent_handoffs/`

A swarm agent MUST NOT treat its private chat history as the project's source of truth.

## 2. Swarm topology

The recommended Nexus topology is:

```text
                         LEAD / ORCHESTRATOR
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
      ARCHITECT                BACKEND                 FRONTEND
          |                       |                       |
          +-----------------------+-----------------------+
                                  |
                       ML / RESEARCH / ACCOUNTING
                                  |
                         REVIEWER / GATEKEEPER
                                  |
                                  v
                         TEST + INTEGRATION
```

Recommended roles:

| Role | Responsibility | Write authority |
|---|---|---|
| `lead` | Decompose work, coordinate dependencies, synthesize results | Only coordination/docs unless explicitly assigned |
| `architect` | Architecture, contracts, dependency impact, design decisions | Architecture/docs/decision files |
| `backend` | FastAPI, application services, persistence, APIs | Assigned backend paths only |
| `frontend` | `Web/` UI, JS/CSS, API integration | Assigned frontend paths only |
| `ml` | Features, models, training, validation | Assigned ML paths only |
| `accounting` | Accounting/reconciliation/ledger truth | Assigned accounting paths only |
| `research` | Research/backtest/data validation | Assigned research paths only |
| `reviewer` | Code review, contract review, regression analysis | Read-first; fixes only when explicitly assigned |
| `qa` | Focused/subsystem/full verification and CI diagnosis | Tests/verification artifacts when assigned |
| `security` | Secret, dependency, permission, and boundary review | Security/docs/tests when assigned |

## 3. Communication model

Hermes Kanban Swarm should be understood as **task/state/result orchestration**, not as a guaranteed Slack-like peer-to-peer chat bus.

Agents communicate project knowledge through durable repository artifacts:

```text
Agent A
  -> task status / artifact / handoff / commit
  -> shared project state
  -> dependency becomes READY
  -> Agent B consumes verified result
```

If the installed Hermes release provides an explicit agent-to-agent messaging primitive, it may be used for ephemeral coordination, but the important conclusion MUST be written to the repository before the task is considered complete.

**Never rely on an ephemeral agent message for:**

- architecture decisions;
- changed contracts;
- bug root causes;
- runtime invariants;
- API/schema changes;
- model/feature dimension changes;
- database migrations;
- ownership transfer;
- verification claims;
- deployment/release decisions.

Those belong in the repository's durable memory.

## 4. Task lifecycle

Every swarm unit of work MUST map to a repository `TASK-ID`, `BUG-ID`, or `CHANGE-ID` before implementation begins.

Canonical lifecycle:

```text
TODO
  -> IN_PROGRESS
  -> BLOCKED / WAITING_FOR_AGENT (when applicable)
  -> READY_FOR_REVIEW
  -> VERIFIED
  -> MERGED
```

A worker MUST NOT silently create a second solution for an existing task. Before starting, inspect `agents/taskboard.md`, recent Git history, locks, and active WIP.

## 5. Ownership and locks

The swarm MUST respect `agents/locks.yaml`.

Rules:

- An exclusive lock means another agent owns the semantic change.
- Do not modify a locked path merely because the change appears easy.
- Do not remove another agent's lock.
- If a task requires a locked path, report the dependency and coordinate ownership.
- Shared files such as `live_engine.py`, `order_manager.py`, feature schemas, settings, and `Web/` entrypoints require extra impact analysis because changes can cross subsystem boundaries.

Unknown/uncommitted WIP is **foreign work until proven otherwise**.

## 6. Git/worktree discipline

For independent code-writing workers, isolated Git worktrees are preferred when the Hermes runtime supports them safely.

```text
Nexus main
 |
 +-- worker/architect
 +-- worker/backend
 +-- worker/frontend
 +-- worker/ml
 +-- worker/qa
```

A shared working directory may be used only when the tasks are intentionally non-overlapping and ownership is explicit.

Never use destructive commands such as reset/clean/rebase-over-WIP to make a swarm workspace look clean.

Every implementation agent produces a coherent, agent-labelled commit according to the master Git contract.

## 7. Shared project state

The following artifacts are the durable coordination plane:

- `agents/taskboard.md` — task ownership/status/dependencies;
- `agents/locks.yaml` — active exclusive ownership;
- `agents/repository_state.md` — current repository truth;
- `agents/contracts.md` — machine-facing contracts;
- `agents/runtime_invariants.md` — runtime safety invariants;
- `agents/change_control.md` — change lifecycle;
- `agents/bugs.md` — forensic bug ledger;
- `agents/decisions/` — architectural decisions;
- `docs/agent_handoffs/` — detailed handoff between agents;
- Git history — durable implementation history.

A swarm result is incomplete if it exists only in the agent transcript.

## 8. Lead-agent behavior

The lead/orchestrator MUST:

1. Read the master skill and multi-agent contract first.
2. Inspect current WIP before decomposition.
3. Convert the objective into non-overlapping tasks.
4. Assign explicit owners and dependencies.
5. Avoid assigning the same semantic change to two workers.
6. Require evidence from workers rather than accepting verbal success.
7. Route completed work through reviewer/QA gates.
8. Synthesize only after dependency results are verified.
9. Preserve unrelated WIP.
10. Never declare the project green from a partial test result.

The lead MUST distinguish:

```text
DONE = worker says done
VERIFIED = evidence proves done
MERGED = verified change is integrated
```

These states are not interchangeable.

## 9. Worker-agent behavior

Every worker MUST:

```text
BOOTSTRAP
  -> inspect status/history/locks/contracts
  -> understand task scope
  -> implement minimal coherent change
  -> add regression coverage
  -> run focused verification
  -> run broader verification when appropriate
  -> update durable project memory
  -> create handoff
  -> create agent-labelled commit
  -> report exact evidence
```

A worker must report foreign failures separately from failures introduced by its own change.

## 10. Reviewer / Gatekeeper

The reviewer is not another implementation worker by default.

Review order:

1. Scope and ownership
2. Contract compatibility
3. Runtime invariants
4. API/schema compatibility
5. Regression tests
6. Error handling
7. Security/secret boundaries
8. Performance/hot-path impact
9. Git diff and unintended changes
10. Full verification state

The reviewer must reject `fake green` claims such as:

- running only the changed test;
- ignoring known failures without classification;
- claiming runtime validation from static inspection;
- claiming production readiness from unit tests;
- hiding unrelated failures.

## 11. Nexus-specific safety boundaries

The swarm MUST NOT allow autonomous agents to place live trades merely because a development task is active.

Live execution remains governed by the existing execution/risk contracts. Agent work must use paper/sandbox/replay paths for behavioral testing unless the project owner explicitly authorizes live validation.

Agents changing:

- `live_engine.py`;
- `order_manager.py`;
- risk sizing;
- MT5 adapters;
- feature schema dimensions;
- model loading/governance;
- database migrations;
- secrets/settings;

must perform the additional reviews required by the existing invariants and contracts.

## 12. Model/feature contract protection

No swarm worker may independently alter the canonical feature dimension or model contract.

Current repository contracts include protected 50D legacy behavior and newer 60D/70D paths. Any change crossing these boundaries must identify:

- schema ID/version;
- dimension;
- training/live/replay parity;
- scaler compatibility;
- artifact manifest compatibility;
- model loading gate;
- regression/golden coverage.

No padding, truncation, or silent fallback may be introduced merely to make dimensions match.

## 13. Accounting and forensic integrity

Accounting, incident, audit, and forensic records are evidence-bearing data.

Swarm automation MUST NOT:

- silently delete audit evidence;
- rewrite historical ledger facts without provenance;
- suppress a failing reconciliation to make tests pass;
- mark an incident resolved without evidence;
- fabricate broker/runtime observations.

Repairs must preserve the project's forensic classification model.

## 14. Handoff minimum

Every worker handoff must contain:

```text
TASK-ID:
OWNER:
SCOPE:
FILES CHANGED:
FILES NOT CHANGED:
DEPENDENCIES:
ROOT CAUSE / DESIGN:
IMPLEMENTATION:
TESTS RUN:
TEST RESULTS:
FOREIGN FAILURES:
RUNTIME VALIDATION:
RISKS:
FOLLOW-UP:
COMMIT:
```

Use `docs/agent_handoffs/` and follow the existing naming convention.

## 15. Swarm acceptance criteria

A Nexus swarm implementation is accepted only when:

- all workers have explicit ownership;
- no unresolved ownership conflict exists;
- every semantic change maps to a task/change/bug ID;
- foreign WIP is preserved;
- contracts and invariants are updated when required;
- tests travel with fixes;
- reviewer evidence exists;
- verification state is truthful;
- handoffs exist;
- commits are coherent and agent-labelled;
- integration does not silently discard another worker's work.

## 16. Recommended Nexus swarm

For large cross-cutting tasks, use:

```text
Lead
 |
 +-- Architect ---------> decision/contract review
 |
 +-- Backend ------------> API/domain/persistence
 |
 +-- Frontend -----------> Web UI/API integration
 |
 +-- ML ------------------> features/models/training
 |
 +-- Accounting ----------> ledger/report truth
 |
 +-- Research ------------> backtest/OOS/data integrity
 |
 +-- QA ------------------> focused -> subsystem -> full -> CI
 |
 +-- Security ------------> secrets/dependencies/boundaries
 |
 +-----------------------> Reviewer/Gatekeeper
```

Do not activate every role for every task. Spawn the smallest set that can complete the dependency graph safely.

## 17. Hermes version-awareness

This file deliberately does **not** hard-code undocumented Hermes CLI syntax, database paths, or Desktop menu names.

Those are runtime/version facts and must be verified against the installed Hermes release before being used as operational instructions.

The repository contract is therefore stable across Hermes upgrades while the actual orchestration command/UI may evolve.

## 18. Skill integration rule

`agents/skill.md` remains the master agent entry point. This document is the detailed Nexus-specific Swarm integration contract referenced from that skill.

Whenever the swarm architecture changes, add a short dated entry to `agents/skill.md` and update this document with the durable details.

**Initial integration:** 2026-08-22 — Hermes Kanban Swarm adopted as an orchestration pattern; repository contracts remain authoritative over task state, ownership, Git, runtime safety, forensic integrity, and verification.
