# 🧭 Legacy Agent Documentation Directory (`Agent/`)

> **NOTICE TO AGENTS AND ENGINEERS:**
>
> **MIGRATED 2026-09-04:** legacy files moved to `agents/legacy/`. This directory is now a compatibility shim (Agent/README.md only).
>
> This directory (`Agent/`, capital A) previously contained historical, companion architecture documents from the 2026-08-20..22 multi-agent development period.
>
> The **CANONICAL, ACTIVE** agent registry for this repository is [`agents/`](../agents/) (lowercase `agents/`).

---

## Directory Resolution & Mapping

| File in `agents/legacy/` (moved from `Agent/`) | Role | Canonical / Active Counterpart | Status |
|---|---|---|---|
| `agents/legacy/skill.md` | Legacy concise entrypoint & path contract (§0) | [`agents/skill.md`](../agents/skill.md) (authoritative master map, §1–§20) | **HISTORICAL COMPANION** |
| `agents/legacy/PROJECT_GRAPH.md` | Deep intelligence map & data paths | Referenced by `agents/legacy/skill.md`; maps system components | **REFERENCE** |
| `agents/legacy/ARCHITECTURE_CONTRACT.md` | System laws & invariants | [`agents/contracts.md`](../agents/contracts.md) & [`agents/runtime_invariants.md`](../agents/runtime_invariants.md) | **REFERENCE** |
| `agents/legacy/AGENT_REASONING_PROTOCOL.md` | Operating manual for autonomous agents | [`agents/multi-agent-git-contract.md`](../agents/multi-agent-git-contract.md) | **REFERENCE** |
| `agents/legacy/DATABASE_MIGRATION_STATUS.md` | Snapshot of database migrations (Aug 2026) | [`docs/DATABASE_MIGRATIONS.md`](../docs/DATABASE_MIGRATIONS.md) | **HISTORICAL** |
| `agents/legacy/TEST_OPTIMIZATION_REPORT.md` | Snapshot of test suite optimization | [`tests/README-TEST-SUITE-REDUCTION.md`](../tests/README-TEST-SUITE-REDUCTION.md) | **HISTORICAL** |

---

## Invariants

1. **Do not delete `Agent/`** — several scripts and internal docs maintain provenance links here.
2. **Do not overwrite `Agent/` with `agents/`** — on case-insensitive filesystems (Windows/NTFS, macOS default), git tracks both separately. Merging them by simple copy creates case-collision churn.
3. **Always write new bug reports, contracts, and taskboard updates to [`agents/`](../agents/)** (`bugs.md`, `taskboard.md`, `change_control.md`, `locks.yaml`).
