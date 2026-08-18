# TASK-11 Handoff — Database Hygiene Worker / Retention / Legacy Pruning

- **Agent**: Hermes-DBHygiene (Hermes Agent)
- **Role**: Database Hygiene / Retention / Legacy Data Cleanup Engineer
- **Task**: TASK-11 (spec delivered 2026-08-18)
- **Starting HEAD**: `5512d40` (HERMES-TASK1: dependabot dependency bound updates)
- **Ending HEAD**: (see git log — commit applied after this doc)

## Executive summary

Built a highly conservative `DatabaseHygieneWorker` that continuously keeps
all NSE databases bounded and consistent while **never deleting history**.
Pipeline: OBSERVE → CLASSIFY → PLAN → VALIDATE → CLEAN → VERIFY. Default
mode is AUDIT_ONLY (zero mutation on debut); SAFE_CLEAN is an explicit
operator opt-in; AGGRESSIVE_CLEAN requires separate activation. Every
destructive action is archive-before-delete (sha256-verified), journaled,
budget-bounded (global), and followed by integrity + financial-aggregate
verification. When the worker is not 100% certain a row is safe, it keeps it.

## Inventory (read-only, 2026-08-18)

| DB | Size | Tables | Notes |
| :--- | ---: | ---: | :--- |
| audit.db | 50.9 MB | 35 | ledger 266, signals 15,142, lifecycle 11,875, broker_deals 7,516 |
| news.db | 6.4 MB | 16 | articles 1,677 (hash UNIQUE), analysis 877 |
| candle_intel.db | 1.0 MB + 4.2 MB WAL | 13 | derived mirrors, rebuildable |

Per-table TIER classification (TIER-0..8), retention, owner, rebuildability,
delete safety: `docs/DATABASE_HYGIENE_MATRIX.md`.

## Files changed (TASK-11 scope)

| File | Change |
| :--- | :--- |
| `src/nexus_scalp/hygiene/__init__.py` | DataTier / Confidence / WorkerMode / WorkerState / OrphanClass enums |
| `src/nexus_scalp/hygiene/retention.py` | RetentionEngine + per-table policy registry (audit/news/candle) |
| `src/nexus_scalp/hygiene/detectors.py` | DuplicateDetector (canonical identities, split-fill PROTECTED) + OrphanDetector |
| `src/nexus_scalp/hygiene/archive.py` | ArchiveManager (checksummed JSONL + verify) + CleanupJournal |
| `src/nexus_scalp/hygiene/worker.py` | HygieneScanner/Planner, CleanupExecutor (bounded, journaled), VerificationEngine, SAFE_RETENTION_DELETES, budgets |
| `src/nexus_scalp/hygiene/state.py` | HygieneStateStore (state + run history + crash recovery) |
| `src/nexus_scalp/hygiene/worker_runner.py` | DatabaseHygieneWorker orchestrator + db_integrity_digest |
| `src/nexus_scalp/cli/db_commands.py` | `nexus db hygiene status/plan/run/pause/resume/history` (--json) |
| `src/nexus_scalp/cli/main.py` | registers hygiene typer |
| `src/nexus_scalp/web/server.py` | `GET /api/db/hygiene` (real data) |
| `src/nexus_scalp/application/live_engine.py` | 6h hygiene cycle via asyncio.to_thread (AUDIT_ONLY first run, LIVE-safe) |
| `tests/unit/test_database_hygiene_task11.py` | 37 tests (TEST-HYG-01..36 + real-DB copy) |
| `docs/DATABASE_HYGIENE_MATRIX.md`, `docs/DATABASE_HYGIENE.md` | classification + policy |
| `docs/agent_handoffs/TASK-11-database-hygiene.md` | this handoff |
| `agents/*` registries | BUG-099, INV-017, CHG-0006, contracts DATABASE_HYGIENE/RETENTION_POLICY v1, skill §15l, taskboard, repository_state |

## Contracts

- DATABASE_HYGIENE v1 (new) — worker behavior contract.
- RETENTION_POLICY v1 (new) — per-table retention rules.
- New invariant INV-017 — hygiene is non-destructive by default.

## Verified behavior (test + real-copy evidence)

- AUDIT_ONLY / DRY_RUN: zero mutation (integrity digest unchanged).
- Exact duplicates (news article flagged duplicate + canonical present)
  archived + deleted; ambiguous flagged (no canonical) KEPT.
- Split-fill family (3 ledger ticks, one order_id): PROTECTED, never a
  duplicate candidate.
- Financial rows (ledger/experiences/outcomes/broker_trades/model registry/
  schema_meta): never auto-deleted (AGGRESSIVE_CLEAN also preserves).
- Bounded cleanup: signals/guard/MOVING/worker-state/candle-derived deleted
  only within retention windows; global budget (max_deleted etc.) enforced.
- Archive checksum verify: corrupt file → verify False.
- Journal written for every destructive action.
- Financial aggregates (ledger_rows, pnl_sum, broker_trades, experiences,
  outcomes) unchanged after cleanup (test_hyg15).
- Busy DB → BUSY_DEFERRED (never forced delete).
- Crash recovery: IN_PROGRESS → INTERRUPTED; no blind resume; idempotent.
- Real production DBs COPIED to tmp and planned (audit/news/candle) with
  zero mutation (test_hyg_real_db_copy_plan_only).
- Live audit.db plan: 0 exact duplicates, 3,372 EXPECTED orphans (broker
  trades without ledger — pre-BUG-045 migration-era, preserved, never
  deleted), 0 retention candidates today (daily purge keeps windows clean).

## Quality gates

- ruff check: clean on all TASK-11 files.
- ruff format: applied (hygiene/, db_commands, test file).
- mypy: Success on hygiene/ (7 files) + db_commands.py.
- pytest tests/unit/test_database_hygiene_task11.py: 37/37 green.

## Bugs found / fixed

- BUG-099 (no retention governance outside audit.db; no hygiene pipeline) —
  FIXED. No cleanup-candidate-bug entries fabricated (spec §69).

## Remaining limitations / coordination

- TASK-10's `nexus db status|plan|migrate|verify` CLI and migration engine
  are parallel WIP; hygiene reads their `schema_meta`-style tables as KEEP
  (unknown → keep) and never performs destructive schema operations.
- VACUUM is policy-documented but NOT auto-executed (maintenance-window +
  non-LIVE + WAL checkpoint verification required first) — future operator
  action or TASK-12.
- Telegram reporting of hygiene runs is wired conceptually (run history +
  `verification` field) but not yet a scheduled message — the operator can
  read it via `nexus db hygiene history`; a Telegram digest can be added by
  the next task as a small notifier consumer of `hygiene_run_history`.
- Growth-rate telemetry (bytes freed / growth-per-day) is computed and
  stored in run history; the dashboard section is API-ready.

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-12)

1. Production reliability / self-healing (TASK-8 follow-ups): consume
   `hygiene_run_history` for the DB health dashboard + optional Telegram
   digest of hygiene runs (`verification` must reflect the actual run).
2. Re-evaluate VACUUM/compaction automation strictly per spec §36: WAL mode
   + disk headroom + lock impact + non-LIVE window + checkpoint + verify.
3. Add growth-rate metrics surfaced from run history (before/after/bytes).
4. If TASK-10's migration engine lands schema_meta in production DBs,
   re-scan the hygiene matrix for TIER-8 legacy artifacts and route any
   removal through TASK-10 migrations (never ad-hoc DROP).
5. Coordinate with TASK-1/2/4 consumers when archived rows are referenced:
   archival only happens for approved duplicate/stale classes, so research/
   accounting lineage is untouched — verified by tests.