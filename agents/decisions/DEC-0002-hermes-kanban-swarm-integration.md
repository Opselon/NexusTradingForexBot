# RETIRED DUPLICATE — DEC-0002 (see DEC-0003)

**Status:** RETIRED — DUPLICATE IDENTITY

## Why this file is empty

`DEC-0002` in this repository canonically refers to:

> **DEC-0002 — Node.js Runtime Role in Nexus Scalp Engine**
> (`agents/decisions/DEC-0002-nodejs-runtime-role.md`)

This file previously carried a full copy of the **Hermes Kanban Swarm
Integration** decision under the same `DEC-0002` identifier — a numbering
collision (two different subjects, one ID). The kanban-swarm decision's
canonical, renumbered home is:

> **DEC-0003 — Hermes Kanban Swarm Integration**
> (`agents/decisions/DEC-0003-hermes-kanban-swarm-integration.md`)
> (renumbered from the upstream `DEC-0002` precisely to avoid this collision;
> see the renumbering note inside DEC-0003.)

The full content was removed from this duplicate on 2026-09-07 by the
decision-record integrity pass (P3). One decision ID maps to exactly ONE
canonical record. Enforcement: `scripts/ci/check_decision_ids.py`
(runs in the local pre-push gate and CI static lane).

Inbound references:
- `agents/repository_state.md` (Snapshot 2026-08-22) → points at DEC-0003.
- `agents/forensic_reports/2026-09-02_repository_hygiene.md` cites
  "DEC-0002 keeps blanket ignore out" — that citation is about the Node.js
  runtime decision (node_modules handling) and resolves correctly to
  `DEC-0002-nodejs-runtime-role.md`.
