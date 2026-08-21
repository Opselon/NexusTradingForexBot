# DEC-0003 — Hermes Kanban Swarm Integration

**Date:** 2026-08-22
**Status:** ACCEPTED
**Scope:** Repository engineering workflow / multi-agent orchestration
**Agent:** Hermes-LeadArchitect
**Note on numbering:** Renumbered from the upstream `DEC-0002` to avoid a collision with
`DEC-0002-nodejs-runtime-role.md` (already present on `main`). Decision identity is
`DEC-0003`; the upstream swarm branch carries the same content under `DEC-0002`.

## Decision

Adopt **Hermes Kanban Swarm / multi-agent orchestration** as an approved way to parallelize
NexusTradingForexBot engineering, while keeping the repository's existing multi-agent
Git/forensic contract authoritative.

The swarm is an orchestration mechanism. It does not replace `agents/taskboard.md`,
`agents/locks.yaml`, contracts, runtime invariants, handoffs, Git history, or verification
gates.

## Rationale

Nexus already has explicit ownership, task, lock, contract, invariant, and handoff mechanisms.
Introducing a second independent coordination state would create conflicting sources of truth.

Therefore:

```text
Hermes Swarm
     |
     v
Task decomposition / worker execution
     |
     v
Nexus durable project memory
     |
     +--> taskboard
     +--> locks
     +--> contracts
     +--> invariants
     +--> handoffs
     +--> Git history
     +--> verification
```

## Important boundary

The project does **not** assume that Hermes provides Slack/Discord-style peer-to-peer live chat
between independent sessions. Any ephemeral agent-to-agent messaging supplied by a particular
Hermes release is supplemental. Durable conclusions must be recorded in repository artifacts.

## Workspace policy

For independent write-heavy workers, isolated Git worktrees are preferred when supported by the
installed Hermes runtime. A shared workspace is allowed only for explicitly non-overlapping
tasks with ownership control.

Unknown WIP remains foreign until ownership is established.

## Safety

Swarm agents must not bypass live-trading, risk, MT5, model-governance, database,
forensic-integrity, or security contracts. No agent may declare a partial test run to be a
global green state.

## Consequence

The detailed operational contract lives in:

`agents/hermes-kanban-swarm.md`

`agents/skill.md` remains the master entry point and receives a short dated reference entry
when this integration is promoted to the default workflow.
