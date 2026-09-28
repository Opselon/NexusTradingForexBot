# Legacy Agent Documentation Directory (`Agent/`)

> **RETIRED 2026-09-28.** The root-level `Agent/` (capital `A`) directory was a
> compatibility shim left over from the 2026-09-04 migration. It is now removed.
> This document preserves its mapping table as the historical record.

## Origin

`Agent/` (capital `A`) previously held historical, companion architecture
documents from the 2026-08-20..22 multi-agent development period. On
2026-09-04 those files were moved into the canonical machine-readable registry
[`agents/`](../../agents/) (lowercase) under `agents/legacy/`, and the capital-A
directory was reduced to a single pointer README.

## Why the directory is gone

1. Nothing in `src/`, `tests/`, `scripts/`, or `.github/` referenced `Agent/` —
   only documentation links did.
2. Two same-named directories differing only by case (`Agent/` vs `agents/`) are
   a hazard on case-insensitive filesystems (Windows/NTFS, macOS default): git
   tracks them separately, but the filesystem collides them, which churns
   checkouts and breaks tooling.
3. The shim pointed at `agents/legacy/README.md`, which does not exist — the
   pointer was already stale, so the shim served no navigational purpose.

The warning in the original shim against merging `Agent/` into `agents/` by
simple copy remains valid as a general rule; this removal avoided that path
entirely (the folder held no content of its own to merge).

## Legacy file map (moved 2026-09-04, preserved for provenance)

| File in `agents/legacy/` (moved from `Agent/`) | Role | Canonical / Active Counterpart | Status |
|---|---|---|---|
| `agents/legacy/skill.md` | Legacy concise entrypoint & path contract (§0) | [`agents/skill.md`](../../agents/skill.md) (authoritative master map, §1–§20) | **HISTORICAL COMPANION** |
| `agents/legacy/PROJECT_GRAPH.md` | Deep intelligence map & data paths | Referenced by `agents/legacy/skill.md`; maps system components | **REFERENCE** |
| `agents/legacy/ARCHITECTURE_CONTRACT.md` | System laws & invariants | [`agents/contracts.md`](../../agents/contracts.md) & [`agents/runtime_invariants.md`](../../agents/runtime_invariants.md) | **REFERENCE** |
| `agents/legacy/AGENT_REASONING_PROTOCOL.md` | Operating manual for autonomous agents | [`agents/multi-agent-git-contract.md`](../../agents/multi-agent-git-contract.md) | **REFERENCE** |
| `agents/legacy/DATABASE_MIGRATION_STATUS.md` | Snapshot of database migrations (Aug 2026) | [`docs/architecture/database/DATABASE_MIGRATIONS.md`](../architecture/database/DATABASE_MIGRATIONS.md) | **HISTORICAL** |
| `agents/legacy/TEST_OPTIMIZATION_REPORT.md` | Snapshot of test suite optimization | [`tests/README-TEST-SUITE-REDUCTION.md`](../../tests/README-TEST-SUITE-REDUCTION.md) | **HISTORICAL** |

## Current rules

1. **Do not delete [`agents/`](../../agents/)** (lowercase) — it is the canonical
   machine-readable multi-agent registry, read and written by CI.
2. **Do not recreate a capital-A `Agent/` directory** at the repository root.
3. **Always write new bug reports, contracts, and taskboard updates to
   [`agents/`](../../agents/)** (`bugs.md`, `taskboard.md`, `change_control.md`,
   `locks.yaml`).
