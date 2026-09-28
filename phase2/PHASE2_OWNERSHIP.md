# PHASE2_OWNERSHIP.md — Agent 2 (Database / Contract Integrity)

Agent: AGENT-2 (Hermes, database integrity & cross-stage contract verification)
Branch: `test/phase2-db-integrity`
Worktree: `C:/c/tmp/nse-phase2`
Base: `origin/main` @ 77eb21b1

## Owned files (exhaustive — every file this branch adds)

Only untracked, NEW files. `git status --porcelain --untracked-files=no` on this
branch returns nothing: zero tracked production files are modified.

```
phase2/PHASE2_OWNERSHIP.md
phase2/phase2_db_reconciliation.md
phase2/phase2_db_reconciliation.json
phase2/phase2_integrity_findings.json
phase2/phase2_orphan_report.md
phase2/phase2_orphan_report.json
phase2/phase2_lineage_report.md
phase2/phase2_lineage_report.json
phase2/phase2_stage_matrix.md
phase2/phase2_constraint_audit.md
phase2/phase2_constraint_audit.json
phase2/storage_inventory.md
phase2/storage_inventory.json
scripts/diagnostics/phase2/storage_inventory.py
scripts/diagnostics/phase2/db_integrity_audit.py
scripts/diagnostics/phase2/orphan_scanner.py
scripts/diagnostics/phase2/constraint_audit.py
scripts/diagnostics/phase2/lineage_report.py
tests/contracts/__init__.py
tests/contracts/phase2/__init__.py
tests/contracts/phase2/conftest.py
tests/contracts/phase2/test_data_persistence.py
tests/contracts/phase2/test_model_persistence.py
tests/contracts/phase2/test_strategy_persistence.py
tests/contracts/phase2/test_backtest_persistence.py
tests/contracts/phase2/test_validation_persistence.py
tests/contracts/phase2/test_promotion_persistence.py
tests/contracts/phase2/test_provider_contracts.py
tests/contracts/phase2/test_lineage_temporal_contracts.py
tests/contracts/phase2/test_state_failure_replay_contracts.py
tests/contracts/phase2/test_api_db_contracts.py
```

## Module → Phase 2 letter coverage

| Phase 2 item | Artifact |
| --- | --- |
| 2A storage inventory | `scripts/diagnostics/phase2/storage_inventory.py` |
| 2B PostgreSQL integrity | `scripts/diagnostics/phase2/db_integrity_audit.py` |
| 2C SQLite integrity | `scripts/diagnostics/phase2/db_integrity_audit.py` |
| 2D PG/SQLite reconciliation | `phase2/phase2_db_reconciliation.{md,json}` |
| 2E write/read round trips | `test_data/model/strategy/backtest/validation/promotion_persistence.py` |
| 2F identity / lineage | `test_lineage_temporal_contracts.py` |
| 2G temporal contracts | `test_lineage_temporal_contracts.py` |
| 2H stage-to-stage | `test_lineage_temporal_contracts.py` |
| 2I state machine | `test_state_failure_replay_contracts.py` |
| 2J constraint audit | `scripts/diagnostics/phase2/constraint_audit.py` |
| 2K provider contract | `test_provider_contracts.py` |
| 2L silent fallback | `test_provider_contracts.py` |
| 2M cache / stale read | `test_provider_contracts.py` (read-path agreement) |
| 2N API contract | `test_api_db_contracts.py` |
| 2O failure propagation | `test_state_failure_replay_contracts.py` |
| 2P replay determinism | `test_state_failure_replay_contracts.py` |
| 2Q backtest/WF/OOS | `test_backtest_persistence.py` |
| 2R robustness/counterfactual | `test_backtest_persistence.py` |
| 2S validation/promotion | `test_validation_persistence.py`, `test_promotion_persistence.py` |
| 2T orphan scanner | `scripts/diagnostics/phase2/orphan_scanner.py` |
| 2U duplicate scanner | `scripts/diagnostics/phase2/orphan_scanner.py` |
| 2V three-path readback | `test_api_db_contracts.py` |
| 2W Chrome MCP UI | not applicable — no UI surface reaches a DB-backed lifecycle state from this harness |
| 2X ForexNexus | not applicable — not needed for the read-only verification path |

## Forbidden shared files (NOT modified by this branch)

Every file under `src/nexus_scalp/**`, in particular the Agent 1 / runtime-owned set:

```
src/nexus_scalp/application/**            (live engine, runtime mode)
src/nexus_scalp/adapters/**               (mt5/paper adapters, audit_repository)
src/nexus_scalp/database/**               (drivers, fabric, migration, provider)
src/nexus_scalp/execution/**              (lifecycle, order_manager, protection)
src/nexus_scalp/risk/**                   (risk engine)
src/nexus_scalp/research/**               (pipeline, observability, store)
src/nexus_scalp/model_lifecycle/**        (registry, store, orchestrator)
src/nexus_scalp/web/**                    (API routes)
src/nexus_scalp/settings/**               (settings service, secret store)
tests/unit/**, tests/integration/**       (existing suites)
tests/conftest.py                         (the repo's session isolation fixtures)
.github/workflows/**                      (CI config)
tests/critical_suite.txt, tests/fast_suite.txt
agents/taskboard.md, agents/locks.yaml
```

Zero edits, zero reformats, zero import changes to any of the above.

## Purpose

Independent verification + persistence-integrity layer for the NSE lifecycle:

1. Inventory every storage domain (SQLite files, PostgreSQL databases, settings,
   artifact stores) from source + runtime evidence, not documentation.
2. Read-only integrity audit of PostgreSQL and SQLite (schema, PKs, unique
   constraints, indexes, FKs, row counts, latest timestamps, orphans).
3. Cross-provider reconciliation with divergence classification.
4. Write/read round-trip contract tests against the real repository interfaces,
   on isolated SQLite files and an isolated throwaway PostgreSQL database.
5. Lineage, temporal, stage-to-stage, state-machine and failure-propagation
   contracts.
6. Provider routing + silent-fallback detection.
7. Orphan / duplicate / conflict scanners (read-only, report only).

## Repair scope

Defects are only ever fixed inside the owned files above (tests, diagnostics,
reports, documentation). A defect found in a production file is recorded as a
regression test + a finding in `phase2/phase2_lineage_report.md`, and left to
the owning agent.

## Test database safety

- SQLite writes go to `tmp_path` fixtures only. Never `artifacts/*.db`.
- PostgreSQL writes go to a disposable database on a throwaway server started
  by the diagnostics tooling (`pg_ctl` on port 55433, data dir under
  `$LOCALAPPDATA/Temp`), never the operator's live `postgresql-x64-17` cluster
  and never the live `nexusdb` / `nse_audit` databases.
- Read-only URI mode (`file:<db>?mode=ro`) is mandatory for all live DB reads.
- No `DROP DATABASE` / `TRUNCATE` / `DELETE` against any production database.

## Test results

`tests/contracts/phase2/` — 138 passed, 0 failed, 0 skipped (local, branch-clean).
